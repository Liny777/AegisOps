"""派发面规则闸：排队后策略被停用/删除 → 存量 queued 单不再起诊断，置 skipped 留痕。

背景：匹配面（list_enabled_rules）本来就是每批重读、改动即时生效，但队列里的存量单
过去只过白名单双保险，不看规则死活——用户点了「停用/删除」之后这些单照样起 run、占
诊断并发，最长要拖到 alert_queue_max_age_s（默认 1 天）排队超时才翻 failed。

闸的口径是「全灭才拦」：同 prompt 组多条规则命中时，只要还剩一条存活且启用就照常派发
（组内任一存活规则等价，与 dispatcher 取提示词的回落口径同源）。
"""
from __future__ import annotations

import time

import pytest

from alerts import matcher
from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, unwrap
from infra.external import alert_platform_mock

BASE = "/api/openops/v1/alerts"
PROMPT = "【专属排查指引】优先检查容器 OOM 与重启记录。"


@pytest.fixture(autouse=True)
def _clean_mock():
    alert_platform_mock._reset()
    yield
    alert_platform_mock._reset()


def _crid() -> str:
    return f"crid_{time.time_ns()}"


def _add_rule(client, iid: str, name: str, prompt: str = PROMPT) -> str:
    got = unwrap(client.post(f"{BASE}/rules", headers=USER_HEADERS,
                             json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                                   "name": name, "categories": ["Docker"], "prompt": prompt}))
    return got["rule"]["rule_id"]


def _queue_one(client, iid: str) -> dict:
    """注入一条命中告警并拉取入队；返回该实例的队首单。

    title 带纳秒后缀：fingerprint 是 source 级全局的，与别的用例撞了会走去重分支
    （计数仍对但语义变味），不如从源头岔开。
    """
    alert_platform_mock._inject(title=f"容器反复重启 {time.time_ns()}", category="Docker",
                                severity="critical", app_id="APP-A")
    counters = unwrap(client.post("/api/openops/v1/admin/alerts:pull",
                                  headers=ADMIN_HEADERS))["counters"]
    assert counters["queued"] == 1, counters
    return _incidents(client, iid)[0]


def _incidents(client, iid: str) -> list[dict]:
    return unwrap(client.get(f"{BASE}/incidents", headers=USER_HEADERS,
                             params={"instance_id": iid}))["items"]


def _dispatch(client) -> int:
    return unwrap(client.post("/api/openops/v1/admin/alerts:dispatch",
                              headers=ADMIN_HEADERS))["started"]


def test_disabled_rule_skips_queued_incident(client):
    """停用（enabled=false）→ 派发前拦下，skipped/rule_revoked 留痕，不起诊断。"""
    iid = create_instance(client, f"规则闸 停用 {time.time_ns()}")["instance_id"]
    rid = _add_rule(client, iid, f"Docker 全量接管 {time.time_ns()}")
    _queue_one(client, iid)

    unwrap(client.post(f"{BASE}/rules:batch", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "rule_ids": [rid],
                             "action": "disable"}))
    assert _dispatch(client) == 0

    inc = _incidents(client, iid)[0]
    assert inc["status"] == "skipped" and inc["state_reason"] == "rule_revoked"


def test_deleted_rule_skips_queued_incident(client):
    """软删（deleted_at）走 any_rule_live 的另一条 where 分支，效果同停用。"""
    iid = create_instance(client, f"规则闸 删除 {time.time_ns()}")["instance_id"]
    rid = _add_rule(client, iid, f"Docker 全量接管 {time.time_ns()}")
    _queue_one(client, iid)

    unwrap(client.post(f"{BASE}/rules/{rid}:delete", headers=USER_HEADERS,
                       json={"client_request_id": _crid()}))
    assert _dispatch(client) == 0

    inc = _incidents(client, iid)[0]
    assert inc["status"] == "skipped" and inc["state_reason"] == "rule_revoked"
    # 详情面板要能说清为什么（error.message 走后端 _SKIP_REASON_TEXT，不是裸 code）
    detail = unwrap(client.get(f"{BASE}/incidents/{inc['incident_id']}", headers=USER_HEADERS))
    assert detail["error"]["code"] == "rule_revoked" and "策略" in detail["error"]["message"]


def test_partially_disabled_group_still_dispatches(client):
    """全灭才拦：同 prompt 组两条规则只停用一条 → 仍派发（组内任一存活规则等价）。"""
    iid = create_instance(client, f"规则闸 部分停用 {time.time_ns()}")["instance_id"]
    rid_a = _add_rule(client, iid, f"Docker 接管 A {time.time_ns()}")
    _add_rule(client, iid, f"Docker 接管 B {time.time_ns()}")
    _queue_one(client, iid)
    assert len(_incidents(client, iid)) == 1, "同归一 prompt 的两条规则应合并为一单"

    unwrap(client.post(f"{BASE}/rules:batch", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "rule_ids": [rid_a],
                             "action": "disable"}))
    assert _dispatch(client) == 1
    assert _incidents(client, iid)[0]["status"] != "skipped"


def test_retry_blocked_while_rule_revoked(client):
    """:retry 前置守卫：不挡在这里，重试出来的单会被派发闸瞬间弹回 skipped，像按钮坏了。"""
    iid = create_instance(client, f"规则闸 重试 {time.time_ns()}")["instance_id"]
    rid = _add_rule(client, iid, f"Docker 全量接管 {time.time_ns()}")
    _queue_one(client, iid)
    unwrap(client.post(f"{BASE}/rules/{rid}:delete", headers=USER_HEADERS,
                       json={"client_request_id": _crid()}))
    assert _dispatch(client) == 0
    inc = _incidents(client, iid)[0]

    resp = client.post(f"{BASE}/incidents/{inc['incident_id']}:retry", headers=USER_HEADERS,
                       json={"client_request_id": _crid()})
    assert resp.status_code >= 400 and "策略" in resp.text
    assert _incidents(client, iid)[0]["status"] == "skipped", "被挡的重试不该改动单状态"


def test_rule_candidates_fail_open_on_legacy_row():
    """存量单两个规则字段皆空 → 候选 []，闸按 fail-open 放行（不误杀历史数据）。"""
    assert matcher.rule_candidates({}) == []
    assert matcher.rule_candidates({"alert_rule_id": None, "matched_rules_json": []}) == []
    # 组首优先 + matched 序回落 + 去重保序
    assert matcher.rule_candidates({
        "alert_rule_id": "r1",
        "matched_rules_json": [{"rule_id": "r1"}, {"rule_id": "r2"}, {}],
    }) == ["r1", "r2"]
