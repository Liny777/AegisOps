"""多规则命中同一告警的按 prompt 分单路由（2026-08-19 拍板）。

背景 bug：同实例两条规则（类型/级别相同、提示词不同）命中同一 Docker 告警时，
旧实现只建一单且只取 matched_rules[0] 的 prompt 派发——第二条提示词静默丢弃，
而且规则加载 SQL 无 ORDER BY，「哪条生效」随行序漂移。
新语义：按归一 prompt（空白≡系统默认）分组，每组各建一单各自诊断；归一相同合并一单。
"""
from __future__ import annotations

import os
import time

import psycopg
import pytest

from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, unwrap, wait_until
from infra.external import alert_platform_mock

BASE = "/api/openops/v1/alerts"

PROMPT_A = "【指引A】优先检查容器 OOM 与重启记录。"
PROMPT_B = "【指引B】优先核对镜像版本与最近部署变更。"


@pytest.fixture(autouse=True)
def _clean_mock():
    alert_platform_mock._reset()
    yield
    alert_platform_mock._reset()


def _crid() -> str:
    return f"crid_{time.time_ns()}"


def _pull(client) -> dict:
    return unwrap(client.post("/api/openops/v1/admin/alerts:pull", headers=ADMIN_HEADERS))["counters"]


def _dispatch(client) -> int:
    return unwrap(client.post("/api/openops/v1/admin/alerts:dispatch", headers=ADMIN_HEADERS))["started"]


def _set_cfg(client, key: str, value) -> None:
    unwrap(client.post("/api/openops/v1/admin/alerts/config:update", headers=ADMIN_HEADERS,
                       json={"client_request_id": _crid(), "updates": {key: value},
                             "reason": "test"}))


def _add_rule(client, iid: str, name: str, prompt: str) -> None:
    unwrap(client.post(f"{BASE}/rules", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "name": name, "categories": ["Docker"], "prompt": prompt}))


def _incidents(client, iid: str) -> list[dict]:
    return unwrap(client.get(f"{BASE}/incidents", headers=USER_HEADERS,
                             params={"instance_id": iid}))["items"]


def _db_rows(sql: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        return conn.execute(sql, params).fetchall()


def _db_exec(sql: str, params: tuple = ()) -> None:
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(sql, params)


def test_two_prompts_two_incidents_each_dispatched_with_own_prompt(client):
    """核心场景（测试同学的 A/B 对比）：双规则不同 prompt → 两单、各自诊断注入各自提示词。"""
    inst = create_instance(client, f"Docker 接管 {time.time_ns()}")
    iid = inst["instance_id"]
    _add_rule(client, iid, "Docker 接管 A", PROMPT_A)
    _add_rule(client, iid, "Docker 接管 B", PROMPT_B)

    alert_platform_mock._inject(title="Docker 容器重启", category="Docker", severity="critical",
                                app_id="APP-A")
    assert _pull(client)["queued"] == 2

    items = _incidents(client, iid)
    assert len(items) == 2
    assert {i["matched_rule"]["name"] for i in items} == {"Docker 接管 A", "Docker 接管 B"}
    pgs = _db_rows("select prompt_group from sre_alert_incident "
                   "where agent_team_instance_id=%s", (iid,))
    assert len({r[0] for r in pgs}) == 2 and all(r[0] for r in pgs)

    assert _dispatch(client) == 2

    def both_running():
        rows = _incidents(client, iid)
        return rows if all(r["status"] == "diagnosing" and r["run_id"] for r in rows) else None

    rows = wait_until(both_running, timeout=8, interval=0.1)
    assert rows, "两张姊妹单未全部进入 diagnosing+run 绑定"

    # 事件清单「接管策略」列：投影单（活跃优先）的组内规则透出，姊妹单并存时为其一
    ev = unwrap(client.get(f"{BASE}/events", headers=USER_HEADERS,
                           params={"instance_id": iid}))["items"][0]
    assert ev["matched_rule"]["name"] in {"Docker 接管 A", "Docker 接管 B"}
    assert ev["matched_rule_total"] == 1

    # 各 run 的诊断输入只含本组提示词（直读 sre_task_state 快照，同 test_dispatch_e2e 手法）
    by_rule = {r["matched_rule"]["name"]: r["run_id"] for r in rows}
    texts = {}
    for name, run_id in by_rule.items():
        row = _db_rows("select input_text from sre_task_state where run_id=%s "
                       "order by creation_date limit 1", (run_id,))
        assert row, f"{name} 的诊断任务快照缺失"
        texts[name] = row[0][0]
    assert PROMPT_A in texts["Docker 接管 A"] and PROMPT_B not in texts["Docker 接管 A"]
    assert PROMPT_B in texts["Docker 接管 B"] and PROMPT_A not in texts["Docker 接管 B"]
    for t in texts.values():
        assert "Docker 容器重启" in t  # 告警要素段照旧拼装


def test_same_normalized_prompt_merges_into_one_incident(client):
    """归一判等：空白 prompt ≡ 系统默认全文 → 同组合并一单，matched_rules_json 记全组。"""
    from alerts.rule_templates import DEFAULT_RULE_PROMPT

    inst = create_instance(client, f"Docker 接管 {time.time_ns()}")
    iid = inst["instance_id"]
    _add_rule(client, iid, "空提示词规则", "")
    _add_rule(client, iid, "默认全文规则", DEFAULT_RULE_PROMPT)

    alert_platform_mock._inject(title="Docker 健康检查失败", category="Docker",
                                severity="critical", app_id="APP-A")
    assert _pull(client)["queued"] == 1

    rows = _db_rows("select matched_rules_json, prompt_group from sre_alert_incident "
                    "where agent_team_instance_id=%s", (iid,))
    assert len(rows) == 1
    matched, pg = rows[0]
    assert len(matched) == 2 and pg
    assert {m["rule_name"] for m in matched} == {"空提示词规则", "默认全文规则"}


def test_group_first_rule_deleted_falls_back_to_surviving_rule_prompt(client):
    """组首规则被软删后派发不得静默回落默认提示词（2026-08-19 审查实证的破洞）：
    按 matched_rules_json 序回落组内下一条存活的同 prompt 规则。"""
    inst = create_instance(client, f"Docker 接管 {time.time_ns()}")
    iid = inst["instance_id"]
    _add_rule(client, iid, "同提示词甲", PROMPT_A)
    _add_rule(client, iid, "同提示词乙", PROMPT_A)  # 归一相同 → 合并一单，组首=甲

    alert_platform_mock._inject(title="Docker 容器重启", category="Docker", severity="critical",
                                app_id="APP-A")
    assert _pull(client)["queued"] == 1

    # 排队期间删除组首规则（合并语义正会引导用户清理冗余规则）
    rules = unwrap(client.get(f"{BASE}/rules", headers=USER_HEADERS,
                              params={"instance_id": iid}))["rules"]
    first_id = next(r["rule_id"] for r in rules if r["name"] == "同提示词甲")
    unwrap(client.post(f"{BASE}/rules/{first_id}:delete", headers=USER_HEADERS,
                       json={"client_request_id": _crid()}))

    assert _dispatch(client) == 1

    def has_run():
        rows = _incidents(client, iid)
        return rows[0] if rows and rows[0]["status"] == "diagnosing" and rows[0]["run_id"] else None

    inc = wait_until(has_run, timeout=8, interval=0.1)
    assert inc, "删组首规则后诊断未启动"
    row = _db_rows("select input_text from sre_task_state where run_id=%s "
                   "order by creation_date limit 1", (inc["run_id"],))
    assert row and PROMPT_A in row[0][0], "组首被删后应回落组内存活规则的提示词，而非默认五步法"


def test_cooldown_isolates_prompt_groups(client):
    """组冷却按组隔离的正向盯防（mutation 审查：把 pg 过滤改恒真时 163 例全绿——本例补上）：
    A 组刚完结在冷却窗内、B 组完结已超窗 → 新告警只有 A 组被冷却拦截，B 组正常重建。"""
    inst = create_instance(client, f"Docker 接管 {time.time_ns()}")
    iid = inst["instance_id"]
    _add_rule(client, iid, "Docker 接管 A", PROMPT_A)
    _add_rule(client, iid, "Docker 接管 B", PROMPT_B)

    alert_platform_mock._inject(title="Docker 容器重启", category="Docker", severity="critical",
                                app_id="APP-A", fingerprint="fp_cd_1")
    assert _pull(client)["queued"] == 2
    by_name = {i["matched_rule"]["name"]: i["incident_id"] for i in _incidents(client, iid)}
    _db_exec("update sre_alert_incident set incident_state='completed', ended_at=now() "
             "where alert_incident_id=%s", (by_name["Docker 接管 A"],))
    _db_exec("update sre_alert_incident set incident_state='completed', "
             "ended_at=now() - interval '20 minutes' where alert_incident_id=%s",
             (by_name["Docker 接管 B"],))  # 默认冷却窗 900s，20min 已出窗

    alert_platform_mock._inject(title="Docker 容器重启", category="Docker", severity="critical",
                                app_id="APP-A", fingerprint="fp_cd_2")
    counters = _pull(client)
    assert counters["cooldown"] == 1 and counters["queued"] == 1
    queued = [i for i in _incidents(client, iid) if i["status"] == "queued"]
    assert len(queued) == 1 and queued[0]["matched_rule"]["name"] == "Docker 接管 B"


def test_legacy_null_prompt_group_attach_then_cooldown_all_groups(client):
    """过渡兼容：存量无组标（prompt_group IS NULL）的未完结单按旧语义整体附着，
    不为新组建单；其完结后冷却对所有组生效。"""
    inst = create_instance(client, f"Docker 接管 {time.time_ns()}")
    iid = inst["instance_id"]
    _add_rule(client, iid, "Docker 接管 A", PROMPT_A)

    alert_platform_mock._inject(title="Docker 容器重启", category="Docker", severity="critical",
                                app_id="APP-A", fingerprint="fp_legacy_1")
    assert _pull(client)["queued"] == 1
    _db_exec("update sre_alert_incident set prompt_group=null "
             "where agent_team_instance_id=%s", (iid,))

    # 部署后新增第二条不同 prompt 的规则：旧单未完结期间不分裂建单，整体附着一次
    _add_rule(client, iid, "Docker 接管 B", PROMPT_B)
    alert_platform_mock._inject(title="Docker 容器重启", category="Docker", severity="critical",
                                app_id="APP-A", fingerprint="fp_legacy_2")
    counters = _pull(client)
    assert counters["attached"] == 1 and counters["queued"] == 0
    assert len(_incidents(client, iid)) == 1

    # 护栏盯防（mutation 审查：删掉 (iid,None)=「全组已接」判定时旧套件仍全绿——本步补上）：
    # 去重窗口内同指纹重发只 bump 计数，不得再次整体附着（attached 与 alert_count 双虚涨）
    alert_platform_mock._inject(title="Docker 容器重启", category="Docker", severity="critical",
                                app_id="APP-A", fingerprint="fp_legacy_2")
    counters = _pull(client)
    assert counters["deduped"] == 1 and counters["attached"] == 0 and counters["queued"] == 0
    items = _incidents(client, iid)
    assert len(items) == 1 and items[0]["alert_count"] == 3  # 建单1+附着1+bump1，无重复附着

    # 旧单完结 → NULL 通配冷却：两个组都不建新单
    _db_exec("update sre_alert_incident set incident_state='completed', ended_at=now() "
             "where agent_team_instance_id=%s", (iid,))
    alert_platform_mock._inject(title="Docker 容器重启", category="Docker", severity="critical",
                                app_id="APP-A", fingerprint="fp_legacy_3")
    counters = _pull(client)
    assert counters["cooldown"] == 2 and counters["queued"] == 0
    assert len(_incidents(client, iid)) == 1

    # 旧单出冷却窗后，**已接过的事件**（fp_legacy_2 曾附着到旧单）窗口内重发也不补建——
    # (iid,None) 即「全组已接」，不因组语义上线而对存量实例重复建全套组单（宁漏勿双建）。
    # 注意不能用 fp_legacy_3：它被冷却拦截时从未 link 过单，按事件语义出窗后补建是既有行为。
    _db_exec("update sre_alert_incident set ended_at=now() - interval '20 minutes' "
             "where agent_team_instance_id=%s", (iid,))
    alert_platform_mock._inject(title="Docker 容器重启", category="Docker", severity="critical",
                                app_id="APP-A", fingerprint="fp_legacy_2")
    counters = _pull(client)
    assert counters["deduped"] == 1 and counters["queued"] == 0 and counters["attached"] == 0
    assert len(_incidents(client, iid)) == 1


def test_instance_cap_binds_across_prompt_groups(client):
    """⑤ 层实例上限是不分组总量闸：双 prompt 组抢 1 个名额 → 一单入队一单留痕。"""
    inst = create_instance(client, f"Docker 接管 {time.time_ns()}")
    iid = inst["instance_id"]
    _add_rule(client, iid, "Docker 接管 A", PROMPT_A)
    _add_rule(client, iid, "Docker 接管 B", PROMPT_B)

    _set_cfg(client, "alert_max_open_incidents_per_instance", 1)
    alert_platform_mock._inject(title="Docker 资源超限", category="Docker", severity="critical",
                                app_id="APP-A")
    counters = _pull(client)
    _set_cfg(client, "alert_max_open_incidents_per_instance", 10)  # 复位默认，别污染后续用例
    assert counters["queued"] == 1 and counters["skipped"] == 1

    items = _incidents(client, iid)
    by_state = {i["status"]: i for i in items}
    assert set(by_state) == {"queued", "skipped"}
    assert by_state["skipped"]["state_reason"] == "overflow_instance"
    # 留痕单归属另一个 prompt 组：retry 时按各自 alert_rule_id 注入各自提示词
    assert by_state["queued"]["matched_rule"]["name"] != by_state["skipped"]["matched_rule"]["name"]


def test_window_dedup_backfills_late_prompt_group(client):
    """滚动去重窗口内补路由组级化（用户核心场景：持续触发的告警上做 prompt A/B）：
    后建的第二条不同 prompt 规则在窗口内也能接上；已接过的组不重复建单。"""
    inst = create_instance(client, f"Docker 接管 {time.time_ns()}")
    iid = inst["instance_id"]
    _add_rule(client, iid, "Docker 接管 A", PROMPT_A)

    alert_platform_mock._inject(title="Docker 容器重启", category="Docker", severity="critical",
                                app_id="APP-A", fingerprint="fp_ab_1")
    assert _pull(client)["queued"] == 1

    _add_rule(client, iid, "Docker 接管 B", PROMPT_B)
    alert_platform_mock._inject(title="Docker 容器重启", category="Docker", severity="critical",
                                app_id="APP-A", fingerprint="fp_ab_1")
    counters = _pull(client)
    assert counters["deduped"] == 1 and counters["queued"] == 1  # B 组补建，A 组仅 bump

    alert_platform_mock._inject(title="Docker 容器重启", category="Docker", severity="critical",
                                app_id="APP-A", fingerprint="fp_ab_1")
    counters = _pull(client)
    assert counters["deduped"] == 1 and counters["queued"] == 0  # 两组都接过：只合并计数
    assert len(_incidents(client, iid)) == 2
