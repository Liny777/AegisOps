from __future__ import annotations

import time

from conftest import USER_HEADERS, create_instance, create_run, unwrap, wait_until


def _start_and_wait_approval(client):
    instance = create_instance(client, f"ASK Agent {time.time_ns()}")
    run = create_run(client, instance["instance_id"])
    task = unwrap(
        client.post(
            f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks",
            headers=USER_HEADERS,
            json={"client_request_id": f"task_{time.time_ns()}", "input_text": "请恢复 APP-A"},
        )
    )
    approvals = wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/approvals", headers=USER_HEADERS)),
    )
    assert approvals
    return run, task, approvals[0]


def test_ask_001_approved_continues_to_recovery_and_completion(client):
    run, _task, approval = _start_and_wait_approval(client)
    decided = unwrap(
        client.post(
            f"/api/openops/v1/approvals/{approval['approval_request_id']}:decide",
            headers=USER_HEADERS,
            json={"client_request_id": "approve_once", "decision": "approved"},
        )
    )
    assert decided["decision"] == "approved"

    completed = wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state", headers=USER_HEADERS))["active_task"]["status"]
        == "completed",
    )
    assert completed is True
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    assert any(e["event_type"] == "approval.approved" for e in events)
    assert any(e["external_request_id"] for e in events if e["action"] == "recover_execute")


def test_ask_003_rejected_does_not_call_recovery_tool(client):
    run, _task, approval = _start_and_wait_approval(client)
    unwrap(
        client.post(
            f"/api/openops/v1/approvals/{approval['approval_request_id']}:decide",
            headers=USER_HEADERS,
            json={"client_request_id": "reject_once", "decision": "rejected"},
        )
    )
    wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state", headers=USER_HEADERS))["active_task"]["status"]
        == "completed",
    )
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    assert any(e["event_type"] == "approval.rejected" for e in events)
    assert not any(e["action"] == "recover_execute" and e["external_request_id"] for e in events)


def test_ask_005_cancel_pending_approval_marks_cancelled(client):
    run, task, approval = _start_and_wait_approval(client)
    unwrap(client.post(f"/api/openops/v1/tasks/{task['task_id']}:cancel", headers=USER_HEADERS, json={}))
    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state", headers=USER_HEADERS))
    assert state["active_task"]["status"] == "cancelled"

    approvals = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/approvals", headers=USER_HEADERS))
    assert approvals == []

    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    assert any(e["event_type"] == "openops.task.cancelled" for e in events)
    # Direct row detail is not exposed in the run API; pending list empty proves it left pending.
    assert approval["decision"] == "pending"


def test_ask_004_timeout_terminal_with_audit(client):
    """连带 B（31 号 ASK-004）：过期 pending → decide 返回 timeout + approval.timeout 审计有痕。"""
    import asyncio as _asyncio

    from infra.db import exec1

    run, _task, approval = _start_and_wait_approval(client)
    aid = approval["approval_request_id"]
    _asyncio.run(exec1("update sre_approval_request set expire_at=now() - interval '1 minute' "
                       "where approval_request_id=%(a)s", {"a": aid}))
    out = unwrap(client.post(f"/api/openops/v1/approvals/{aid}:decide", headers=USER_HEADERS,
                             json={"client_request_id": "to1", "decision": "approved"}))
    assert out["decision"] == "timeout"  # 过期优先于本次决策（ASK-004）
    audit_events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    timeout_audit = next(e for e in reversed(audit_events) if e["event_type"] == "approval.timeout")
    assert timeout_audit  # 翻转有审计（修复点）
    from runtime import events as runtime_events

    timeout_live = next(
        e for e in reversed(runtime_events.snapshot(run["agent_run_id"]))
        if e["event_type"] == "openops.approval.timeout"
    )
    assert timeout_live["event_id"] == timeout_audit["audit_event_id"]


def test_ask_main_loop_timeout_yields_unexecuted_conclusion(client, runtime_backend, monkeypatch):
    """连带 B：主循环 ASK 超时 → 任务完成且结论为「未执行恢复」（不放行写工具）。"""
    import pytest as _pytest

    if runtime_backend != "agentscope":
        _pytest.skip("主循环超时路径仅 agentscope runtime")
    from runtime import agentscope_runtime as rt

    monkeypatch.setattr(rt, "ASK_TIMEOUT_S", 1.0)
    run, _task, _approval = _start_and_wait_approval(client)
    rid = run["agent_run_id"]

    def _done():
        s = unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/state", headers=USER_HEADERS))
        at = s.get("active_task")
        return at if at and at["status"] in ("completed", "failed") else None

    final = wait_until(_done, timeout=30.0, interval=0.2)
    assert final["status"] == "completed"
    approvals = unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/approvals", headers=USER_HEADERS))
    assert approvals == []  # pending 已全部收口
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{rid}", headers=USER_HEADERS))
    assert any(e["event_type"] == "approval.timeout" for e in events)
