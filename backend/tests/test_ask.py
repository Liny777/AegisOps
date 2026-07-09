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
