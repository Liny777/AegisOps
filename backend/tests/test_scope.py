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


async def test_scope_apps_decorates_effective_appids(client):  # noqa: ARG001 —— 借 client 夹具复位 mock 库
    """scope_apps 是 effective_appids 的纯装饰：同元素同序、无名留空、失败态给 []（不得缺键）。"""
    ok = await omodel_mock.resolve_scope("ws_pay_abc", "rev", "u")
    assert ok["effective_appids"] == ["APP-A", "APP-B", "APP-C"]
    assert [a["appid"] for a in ok["scope_apps"]] == ok["effective_appids"]
    assert ok["scope_apps"] == [{"appid": "APP-A", "name": "支付核心交易"},
                                {"appid": "APP-B", "name": "订单履约中心"},
                                {"appid": "APP-C", "name": ""}]  # APP-C 无 apps 行 → 降级
    for ws in ("ws_syncing", "ws_failed", "ws_missing"):
        assert (await omodel_mock.resolve_scope(ws, "rev", "u"))["scope_apps"] == []


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


def test_scope_007_override_bypasses_omodel(client, monkeypatch):
    """联调缝 OPENOPS_SCOPE_OVERRIDE_APPIDS：即便 oModel failed，也用覆盖 appid 解析成功（跳过 oModel）。

    验证方式——把 mock oModel 置成 failed（正常会 SCOPE_RESOLVE_FAILED 阻断），设覆盖后 task 仍 running，
    即证明 scope 走的是覆盖 appid、根本没调 oModel。
    """
    monkeypatch.setenv("OPENOPS_SCOPE_OVERRIDE_APPIDS", "APP-REAL-1, APP-REAL-2")
    instance = create_instance(client)
    omodel_mock._set_scope("ws_pay_abc", sync_status="failed")
    run = create_run(client, instance["instance_id"])
    assert unwrap(_start_task(client, run["agent_run_id"]))["status"] == "running"
