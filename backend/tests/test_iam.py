from __future__ import annotations

from conftest import ADMIN_HEADERS, OTHER_HEADERS, USER_HEADERS, create_instance, create_run, unwrap


def test_iam_001_requires_login(client):
    response = client.get("/api/openops/v1/templates/available")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_iam_002_me_allows_non_whitelisted_but_profile_blocks(client):
    me = unwrap(client.get("/api/openops/v1/me", headers=OTHER_HEADERS))
    assert me["whitelisted"] is False

    blocked = client.get("/api/openops/v1/templates/available", headers=OTHER_HEADERS)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "NOT_WHITELISTED"


def test_iam_003_regular_user_cannot_access_admin(client):
    response = client.get("/api/openops/v1/admin/users", headers=USER_HEADERS)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

    rows = unwrap(client.get("/api/openops/v1/admin/users", headers=ADMIN_HEADERS))
    assert any(r["user_id"] == "0026demo01" for r in rows)


def test_iam_004_005_owner_isolation_for_instance_and_run(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])

    inst_forbidden = client.get(f"/api/openops/v1/agent-teams/{instance['instance_id']}", headers=ADMIN_HEADERS)
    assert inst_forbidden.status_code == 403

    run_forbidden = client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state", headers=ADMIN_HEADERS)
    assert run_forbidden.status_code == 403
