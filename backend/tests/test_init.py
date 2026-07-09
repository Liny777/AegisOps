from __future__ import annotations

from conftest import USER_HEADERS, unwrap


def test_init_002_lists_template_and_ready_workspaces(client):
    templates = unwrap(client.get("/api/openops/v1/templates/available", headers=USER_HEADERS))
    assert templates[0]["template_key"] == "sensai_fast_recovery"

    workspaces = unwrap(client.get("/api/openops/v1/workspaces", headers=USER_HEADERS))
    assert any(w["workspace_id"] == "ws_pay_abc" and w["sync_status"] == "ready" for w in workspaces)

    models = unwrap(client.get("/api/openops/v1/models/platform", headers=USER_HEADERS))
    assert any(m["model_id"] == "glm-5.1" and m["status"] == "active" for m in models)


def test_init_003_blocks_workspace_not_ready(client):
    templates = unwrap(client.get("/api/openops/v1/templates/available", headers=USER_HEADERS))
    response = client.post(
        "/api/openops/v1/agent-teams",
        headers=USER_HEADERS,
        json={
            "client_request_id": "init_not_ready",
            "template_version_id": templates[0]["template_version_id"],
            "name": "同步中 Agent",
            "workspace_id": "ws_syncing",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "WORKSPACE_NOT_READY"


def test_init_004_ignores_non_v1_overlay_fields(client):
    templates = unwrap(client.get("/api/openops/v1/templates/available", headers=USER_HEADERS))
    created = unwrap(
        client.post(
            "/api/openops/v1/agent-teams",
            headers=USER_HEADERS,
            json={
                "client_request_id": "init_overlay",
                "template_version_id": templates[0]["template_version_id"],
                "name": "覆盖配置 Agent",
                "workspace_id": "ws_pay_abc",
                "initial_overlay_json": {
                    "main_role_append": "只追加 main agent 提示词",
                    "knowledge": {"enabled": True},
                    "sub_agents": [{"key": "bad"}],
                },
            },
        )
    )["instance"]
    detail = unwrap(client.get(f"/api/openops/v1/agent-teams/{created['instance_id']}", headers=USER_HEADERS))
    overlay = detail["active_config_version"]["overlay_json"]
    assert overlay == {"main_role_append": "只追加 main agent 提示词"}


def test_init_006_client_request_id_is_idempotent(client):
    templates = unwrap(client.get("/api/openops/v1/templates/available", headers=USER_HEADERS))
    body = {
        "client_request_id": "same_create_instance",
        "template_version_id": templates[0]["template_version_id"],
        "name": "幂等 Agent",
        "workspace_id": "ws_pay_abc",
    }
    one = unwrap(client.post("/api/openops/v1/agent-teams", headers=USER_HEADERS, json=body))
    two = unwrap(client.post("/api/openops/v1/agent-teams", headers=USER_HEADERS, json=body))
    assert one["instance"]["instance_id"] == two["instance"]["instance_id"]
