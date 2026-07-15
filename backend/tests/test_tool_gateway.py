from __future__ import annotations

import time
import uuid

import pytest
from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, create_run, unwrap, wait_until
from infra.external import http_mcp_client, omodel_mock
from runtime import tool_gateway
from runtime.task_registry import TaskState
from runtime.tool_gateway import ToolBlocked


def _start_task(client, run_id: str, text: str = "巡检 APP-A"):
    return unwrap(
        client.post(
            f"/api/openops/v1/agent-runs/{run_id}/tasks",
            headers=USER_HEADERS,
            json={"client_request_id": f"task_{time.time_ns()}", "input_text": text},
        )
    )


def _audit(client, run_id: str):
    return unwrap(client.get(f"/api/openops/v1/audit/runs/{run_id}", headers=USER_HEADERS))


def _wait_status(client, run_id: str, status: str) -> bool:
    return wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{run_id}/state", headers=USER_HEADERS))["active_task"]["status"]
        == status,
        timeout=5.0,
    )


# ---- API 级：走完整 mock runtime 调用链 ----

def test_tool_001_platform_headers_injected(client):
    """平台 MCP 调用注入 28.2 X-OpenOps-* 上下文 header（Gateway 是唯一注入点）。"""
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    task = _start_task(client, run["agent_run_id"])
    approvals = wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/approvals", headers=USER_HEADERS)),
    )
    unwrap(client.post(
        f"/api/openops/v1/approvals/{approvals[0]['approval_request_id']}:decide",
        headers=USER_HEADERS, json={"client_request_id": "ok", "decision": "approved"},
    ))
    assert _wait_status(client, run["agent_run_id"], "completed") is True

    last = http_mcp_client.last_call
    assert last is not None and last["tool"] == "recover_execute"
    h = last["headers"]
    assert h["X-OpenOps-Task-Id"] == task["task_id"]
    assert "APP-A" in h["X-OpenOps-Effective-Appids"]
    assert h["X-OpenOps-Scope-Snapshot-Id"]
    assert h["X-OpenOps-User-Id"] == "0026demo01"
    assert h["workspace_id"]  # omodel 工作空间 id 出站（值=该实例绑定的 workspace）


def test_tool_002_blocked_annotation_direct_fail_closed(client):
    """标注 status=blocked：不 ASK、直接 fail-closed（TOOL_BLOCKED），任务失败。"""
    catalog = unwrap(client.get("/api/openops/v1/admin/mcp-tools", headers=ADMIN_HEADERS))
    rec = next(t for t in catalog if t["tool_name"] == "recover_execute")
    unwrap(client.put(
        f"/api/openops/v1/admin/mcp-tools/{rec['tool_catalog_id']}/annotation",
        headers=ADMIN_HEADERS,
        json={"is_approval_required": True, "is_secret_required": False, "scope_mode": "required",
              "appid_arg_path": "$.appid", "status": "blocked", "blocked_reason": "变更冻结期"},
    ))
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    _start_task(client, run["agent_run_id"])
    assert _wait_status(client, run["agent_run_id"], "failed") is True

    events = _audit(client, run["agent_run_id"])
    types = {e["event_type"] for e in events}
    assert "openops.tool.blocked" in types
    assert any(e["reason_code"] == "TOOL_BLOCKED" for e in events)
    assert "openops.approval.required" not in types  # blocked 不进入 ASK
    assert not any(e["action"] == "recover_execute" and e["external_request_id"] for e in events)  # 未调用 MCP


def test_tool_003_appid_out_of_scope_fail_closed(client):
    """入参 APPID 不在 effective_appids：APPID_OUT_OF_SCOPE fail-closed。"""
    instance = create_instance(client)
    omodel_mock._set_scope("ws_pay_abc", app_ids=["APP-X"])  # 范围变为不含 APP-A
    run = create_run(client, instance["instance_id"])
    _start_task(client, run["agent_run_id"])
    assert _wait_status(client, run["agent_run_id"], "failed") is True

    events = _audit(client, run["agent_run_id"])
    assert any(e["event_type"] == "openops.tool.blocked" and e["reason_code"] == "APPID_OUT_OF_SCOPE" for e in events)
    assert not any(e["action"] == "query_resource" and e["external_request_id"] for e in events)  # 未调用 MCP


# ---- 单元级：直接打 Gateway（emit 桩掉，不依赖 DB）----

def _st(annotations: dict | None, appids: list[str] | None = None) -> TaskState:
    st = TaskState(task_id="tsk_unit", run_id=str(uuid.uuid4()), user_id="u1", instance_id="i1", input_text="x")
    st.tool_annotations = annotations
    st.scope_ctx = {"effective_appids": appids or ["APP-A"], "scope_snapshot_id": "snap-1",
                    "scope_revision": "r1", "workspace_id": "ws_unit"}
    return st


_RUN = {"framework_session_id": "sess-unit", "config_version_id": "cv-unit", "audit_trace_id": str(uuid.uuid4()),
        "agent_team_instance_id": "i1"}


def test_tool_010_platform_headers_carry_user_cookie_and_ip():
    """平台 MCP 出站透传用户登录态：请求上下文有用户 cookie/IP 时注入 Cookie+IAM-Client-Ip+X-Forwarded-For
    （IAM 保护的内网 MCP 靠 cookie 鉴权，且华为 IAM 会话绑客户端 IP，cookie 必带用户真实 IP）；
    无 cookie（mock / 未开 IAM）时不注入——零回归。X-OpenOps-* 上下文头照旧。"""
    from infra import request_context as rc

    st = _st({})
    rc.clear()
    h0 = tool_gateway._platform_headers(st, _RUN)
    assert "Cookie" not in h0 and "IAM-Client-Ip" not in h0 and "X-Forwarded-For" not in h0
    assert h0["X-OpenOps-User-Id"] == "u1"  # 原上下文头在

    rc.set_request_user("u1", "JSESSIONID=abc")
    rc.set_client_ip("10.1.2.3")
    try:
        h1 = tool_gateway._platform_headers(st, _RUN)
    finally:
        rc.clear()
    assert h1["Cookie"] == "JSESSIONID=abc"
    assert h1["IAM-Client-Ip"] == "10.1.2.3" and h1["X-Forwarded-For"] == "10.1.2.3"
    assert "Mozilla" in h1.get("User-Agent", "")  # 浏览器 UA（网关拦脚本 UA）
    assert h1["X-OpenOps-User-Id"] == "u1"  # 原上下文头不受影响


def test_tool_011_platform_headers_carry_workspace_id():
    """平台 MCP 出站带 omodel 工作空间 id header `workspace_id`（scope_ctx 里有就发，无则不发=零回归）。"""
    st = _st({})  # _st 的 scope_ctx 含 workspace_id="ws_unit"
    assert tool_gateway._platform_headers(st, _RUN)["workspace_id"] == "ws_unit"
    # scope_ctx 无 workspace_id（旧缓存态）→ 不发该 header
    st.scope_ctx = {"effective_appids": ["APP-A"], "scope_snapshot_id": "s", "scope_revision": "r"}
    assert "workspace_id" not in tool_gateway._platform_headers(st, _RUN)


@pytest.fixture()
def quiet_emit(monkeypatch):
    """桩掉 Gateway 的 emit（不写 DB/SSE），并捕获事件供断言。"""
    captured: list[dict] = []

    async def fake_emit(st, run, event_type, **kw):
        captured.append({"event_type": event_type, **kw})

    monkeypatch.setattr(tool_gateway, "emit", fake_emit)
    return captured


async def test_tool_004_unannotated_fail_closed(quiet_emit):
    st = _st(annotations={})
    with pytest.raises(ToolBlocked) as e:
        await tool_gateway.invoke(st, _RUN, "unknown_tool", {"appid": "APP-A"})
    assert e.value.reason_code == "TOOL_NOT_ANNOTATED"
    assert any(ev["event_type"] == "openops.tool.blocked" for ev in quiet_emit)


async def test_tool_005_user_mcp_no_platform_headers(quiet_emit):
    st = _st(annotations={})  # 用户分支不看平台标注
    await tool_gateway.invoke(st, _RUN, "user_custom_tool", {"q": "x"}, source_type="user")
    headers = (http_mcp_client.last_call or {})["headers"]
    assert not any(k.startswith("X-OpenOps-") for k in headers)
    assert "Cookie" not in headers and "Authorization" not in headers


async def test_tool_005b_call_lifecycle_has_one_stable_correlation_id(quiet_emit):
    """一次调用的 started/succeeded 共享 ID，且不同调用不会复用。"""
    st = _st(annotations={})
    await tool_gateway.invoke(st, _RUN, "user_custom_tool", {"q": "first"}, source_type="user")
    await tool_gateway.invoke(st, _RUN, "user_custom_tool", {"q": "second"}, source_type="user")

    lifecycle = [
        event for event in quiet_emit
        if event["event_type"] in ("openops.tool.call.started", "openops.tool.call.succeeded")
    ]
    ids = [event["payload"]["tool_call_id"] for event in lifecycle]
    assert ids[0] == ids[1]
    assert ids[2] == ids[3]
    assert ids[0] != ids[2]


async def test_tool_005c_failed_call_reuses_started_correlation_id(quiet_emit, monkeypatch):
    async def fail_call(*_args, **_kwargs):
        raise RuntimeError("tool failed")

    monkeypatch.setattr(http_mcp_client, "call_tool", fail_call)
    with pytest.raises(RuntimeError, match="tool failed"):
        await tool_gateway.invoke(_st(annotations={}), _RUN, "user_custom_tool", {}, source_type="user")

    lifecycle = [
        event for event in quiet_emit
        if event["event_type"] in ("openops.tool.call.started", "openops.tool.call.failed")
    ]
    assert [event["event_type"] for event in lifecycle] == [
        "openops.tool.call.started", "openops.tool.call.failed",
    ]
    assert lifecycle[0]["payload"]["tool_call_id"] == lifecycle[1]["payload"]["tool_call_id"]


async def test_tool_006_secret_required_order_and_no_leak(quiet_emit, monkeypatch):
    ann = {"tool_x": {"is_approval_required": False, "is_secret_required": True,
                      "scope_mode": "none", "appid_arg_path": None, "status": "allowed", "blocked_reason": None}}
    # 未配置凭证：SECRET_REQUIRED fail-closed（不产生 tool.call.started，即未触发调用）
    monkeypatch.delenv("OPENOPS_PLATFORM_MCP_TOKEN", raising=False)
    with pytest.raises(ToolBlocked) as e:
        await tool_gateway.invoke(_st(ann), _RUN, "tool_x", {})
    assert e.value.reason_code == "SECRET_REQUIRED"
    assert not any(ev["event_type"] == "openops.tool.call.started" for ev in quiet_emit)

    # 配置凭证：注入 Authorization，且 token 不出现在任何事件 payload（SEC-002）
    monkeypatch.setenv("OPENOPS_PLATFORM_MCP_TOKEN", "tok-secret-123")
    await tool_gateway.invoke(_st(ann), _RUN, "tool_x", {})
    assert http_mcp_client.last_call["headers"]["Authorization"] == "Bearer tok-secret-123"
    assert "tok-secret-123" not in repr(quiet_emit)


async def test_tool_007_scope_optional_mode(quiet_emit):
    ann = {"tool_y": {"is_approval_required": False, "is_secret_required": False,
                      "scope_mode": "optional", "appid_arg_path": "$.appid", "status": "allowed", "blocked_reason": None}}
    # optional：不传 APPID 放行
    await tool_gateway.invoke(_st(ann), _RUN, "tool_y", {})
    # optional：传了越界 APPID 拦截
    with pytest.raises(ToolBlocked) as e:
        await tool_gateway.invoke(_st(ann), _RUN, "tool_y", {"appid": "APP-Z"})
    assert e.value.reason_code == "APPID_OUT_OF_SCOPE"

async def test_tool_008_dynamic_unannotated_catalog_row_keeps_injection(quiet_emit, monkeypatch):
    """动态注册表工具：reconcile 入库产生的**未标注 catalog 行**不推翻运行时注入（内网实测回归：
    alarm-server 入库后 query_alarm_list 被 TOOL_NOT_ANNOTATED 拦）；无 origin 标记的快照（平台工具
    schema 变更标注软删场景）仍 fail-closed——28.7 铁律不破。管理员显式标注后以 catalog 为准（既有
    hot-update 用例护栏）。"""
    from infra.repositories import mcp_tools

    async def unannotated_row(_name):
        return {"annotation_id": None, "tool_name": "query_alarm_list"}

    monkeypatch.setattr(mcp_tools, "get_runtime_annotation", unannotated_row)

    dyn = {"is_approval_required": False, "is_secret_required": False, "scope_mode": "none",
           "appid_arg_path": None, "status": "allowed", "origin": "dynamic"}
    st = _st(annotations={"query_alarm_list": dyn})
    await tool_gateway.invoke(st, _RUN, "query_alarm_list", {"alarm_code": "1"})  # 放行（不抛）

    plat = {k: v for k, v in dyn.items() if k != "origin"}
    st2 = _st(annotations={"query_alarm_list": plat})
    with pytest.raises(ToolBlocked) as e:
        await tool_gateway.invoke(st2, _RUN, "query_alarm_list", {"alarm_code": "1"})
    assert e.value.reason_code == "TOOL_NOT_ANNOTATED"
