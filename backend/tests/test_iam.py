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


def test_iam_whitelist_grant_revoke_cycle(client):
    """B7·三：加入→重复加入幂等（列表不出重行）→移出→profile 拦截→防自锁。"""
    import time as _time

    def _wl(uid):
        rows = unwrap(client.get("/api/openops/v1/admin/users", headers=ADMIN_HEADERS))
        return [r for r in rows if r["user_id"] == uid]

    # 加入（新用户自动建行）+ 幂等重复加入
    for i in range(2):
        unwrap(client.post("/api/openops/v1/admin/users/whitelist", headers=ADMIN_HEADERS,
                           json={"client_request_id": f"wl_{i}_{_time.time_ns()}", "user_id": "newbie01", "display_name": "新人"}))
    rows = _wl("newbie01")
    assert len(rows) == 1 and rows[0]["whitelist_status"] == "active"  # LEFT JOIN 不出重行

    # 新用户可过白名单闸
    hdr = {"X-OpenOps-Mock-User": "newbie01", "X-OpenOps-Mock-Name": "Newbie"}
    assert client.get("/api/openops/v1/agent-teams", headers=hdr).status_code == 200

    # 移出 → 白名单闸 403
    unwrap(client.post("/api/openops/v1/admin/users/whitelist:revoke", headers=ADMIN_HEADERS,
                       json={"client_request_id": f"wlr_{_time.time_ns()}", "user_id": "newbie01"}))
    assert _wl("newbie01")[0]["whitelist_status"] == "none"
    assert client.get("/api/openops/v1/agent-teams", headers=hdr).status_code == 403

    # 重复移出 → 404；移除自己 → 400 防自锁
    r = client.post("/api/openops/v1/admin/users/whitelist:revoke", headers=ADMIN_HEADERS,
                    json={"client_request_id": f"wlr2_{_time.time_ns()}", "user_id": "newbie01"})
    assert r.status_code == 404
    r = client.post("/api/openops/v1/admin/users/whitelist:revoke", headers=ADMIN_HEADERS,
                    json={"client_request_id": f"wlr3_{_time.time_ns()}", "user_id": "admin"})
    assert r.status_code == 400
