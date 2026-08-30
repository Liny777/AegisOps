"""四号校验（29.14）：标注互斥/配置校验 → 运行时握手 → decide 回写 → Gateway 凭证注入。

E2E 走 mock 编排器（与真 runtime 共用 _handle_flow_check 握手实现）；
凭证铁律断言：token/flow_code 不出现在任何事件/审计 payload。
"""
from __future__ import annotations

import json
import time

import pytest

from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, create_run, unwrap, wait_until

TENANT = "88888888888888888888888888888888"  # apptree_client._DEFAULT_ENTERPRISE（无 env 时的当前租户）

FLOW_CONFIG = {
    "init_path": "/rca/web/service/risk/control/orc/initialization",
    "verify_path": "/rca/web/service/risk/control/orc/flow-number-check",
    "invoking_method": "redisAgent.hotKeyAnalysis",
    "service_id_by_tenant": {TENANT: "svc-tenant-a"},
    "object_arg_path": "$.appid",
}


def _recover_catalog_id(client) -> str:
    catalog = unwrap(client.get("/api/openops/v1/admin/mcp-tools", headers=ADMIN_HEADERS))
    return next(t for t in catalog if t["tool_name"] == "recover_execute")["tool_catalog_id"]


def _annotate_recover_flow_check(client, config: dict | None = None) -> str:
    tcid = _recover_catalog_id(client)
    unwrap(client.put(
        f"/api/openops/v1/admin/mcp-tools/{tcid}/annotation",
        headers=ADMIN_HEADERS,
        json={"is_approval_required": False, "is_flow_check_required": True,
              "flow_check_config": config or FLOW_CONFIG,
              "is_secret_required": False, "scope_mode": "required",
              "appid_arg_path": "$.appid", "status": "allowed"},
    ))
    return tcid


def _start_and_wait_flow_check(client):
    instance = create_instance(client, f"FlowCheck Agent {time.time_ns()}")
    run = create_run(client, instance["instance_id"])
    task = unwrap(client.post(
        f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks",
        headers=USER_HEADERS,
        json={"client_request_id": f"task_{time.time_ns()}", "input_text": "请恢复 APP-A"},
    ))
    pending = wait_until(
        lambda: unwrap(client.get(
            f"/api/openops/v1/agent-runs/{run['agent_run_id']}/flow-checks", headers=USER_HEADERS)),
    )
    assert pending
    return run, task, pending[0]


# ---- 标注层：互斥 + 配置校验 ----

def test_fc_001_annotation_mutual_exclusion_rejected(client):
    tcid = _recover_catalog_id(client)
    r = client.put(
        f"/api/openops/v1/admin/mcp-tools/{tcid}/annotation",
        headers=ADMIN_HEADERS,
        json={"is_approval_required": True, "is_flow_check_required": True,
              "flow_check_config": FLOW_CONFIG, "scope_mode": "none", "status": "allowed"},
    )
    assert r.status_code == 400
    assert "不可同时" in r.text


@pytest.mark.parametrize("mutation, expect", [
    ({"init_path": ""}, "init_path"),
    ({"init_path": "https://a.b/init"}, "init_path"),           # 含域名拒绝
    ({"verify_path": "no-slash"}, "verify_path"),
    ({"invoking_method": " "}, "invoking_method"),
    ({"service_id_by_tenant": {}}, "service_id"),
    ({"object_arg_path": "target.appid"}, "object_arg_path"),   # 非 $ 开头拒绝
])
def test_fc_002_annotation_config_validation(client, mutation, expect):
    tcid = _recover_catalog_id(client)
    bad = {**FLOW_CONFIG, **mutation}
    r = client.put(
        f"/api/openops/v1/admin/mcp-tools/{tcid}/annotation",
        headers=ADMIN_HEADERS,
        json={"is_approval_required": False, "is_flow_check_required": True,
              "flow_check_config": bad, "scope_mode": "none", "status": "allowed"},
    )
    assert r.status_code == 400, r.text
    assert expect in r.text


def test_fc_003_annotation_roundtrip_in_catalog(client):
    tcid = _annotate_recover_flow_check(client)
    catalog = unwrap(client.get("/api/openops/v1/admin/mcp-tools", headers=ADMIN_HEADERS))
    row = next(t for t in catalog if t["tool_catalog_id"] == tcid)
    assert row["is_flow_check_required"] is True
    assert row["is_approval_required"] is False
    assert row["flow_check_config"]["service_id_by_tenant"] == {TENANT: "svc-tenant-a"}
    assert row["flow_check_config"]["object_arg_path"] == "$.appid"


# ---- E2E：required → decide → Gateway 注入（mock 编排器；握手实现与真 runtime 共用）----

def test_fc_010_approved_executes_tool_with_credential_headers(client):
    from infra.external import http_mcp_client

    _annotate_recover_flow_check(client)
    run, _task, fc = _start_and_wait_flow_check(client)
    rid = run["agent_run_id"]

    # 配置快照随行/事件下发（前端 SDK 初始化参数），operator 回退 user_id（mock 登录无 IAM）
    cfg = fc["flow_check_config_json"]
    assert cfg["init_path"] == FLOW_CONFIG["init_path"]
    assert cfg["verify_path"] == FLOW_CONFIG["verify_path"]
    assert cfg["service_id"] == "svc-tenant-a"       # 按当前租户解析后的单值
    assert cfg["invoking_method"] == FLOW_CONFIG["invoking_method"]
    assert cfg["enterprise_id"] == TENANT
    assert cfg["operator"] == "0026demo01"
    assert cfg["target_object"] == {"value": "APP-A", "path": "$.appid"}

    token, flow_code = "tok-flow-check-9x8y", "FC20260830001"
    decided = unwrap(client.post(
        f"/api/openops/v1/flow-checks/{fc['flow_check_request_id']}:decide",
        headers=USER_HEADERS,
        json={"client_request_id": "fc_ok", "decision": "approved",
              "token": token, "flow_code": flow_code},
    ))
    assert decided["decision"] == "approved"

    assert wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/state",
                                  headers=USER_HEADERS))["active_task"]["status"] == "completed",
    ) is True

    # Gateway 在调用边界注入凭证 header（唯一出口）
    last = http_mcp_client.last_call
    assert last is not None and last["tool"] == "recover_execute"
    assert last["headers"]["X-OpenOps-Flow-Check-Token"] == token
    assert last["headers"]["X-OpenOps-Flow-Check-Code"] == flow_code

    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{rid}", headers=USER_HEADERS))
    types = {e["event_type"] for e in events}
    assert "openops.flow_check.required" in types
    assert "flow_check.approved" in types
    assert any(e["external_request_id"] for e in events if e["action"] == "recover_execute")
    # required 事件 payload 带前端 SDK 初始化所需标量
    req_ev = next(e for e in events if e["event_type"] == "openops.flow_check.required")
    p = req_ev["payload_redacted_json"]
    assert p["init_path"] == FLOW_CONFIG["init_path"]
    assert p["service_id"] == "svc-tenant-a"
    assert p["invoking_method"] == FLOW_CONFIG["invoking_method"]
    assert p["operator"] == "0026demo01"
    # 凭证铁律：token / flow_code 不进任何事件/审计 payload
    dump = json.dumps(events, ensure_ascii=False)
    assert token not in dump
    assert flow_code not in dump
    # /state 的 pending 快照也已收口
    assert unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/flow-checks", headers=USER_HEADERS)) == []


def test_fc_010b_approved_flow_works_on_both_runtimes(client, runtime_backend):
    """双 runtime 同一握手链（mock 直调 / agentscope 经 PermissionEngine ASK 分流）。"""
    from infra.external import http_mcp_client

    _annotate_recover_flow_check(client)
    run, _task, fc = _start_and_wait_flow_check(client)
    rid = run["agent_run_id"]
    unwrap(client.post(
        f"/api/openops/v1/flow-checks/{fc['flow_check_request_id']}:decide",
        headers=USER_HEADERS,
        json={"client_request_id": "fc_rt", "decision": "approved",
              "token": "tok-rt", "flow_code": "FC-rt"},
    ))
    assert wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/state",
                                  headers=USER_HEADERS))["active_task"]["status"] == "completed",
        timeout=15.0, interval=0.1,
    ) is True
    last = http_mcp_client.last_call
    assert last is not None and last["tool"] == "recover_execute"
    assert last["headers"]["X-OpenOps-Flow-Check-Token"] == "tok-rt"
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{rid}", headers=USER_HEADERS))
    assert any(e["event_type"] == "openops.flow_check.required" for e in events)
    assert any(e["external_request_id"] for e in events if e["action"] == "recover_execute")


def test_fc_011_rejected_does_not_call_tool(client):
    _annotate_recover_flow_check(client)
    run, _task, fc = _start_and_wait_flow_check(client)
    rid = run["agent_run_id"]
    unwrap(client.post(
        f"/api/openops/v1/flow-checks/{fc['flow_check_request_id']}:decide",
        headers=USER_HEADERS,
        json={"client_request_id": "fc_no", "decision": "rejected"},
    ))
    wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/state",
                                  headers=USER_HEADERS))["active_task"]["status"] == "completed",
    )
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{rid}", headers=USER_HEADERS))
    assert any(e["event_type"] == "flow_check.rejected" for e in events)
    assert not any(e["action"] == "recover_execute" and e["external_request_id"] for e in events)


def test_fc_012a_token_with_control_chars_rejected(client):
    """含 CR/LF 的凭证在入口 400——不给 h11 组头抛错、凭证原文进日志的机会。"""
    _annotate_recover_flow_check(client)
    _run, _task, fc = _start_and_wait_flow_check(client)
    for bad_token, bad_code in [("tok\r\nX-Evil: 1", "FC-1"), ("tok-ok", "FC\n1"), ("t" * 5000, "FC-1")]:
        r = client.post(
            f"/api/openops/v1/flow-checks/{fc['flow_check_request_id']}:decide",
            headers=USER_HEADERS,
            json={"client_request_id": "fc_bad", "decision": "approved",
                  "token": bad_token, "flow_code": bad_code},
        )
        assert r.status_code == 400, (bad_token[:20], r.text)


def test_fc_016_decide_race_loser_follows_db_terminal_state(client, monkeypatch):
    """并发决策竞态：条件更新 0 行的一方以 DB 终态收口，不置握手、不写凭证。"""
    from infra.repositories import runs as runs_repo

    _annotate_recover_flow_check(client)
    run, task, fc = _start_and_wait_flow_check(client)
    orig = runs_repo.decide_flow_check

    async def racing(fid, decision, by):
        await orig(fid, "rejected", "race-opponent")  # 对手先落子（未经服务层，无事件无握手）
        return await orig(fid, decision, by)          # 行已非 pending → 0 行

    monkeypatch.setattr(runs_repo, "decide_flow_check", racing)
    out = unwrap(client.post(
        f"/api/openops/v1/flow-checks/{fc['flow_check_request_id']}:decide",
        headers=USER_HEADERS,
        json={"client_request_id": "fc_race", "decision": "approved",
              "token": "tok-race", "flow_code": "FC-race"},
    ))
    assert out["decision"] == "rejected"  # 以 DB 终态为准，不谎报 approved
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    assert not any(e["event_type"] == "flow_check.approved" for e in events)
    assert not any(e["action"] == "recover_execute" and e["external_request_id"] for e in events)
    monkeypatch.setattr(runs_repo, "decide_flow_check", orig)
    unwrap(client.post(f"/api/openops/v1/tasks/{task['task_id']}:cancel", headers=USER_HEADERS, json={}))


def test_fc_017_expire_at_ceils_to_waiter_window(client, monkeypatch):
    """行内过期 ≥ 等待窗口（向上取整）：非 60 整倍配置不再被变相缩短。"""
    import asyncio as _asyncio

    from infra.db import q_one
    from runtime import agentscope_runtime as rt

    monkeypatch.setattr(rt, "FLOW_CHECK_TIMEOUT_S", 90.0)
    _annotate_recover_flow_check(client)
    run, task, fc = _start_and_wait_flow_check(client)
    row = _asyncio.run(q_one(
        "select extract(epoch from (expire_at - creation_date)) secs from sre_flow_check_request "
        "where flow_check_request_id=%(f)s", {"f": fc["flow_check_request_id"]}))
    assert row is not None and float(row["secs"]) >= 90.0  # ceil(90/60)=2 分钟
    unwrap(client.post(f"/api/openops/v1/tasks/{task['task_id']}:cancel", headers=USER_HEADERS, json={}))


def test_fc_012_approved_without_token_rejected_as_validation_error(client):
    _annotate_recover_flow_check(client)
    _run, _task, fc = _start_and_wait_flow_check(client)
    r = client.post(
        f"/api/openops/v1/flow-checks/{fc['flow_check_request_id']}:decide",
        headers=USER_HEADERS,
        json={"client_request_id": "fc_missing", "decision": "approved", "token": "", "flow_code": ""},
    )
    assert r.status_code == 400
    assert "token" in r.text


def test_fc_013_timeout_terminal_with_audit(client):
    """同 ASK-004：过期 pending → decide 返回 timeout + flow_check.timeout 审计/SSE 同 event_id。"""
    import asyncio as _asyncio

    from infra.db import exec1

    _annotate_recover_flow_check(client)
    run, _task, fc = _start_and_wait_flow_check(client)
    fid = fc["flow_check_request_id"]
    _asyncio.run(exec1("update sre_flow_check_request set expire_at=now() - interval '1 minute' "
                       "where flow_check_request_id=%(f)s", {"f": fid}))
    out = unwrap(client.post(f"/api/openops/v1/flow-checks/{fid}:decide", headers=USER_HEADERS,
                             json={"client_request_id": "fc_late", "decision": "approved",
                                   "token": "t", "flow_code": "c"}))
    assert out["decision"] == "timeout"
    audit_events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    timeout_audit = next(e for e in reversed(audit_events) if e["event_type"] == "flow_check.timeout")
    from runtime import events as runtime_events

    timeout_live = next(
        e for e in reversed(runtime_events.snapshot(run["agent_run_id"]))
        if e["event_type"] == "openops.flow_check.timeout"
    )
    assert timeout_live["event_id"] == timeout_audit["audit_event_id"]


def test_fc_014_cancel_task_cancels_pending_flow_check(client):
    _annotate_recover_flow_check(client)
    run, task, fc = _start_and_wait_flow_check(client)
    unwrap(client.post(f"/api/openops/v1/tasks/{task['task_id']}:cancel", headers=USER_HEADERS, json={}))
    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state", headers=USER_HEADERS))
    assert state["active_task"]["status"] == "cancelled"
    assert unwrap(client.get(
        f"/api/openops/v1/agent-runs/{run['agent_run_id']}/flow-checks", headers=USER_HEADERS)) == []
    assert fc["decision"] == "pending"  # 行创建时确为 pending，取消后离开 pending 集合


def test_fc_015_state_snapshot_carries_pending_flow_check(client):
    """刷新恢复路径：/state.pending_flow_checks 带配置快照，前端据此重挂卡并重拉 SDK。"""
    _annotate_recover_flow_check(client)
    run, _task, fc = _start_and_wait_flow_check(client)
    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state", headers=USER_HEADERS))
    rows = state["pending_flow_checks"]
    assert len(rows) == 1 and rows[0]["flow_check_request_id"] == fc["flow_check_request_id"]
    assert rows[0]["flow_check_config_json"]["init_path"] == FLOW_CONFIG["init_path"]


# ---- 单元：Gateway 凭证注入边界 ----

async def test_fc_020_gateway_injects_headers_only_for_flow_check_tool(monkeypatch):
    import uuid as _uuid

    from infra.external import http_mcp_client
    from runtime import tool_gateway
    from runtime.task_registry import TaskState

    async def fake_emit(st, run, event_type, **kw):
        pass

    monkeypatch.setattr(tool_gateway, "emit", fake_emit)
    ann = {
        "fc_tool": {"is_approval_required": False, "is_secret_required": False,
                    "is_flow_check_required": True, "flow_check_config": {},
                    "scope_mode": "none", "appid_arg_path": None, "status": "allowed",
                    "blocked_reason": None, "origin": "dynamic"},
        "plain_tool": {"is_approval_required": False, "is_secret_required": False,
                       "scope_mode": "none", "appid_arg_path": None, "status": "allowed",
                       "blocked_reason": None, "origin": "dynamic"},
    }
    st = TaskState(task_id="tsk_fc_unit", run_id=str(_uuid.uuid4()), user_id="u1",
                   instance_id="i1", input_text="x")
    st.tool_annotations = ann
    st.scope_ctx = {"effective_appids": ["APP-A"], "scope_snapshot_id": "snap-1",
                    "scope_revision": "r1", "workspace_id": "ws_unit"}
    run = {"framework_session_id": "sess-unit", "config_version_id": "cv-unit",
           "audit_trace_id": str(_uuid.uuid4()), "agent_team_instance_id": "i1"}
    st.flow_check_token = "tok-inject-1"
    st.flow_check_code = "FC-1"

    await tool_gateway.invoke(st, run, "fc_tool", {})
    h = http_mcp_client.last_call["headers"]
    assert h["X-OpenOps-Flow-Check-Token"] == "tok-inject-1"
    assert h["X-OpenOps-Flow-Check-Code"] == "FC-1"
    # 消费式注入：注完即弃（一号一操作）
    assert st.flow_check_token is None and st.flow_check_code is None

    # 无关工具不得带上凭证（避免跨工具泄凭证）
    await tool_gateway.invoke(st, run, "plain_tool", {})
    h2 = http_mcp_client.last_call["headers"]
    assert "X-OpenOps-Flow-Check-Token" not in h2
    assert "X-OpenOps-Flow-Check-Code" not in h2

    # 凭证已消费：同工具再调 fail-closed（不放行裸调用）
    from runtime.tool_gateway import ToolBlocked
    with pytest.raises(ToolBlocked) as e1:
        await tool_gateway.invoke(st, run, "fc_tool", {})
    assert e1.value.reason_code == "FLOW_CHECK_REQUIRED"

    # 凭证与被批准的工具绑定：热切换场景下 A 的 token 不得错发给 B
    st.flow_check_token, st.flow_check_code, st.flow_check_tool = "tok-A", "FC-A", "another_tool"
    with pytest.raises(ToolBlocked) as e2:
        await tool_gateway.invoke(st, run, "fc_tool", {})
    assert e2.value.reason_code == "FLOW_CHECK_REQUIRED"
    assert st.flow_check_token == "tok-A"  # 未消费（凭证仍属 another_tool）


async def test_fc_021_gateway_fail_closed_when_hot_switched_without_credential(monkeypatch):
    """标注中途热切为需四号（ASK 计划仍按启动快照）：Gateway 兜底 fail-closed，不放行裸调用。"""
    import uuid as _uuid

    from runtime import tool_gateway
    from runtime.task_registry import TaskState
    from runtime.tool_gateway import ToolBlocked

    captured = []

    async def fake_emit(st, run, event_type, **kw):
        captured.append({"event_type": event_type, **kw})

    monkeypatch.setattr(tool_gateway, "emit", fake_emit)
    ann = {"hot_tool": {"is_approval_required": False, "is_secret_required": False,
                        "is_flow_check_required": True, "flow_check_config": {},
                        "scope_mode": "none", "appid_arg_path": None, "status": "allowed",
                        "blocked_reason": None, "origin": "dynamic"}}
    st = TaskState(task_id="tsk_fc_hot", run_id=str(_uuid.uuid4()), user_id="u1",
                   instance_id="i1", input_text="x")
    st.tool_annotations = ann
    st.scope_ctx = {"effective_appids": ["APP-A"], "scope_snapshot_id": "s", "scope_revision": "r",
                    "workspace_id": "ws"}
    run = {"framework_session_id": "sess", "config_version_id": "cv",
           "audit_trace_id": str(_uuid.uuid4()), "agent_team_instance_id": "i1"}
    with pytest.raises(ToolBlocked) as e:
        await tool_gateway.invoke(st, run, "hot_tool", {})
    assert e.value.reason_code == "FLOW_CHECK_REQUIRED"
    assert any(ev["event_type"] == "openops.tool.blocked" for ev in captured)


# ---- 单元：operator / 租户 serviceId / 操作对象提取 ----

def test_fc_030_extract_target_object_and_service_id_resolution():
    from runtime.agentscope_runtime import _extract_target_object, _resolve_flow_check_service_id
    from runtime.task_registry import TaskState

    assert _extract_target_object({"target": {"appid": "A1"}}, "$.target.appid") == {
        "value": "A1", "path": "$.target.appid"}
    assert _extract_target_object({"target": {}}, "$.target.appid") is None
    assert _extract_target_object({"a": 1}, None) is None
    # 脱敏口径与 args 一致：路径末段键名命中敏感模式即打码（不因换个投影就明文外泄）
    assert _extract_target_object({"credentials": {"token": "sk-secret-1"}},
                                  "$.credentials.token")["value"] == "***"

    st = TaskState(task_id="t", run_id="r", user_id="u", instance_id="i", input_text="x")
    st.scope_ctx = {}
    tenant, sid = _resolve_flow_check_service_id(st, {"service_id_by_tenant": {TENANT: "svc-a"}})
    assert tenant == TENANT and sid == "svc-a"  # 默认企业回退（apptree 无 env 配置）
    st.scope_ctx = {"enterprise_id": "11111111111111111111111111111111"}
    tenant2, sid2 = _resolve_flow_check_service_id(
        st, {"service_id_by_tenant": {"11111111111111111111111111111111": "svc-b"},
             "service_id": "svc-fallback"})
    assert tenant2 == "11111111111111111111111111111111" and sid2 == "svc-b"
    tenant3, sid3 = _resolve_flow_check_service_id(st, {"service_id": "svc-fallback"})
    assert sid3 == "svc-fallback"  # 租户未命中 map → 全局 service_id 兜底
    assert tenant3 == "11111111111111111111111111111111"


def test_fc_031_iam_verify_extracts_operator(monkeypatch):
    """29.16：operator = userinfo `data.attributes.id`（默认字段，可 env 覆盖）；随身份缓存。"""
    import asyncio as _asyncio

    from test_iam import _FakeIam, _iam_env

    _iam_env(monkeypatch, OPENOPS_IAM_LOGIN_KEY_FIELD="data.attributes.identity")
    _FakeIam.script = {
        "token": (200, {"code": "201", "access_token": "at-1"}),
        "userinfo": (200, {"data": {"id": "4dc6028506164eedbca4e74595e0a423", "type": "user",
                                    "attributes": {"id": "4dc6028506164eedbca4e74595e0a423",
                                                   "identity": "l00833445", "name": "临时用户"}}}),
    }
    from infra.external import iam_client

    ident = _asyncio.run(iam_client.verify("iam=cookie-1", "127.0.0.1"))
    assert ident["login_key"] == "l00833445"
    assert ident["operator"] == "4dc6028506164eedbca4e74595e0a423"
