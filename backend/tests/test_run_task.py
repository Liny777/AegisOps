from __future__ import annotations

import time

from conftest import USER_HEADERS, create_instance, create_run, unwrap, wait_until


def start_task(client, run_id: str, text: str = "巡检 APP-A"):
    return unwrap(
        client.post(
            f"/api/openops/v1/agent-runs/{run_id}/tasks",
            headers=USER_HEADERS,
            json={"client_request_id": f"task_{time.time_ns()}", "input_text": text},
        )
    )


def test_run_001_003_task_start_resolves_scope_and_waits_for_ask(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    task = start_task(client, run["agent_run_id"])
    assert task["status"] == "running"

    approval = wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/approvals", headers=USER_HEADERS)),
    )
    assert approval[0]["tool_call_name"] == "recover_execute"

    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state", headers=USER_HEADERS))
    assert state["active_task"]["status"] == "running"
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    event_types = {e["event_type"] for e in events}
    assert "task.started" in event_types
    assert "scope.resolved" in event_types
    assert "openops.approval.required" in event_types


def test_run_002_cannot_create_run_on_other_owner_instance(client):
    instance = create_instance(client)
    forbidden = client.post(
        "/api/openops/v1/agent-runs",
        headers={"X-OpenOps-Mock-User": "admin", "X-OpenOps-Mock-Name": "Admin"},
        json={"client_request_id": "other_run", "agent_team_instance_id": instance["instance_id"]},
    )
    assert forbidden.status_code == 403


def test_run_005_closed_run_rejects_new_task(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    closed = unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}:close", headers=USER_HEADERS, json={}))
    assert closed["run_status"] == "closed"

    response = client.post(
        f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks",
        headers=USER_HEADERS,
        json={"client_request_id": "closed_task", "input_text": "还想继续"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RUN_ALREADY_CLOSED"


def test_cancel_001_task_cancel_keeps_run_active(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    task = start_task(client, run["agent_run_id"])

    cancelled = unwrap(client.post(f"/api/openops/v1/tasks/{task['task_id']}:cancel", headers=USER_HEADERS, json={}))
    assert cancelled["status"] == "cancelled"
    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state", headers=USER_HEADERS))
    assert state["run"]["run_status"] == "active"

    next_task = start_task(client, run["agent_run_id"], "继续新的巡检")
    assert next_task["status"] == "running"


def test_model_switch_is_run_level_only(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    selected = unwrap(
        client.post(
            f"/api/openops/v1/agent-runs/{run['agent_run_id']}:select-model",
            headers=USER_HEADERS,
            json={"client_request_id": "model_select", "model_source": "qwen3.5-instruct"},
        )
    )
    assert selected["selected_model"] == "qwen3.5-instruct"

    cfgs = unwrap(client.get(f"/api/openops/v1/agent-teams/{instance['instance_id']}/config-versions", headers=USER_HEADERS))
    assert len(cfgs) == 1
