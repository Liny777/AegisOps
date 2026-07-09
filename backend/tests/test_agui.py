from __future__ import annotations

import json
import time

from conftest import ADMIN_HEADERS, OTHER_HEADERS, USER_HEADERS, create_instance, create_run, unwrap


def _run_input(text: str = "支付延迟突增，帮我定位") -> dict:
    return {
        "threadId": "th_test", "runId": f"agui_{time.time_ns()}", "state": {},
        "messages": [{"id": "m1", "role": "user", "content": text}],
        "tools": [], "context": [], "forwardedProps": {},
    }


def _annotate_recover_no_ask(client) -> None:
    """把 recover_execute 标注为免审批：AG-UI 流不经 ASK 一次跑完（流内无并发审批人）。"""
    catalog = unwrap(client.get("/api/openops/v1/admin/mcp-tools", headers=ADMIN_HEADERS))
    rec = next(t for t in catalog if t["tool_name"] == "recover_execute")
    unwrap(client.put(
        f"/api/openops/v1/admin/mcp-tools/{rec['tool_catalog_id']}/annotation",
        headers=ADMIN_HEADERS,
        json={"is_approval_required": False, "is_secret_required": False, "scope_mode": "required",
              "appid_arg_path": "$.appid", "status": "allowed"},
    ))


def _collect_events(client, run_id: str, body: dict) -> list[dict]:
    out: list[dict] = []
    with client.stream("POST", f"/api/openops/v1/agent-runs/{run_id}/agui",
                       headers=USER_HEADERS, json=body) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data:"):
                out.append(json.loads(line[5:].strip()))
    return out


def test_agui_full_flow_standard_and_custom_events(client):
    """AG-UI 流：RUN_STARTED 开、RUN_FINISHED 收；标准 TOOL_CALL/TEXT_MESSAGE 与 CUSTOM(openops.*) 并存（30.4）。"""
    _annotate_recover_no_ask(client)
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    evs = _collect_events(client, run["agent_run_id"], _run_input())

    types = [e["type"] for e in evs]
    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"
    assert "TOOL_CALL_START" in types and "TOOL_CALL_END" in types and "TOOL_CALL_RESULT" in types
    # 对话主区有文本（mock 无增量 → task.completed 合成结论消息）
    assert "TEXT_MESSAGE_START" in types and "TEXT_MESSAGE_CONTENT" in types and "TEXT_MESSAGE_END" in types
    # openops.* 自定义事件透传（活动线 / RCA）
    customs = {e["name"] for e in evs if e["type"] == "CUSTOM"}
    assert "openops.tool.call.started" in customs
    assert "openops.rca.updated" in customs
    assert "openops.task.completed" in customs
    # envelope 原样在 value 里（sequence 供前端去重）
    rca = next(e for e in evs if e["type"] == "CUSTOM" and e["name"] == "openops.rca.updated")
    assert rca["value"]["sequence"] > 0
    assert rca["value"]["payload_redacted_json"]["revision"] >= 1


def test_agui_tool_call_pairing(client):
    """TOOL_CALL_START/END 按调用配对（toolCallId 一致）。"""
    _annotate_recover_no_ask(client)
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    evs = _collect_events(client, run["agent_run_id"], _run_input())
    starts = [e["toolCallId"] for e in evs if e["type"] == "TOOL_CALL_START"]
    ends = [e["toolCallId"] for e in evs if e["type"] == "TOOL_CALL_END"]
    assert starts and starts == ends  # 顺序执行、一一配对


def test_agui_owner_isolation(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    r = client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/agui",
                    headers=OTHER_HEADERS, json=_run_input())
    assert r.status_code == 403


def test_agui_closed_run_rejects(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}:close", headers=USER_HEADERS, json={}))
    r = client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/agui",
                    headers=USER_HEADERS, json=_run_input())
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "RUN_ALREADY_CLOSED"


def test_agui_requires_user_text(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    body = _run_input()
    body["messages"] = []
    r = client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/agui",
                    headers=USER_HEADERS, json=body)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_FAILED"
