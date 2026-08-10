"""派发器端到端链（mock 客户端 + mock runtime）：

inject → :pull(queued) → :dispatch(diagnosing+建 run) → ASK 审批 → 收割 completed。
另测：alert 池绕开 user 并发闸（且反向不挤占）、entry_source 会话历史过滤、
重启收敛（TestClient 关开两次 = 一次真实重启）。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, create_run, unwrap, wait_until
from infra.external import alert_platform_mock

BASE = "/api/openops/v1/alerts"


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


def _setup_instance(client,
                    prompt: str = "【专属排查指引】优先检查主从复制线程与 binlog 位点。") -> str:
    inst = create_instance(client, f"告警接管 Agent {time.time_ns()}")
    iid = inst["instance_id"]
    unwrap(client.post(f"{BASE}/subscription:update", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "enabled": True}))
    unwrap(client.post(f"{BASE}/rules", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "name": "MySQL 全量接管", "categories": ["MySQL"],
                             "prompt": prompt}))
    return iid


def _first_incident(client, iid: str) -> dict | None:
    items = unwrap(client.get(f"{BASE}/incidents", headers=USER_HEADERS,
                              params={"instance_id": iid}))["items"]
    return items[0] if items else None


def _wait_incident(client, iid: str, status: str, timeout: float = 8.0) -> dict:
    def probe():
        inc = _first_incident(client, iid)
        return inc if inc and inc["status"] == status else None

    inc = wait_until(probe, timeout=timeout, interval=0.1)
    assert inc, f"incident 未到达 {status}"
    return inc


def _wait_diagnosing_with_run(client, iid: str, timeout: float = 8.0) -> dict:
    """诊断态与 run 绑定之间有异步窗口（抢占先翻状态、worker 随后建 run）——合并探测。"""
    def probe():
        inc = _first_incident(client, iid)
        return inc if inc and inc["status"] == "diagnosing" and inc["run_id"] else None

    inc = wait_until(probe, timeout=timeout, interval=0.1)
    assert inc, "incident 未到达 diagnosing+run 绑定"
    return inc


def _drive_to_completed(client, run_id: str, timeout: float = 15.0) -> None:
    """mock 编排器的 ASK 停点：轮询批准全部 pending 审批直到任务完成。"""
    def probe():
        st = unwrap(client.get(f"/api/openops/v1/agent-runs/{run_id}/state", headers=USER_HEADERS))
        if (st.get("active_task") or {}).get("status") == "completed":
            return True
        for ap in unwrap(client.get(f"/api/openops/v1/agent-runs/{run_id}/approvals",
                                    headers=USER_HEADERS)):
            if ap.get("decision") == "pending":
                client.post(f"/api/openops/v1/approvals/{ap['approval_request_id']}:decide",
                            headers=USER_HEADERS,
                            json={"client_request_id": _crid(), "decision": "approved"})
        return None

    assert wait_until(probe, timeout=timeout, interval=0.1), "诊断任务未完成"


def test_full_diagnosis_chain(client):
    iid = _setup_instance(client)
    alert_platform_mock._inject(title="MySQL 主库延迟>5s", category="MySQL", severity="fatal",
                                app_id="APP-A", labels={"service": "pay-core"},
                                description="主从延迟 6.8s，持续 3 分钟")
    assert _pull(client)["queued"] == 1
    assert _dispatch(client) == 1

    inc = _wait_diagnosing_with_run(client, iid)
    run_id = inc["run_id"]

    # entry_source='alert'：会话历史不出现，但 /state 直开可读（决策 6）
    conv_ids = [r["agent_run_id"] for r in unwrap(client.get("/api/openops/v1/agent-runs",
                                                             headers=USER_HEADERS))]
    assert run_id not in conv_ids
    assert unwrap(client.get(f"/api/openops/v1/agent-runs/{run_id}/state", headers=USER_HEADERS))

    _drive_to_completed(client, run_id)
    done = _wait_incident(client, iid, "completed", timeout=10)
    assert done["result_summary"], "收割的诊断摘要为空"
    assert done["run_id"] == run_id

    detail = unwrap(client.get(f"{BASE}/incidents/{done['incident_id']}", headers=USER_HEADERS))
    assert detail["timeline"][-1]["event"].startswith("诊断完成")
    assert detail["alerts_total"] == 1

    # 规则自定义提示词注入：诊断任务输入快照携带专属指引
    # （mock runtime 不落 AgentState/transcript，直读 sre_task_state 快照——同步连接与 test_ddl_008 同法）
    import os

    import psycopg

    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        row = conn.execute(
            "select input_text from sre_task_state where run_id=%s "
            "order by creation_date limit 1", (run_id,)).fetchone()
    assert row and "【专属排查指引】优先检查主从复制线程" in row[0]
    assert "MySQL 主库延迟>5s" in row[0]  # 告警要素段照旧拼装

    # 完成后可评价；summary 的 attention 归零
    fb = unwrap(client.post(f"{BASE}/incidents/{done['incident_id']}:feedback",
                            headers=USER_HEADERS,
                            json={"client_request_id": _crid(), "feedback": "positive",
                                  "note": "诊断准确"}))
    assert fb["user_feedback"] == "positive"
    s = unwrap(client.get(f"{BASE}/summary", headers=USER_HEADERS, params={"instance_id": iid}))
    assert s["attention"] == 0 and s["completed_24h"] == 1

    # 事件清单（全量告警视角）：已完成 + 本人可点会话 + 评价随行
    ev = unwrap(client.get(f"{BASE}/events", headers=USER_HEADERS,
                           params={"instance_id": iid}))["items"][0]
    assert ev["takeover_status"] == "done" and ev["alert_status"] == "assigned"
    assert ev["run_clickable"] is True and ev["run_id"] == run_id
    assert ev["user_feedback"] == "positive"

    # 共享告警沙箱（决策⑥⑦）：诊断建号在 sys-alert-sandbox；追问 lazy ensure 用户容器（双账并存）
    from sandbox.executor import ALERT_SANDBOX_UID
    from sandbox.executor import executor as sandbox_executor

    alert_c = sandbox_executor._by_user.get(ALERT_SANDBOX_UID)
    assert alert_c is not None and run_id in alert_c.active_run_ids
    assert "0026demo01" not in sandbox_executor._by_user  # 纯诊断阶段不建用户容器

    followup = unwrap(client.post(f"/api/openops/v1/agent-runs/{run_id}/tasks",
                                  headers=USER_HEADERS,
                                  json={"client_request_id": _crid(),
                                        "input_text": "追问：给出回滚步骤"}))
    assert followup["status"] == "running"
    user_c = sandbox_executor._by_user.get("0026demo01")
    assert user_c is not None and run_id in user_c.active_run_ids  # 追问切回用户容器
    assert run_id in alert_c.active_run_ids  # 告警容器账目不动（双账并存）
    from runtime import task_registry

    st_now = task_registry.get_by_run(run_id)
    assert st_now is not None and st_now.origin == "user" and st_now.sandbox_uid == "0026demo01"

    # 双键幂等释放（难点①守卫）：close 后两本账的 run_id 均归零
    unwrap(client.post(f"/api/openops/v1/agent-runs/{run_id}:close", headers=USER_HEADERS,
                       json={"client_request_id": _crid()}))
    assert run_id not in alert_c.active_run_ids
    assert run_id not in user_c.active_run_ids


def test_rule_prompt_slash_skill_sets_hint(client):
    """规则提示词句中 /inspection（seed 平台 skill）→ 派发任务 skill_hint 生效。

    链路：dispatcher._skill_hint_from_prompt 词法提取 → _AlertTaskReq.skill_hint →
    start_task 的 resolve_skill_alias 按属主 available_skills 解析为 canonical key。
    """
    iid = _setup_instance(client, prompt="优先 /inspection 巡检定界，再按五步法输出结论。")
    alert_platform_mock._inject(title="MySQL 连接飙升", category="MySQL", severity="critical")
    assert _pull(client)["queued"] == 1
    assert _dispatch(client) == 1
    inc = _wait_diagnosing_with_run(client, iid)

    from runtime import task_registry

    st = task_registry.get_by_run(inc["run_id"])
    assert st is not None and st.origin == "alert"
    assert st.skill_hint == "inspection", f"skill_hint 未生效：{st.skill_hint!r}"


def test_rule_prompt_unknown_slash_token_ignored(client):
    """/token 不在 available_skills → fail-safe 忽略（skill_hint None，诊断照常启动）。"""
    iid = _setup_instance(client, prompt="/no-such-skill 先看这个，再按五步法。")
    alert_platform_mock._inject(title="MySQL 磁盘满", category="MySQL", severity="critical")
    assert _pull(client)["queued"] == 1
    assert _dispatch(client) == 1
    inc = _wait_diagnosing_with_run(client, iid)

    from runtime import task_registry

    st = task_registry.get_by_run(inc["run_id"])
    assert st is not None and st.skill_hint is None


def test_alert_pool_bypasses_user_gate_both_ways(client):
    """交互闸只拦交互、不拦告警；反向告警在跑也不吃交互额度。

    2026-07-31 起交互侧超限**排队而非 429**（详见 tests/test_interactive_queue.py），
    所以"被交互闸挡住"的证据从 `429` 变成 `status=="queued"` —— 判据变了，隔离语义没变。
    """
    from app import interactive_queue

    iid = _setup_instance(client)
    # 占满 user 池：两个交互任务停在 ASK（running）
    runs = [create_run(client, iid) for _ in range(2)]
    for r in runs:
        unwrap(client.post(f"/api/openops/v1/agent-runs/{r['agent_run_id']}/tasks",
                           headers=USER_HEADERS,
                           json={"client_request_id": _crid(), "input_text": "请恢复 APP-A"}))
    r3 = create_run(client, iid)
    blocked = unwrap(client.post(f"/api/openops/v1/agent-runs/{r3['agent_run_id']}/tasks",
                                 headers=USER_HEADERS,
                                 json={"client_request_id": _crid(), "input_text": "第三个交互任务"}))
    assert blocked["status"] == "queued", "user 池仍然只有 2 个额度，第三个应排队"
    queued_task = blocked["task_id"]

    # user 池满不挡告警派发（alert 池闸门独立）
    alert_platform_mock._inject(title="MySQL 连接数超限", category="MySQL", severity="critical")
    assert _pull(client)["queued"] == 1
    assert _dispatch(client) == 1
    inc = _wait_diagnosing_with_run(client, iid)
    assert inc["run_id"]
    assert interactive_queue.position(queued_task) == 1, "告警派发不应挤动交互队列"

    # 反向：告警诊断在跑不占用户额度——批掉交互任务后，排队的第三个自动出队启动
    for r in runs:
        for ap in unwrap(client.get(f"/api/openops/v1/agent-runs/{r['agent_run_id']}/approvals",
                                    headers=USER_HEADERS)):
            if ap.get("decision") == "pending":
                client.post(f"/api/openops/v1/approvals/{ap['approval_request_id']}:decide",
                            headers=USER_HEADERS,
                            json={"client_request_id": _crid(), "decision": "approved"})
    assert wait_until(lambda: interactive_queue.position(queued_task) is None,
                      timeout=10, interval=0.2), "告警 run 在跑时，交互额度未按分池释放"
    state = wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{r3['agent_run_id']}/state",
                                  headers=USER_HEADERS)).get("active_task"),
        timeout=10, interval=0.2)
    assert state and state["task_id"] == queued_task, "出队后应复用原 task_id 真正启动"


def test_converge_requeues_interrupted_diagnosis():
    """真实重启语义：TestClient 关（worker 取消，单停 diagnosing）→ 开（core converge 打断
    task → alerts converge 预算内 requeue，复用同一 run）。"""
    import asyncio as _aio

    from alerts import repository as _grant_repo
    from app import asset_reconcile_service, asset_registry_service, scope_service
    from conftest import reset_database
    from infra import idempotency
    from infra.external import omodel_mock
    from main import app
    from runtime import events, task_registry
    from sandbox.executor import executor as sandbox_executor

    def _reset_process_state():
        events.reset()
        task_registry.reset()
        idempotency.clear()
        scope_service._reset_cache()
        omodel_mock._reset()
        asset_reconcile_service._reset()
        asset_registry_service._reset_user_skill_sync()
        sandbox_executor._by_user.clear()

    reset_database()
    _reset_process_state()
    iid = None
    try:
        with TestClient(app) as c1:
            # 本用例不走 client fixture（自管 TestClient 生命周期），autouse 的白名单
            # 补种不生效——手动给测试用户开通（与 conftest._grant_alert_takeover 同口径）
            _aio.run(_grant_repo.set_user_grant("0026demo01", True, "test"))
            iid = _setup_instance(c1)
            alert_platform_mock._inject(title="重启前的告警", category="MySQL", severity="critical")
            assert _pull(c1)["queued"] == 1
            assert _dispatch(c1) == 1
            inc = _wait_diagnosing_with_run(c1, iid)
            run_id_before = inc["run_id"]
        # 关停：worker 被取消，incident 停在 diagnosing；孤儿 task 由下次启动收敛
        _reset_process_state()
        with TestClient(app) as c2:
            items = unwrap(c2.get(f"{BASE}/incidents", headers=USER_HEADERS,
                                  params={"instance_id": iid}))["items"]
            assert items, "重启后 incident 丢失"
            assert items[0]["status"] == "queued", f"未收敛回队列：{items[0]['status']}"
            assert items[0]["retry_count"] == 1
            assert items[0]["run_id"] == run_id_before  # 复用同一 run（AgentState 记忆延续）
    finally:
        _reset_process_state()
        alert_platform_mock._reset()
