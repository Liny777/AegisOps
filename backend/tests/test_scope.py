from __future__ import annotations

import time

from conftest import USER_HEADERS, create_instance, create_run, unwrap
from infra.external import omodel_mock


def _start_task(client, run_id: str, text: str = "巡检 APP-A"):
    return client.post(
        f"/api/openops/v1/agent-runs/{run_id}/tasks",
        headers=USER_HEADERS,
        json={"client_request_id": f"task_{time.time_ns()}", "input_text": text},
    )


def _audit_types(client, run_id: str) -> set[str]:
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run_id}", headers=USER_HEADERS))
    return {e["event_type"] for e in events}


def test_scope_001_ready_resolves_effective_appids(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    task = unwrap(_start_task(client, run["agent_run_id"]))
    assert task["status"] == "running"
    assert "scope.resolved" in _audit_types(client, run["agent_run_id"])  # effective_appids 来自 oModel


def test_scope_002_syncing_blocks_workspace_not_ready(client):
    instance = create_instance(client)  # 建实例时 ws_pay_abc ready；建后转 syncing
    omodel_mock._set_scope("ws_pay_abc", sync_status="syncing")
    run = create_run(client, instance["instance_id"])
    resp = _start_task(client, run["agent_run_id"])
    assert resp.status_code >= 400
    assert resp.json()["error"]["code"] == "WORKSPACE_NOT_READY"
    assert "scope.blocked" in _audit_types(client, run["agent_run_id"])


def test_scope_003_resolve_failed_fail_closed(client):
    instance = create_instance(client)
    omodel_mock._set_scope("ws_pay_abc", sync_status="failed")
    run = create_run(client, instance["instance_id"])
    resp = _start_task(client, run["agent_run_id"])
    assert resp.status_code >= 400
    assert resp.json()["error"]["code"] == "SCOPE_RESOLVE_FAILED"
    assert "scope.blocked" in _audit_types(client, run["agent_run_id"])


def test_scope_004_empty_scope_fail_closed(client):
    instance = create_instance(client)
    omodel_mock._set_scope("ws_pay_abc", app_ids=[])  # 仍 ready 但有效范围为空
    run = create_run(client, instance["instance_id"])
    resp = _start_task(client, run["agent_run_id"])
    assert resp.status_code >= 400
    assert resp.json()["error"]["code"] == "EMPTY_SCOPE"


def test_scope_005_revision_changed_writeback_and_event(client):
    instance = create_instance(client)
    omodel_mock._set_scope("ws_pay_abc", scope_revision="rev-20260709-002")
    run = create_run(client, instance["instance_id"])
    task = unwrap(_start_task(client, run["agent_run_id"]))
    assert task["status"] == "running"
    assert "scope.updated" in _audit_types(client, run["agent_run_id"])  # 采用新版本并写审计
    detail = unwrap(client.get(f"/api/openops/v1/agent-teams/{instance['instance_id']}", headers=USER_HEADERS))
    assert detail["instance"]["scope_revision"] == "rev-20260709-002"  # 回写实例


def test_scope_006_ttl_cache_reuse_survives_backend_change(client):
    instance = create_instance(client)
    run1 = create_run(client, instance["instance_id"])
    assert unwrap(_start_task(client, run1["agent_run_id"]))["status"] == "running"
    # 缓存已建；把 oModel 侧改成空范围（若重解会 EMPTY_SCOPE）
    omodel_mock._set_scope("ws_pay_abc", app_ids=[])
    # 同实例第二个 Run 在 30s TTL 内命中缓存、不重解 → 仍成功（非空 appids）
    run2 = create_run(client, instance["instance_id"])
    assert unwrap(_start_task(client, run2["agent_run_id"], "第二次"))["status"] == "running"
