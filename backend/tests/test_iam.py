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


# ---- B9：真 IAM 双步握手（OPENOPS_IAM_ENABLED=1 + 假上游） ----

class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _FakeIam:
    """假 IAM 上游：script = {"token": (status, payload), "userinfo": (status, payload)}。"""

    calls = {"token": 0, "userinfo": 0}
    script = {}

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        kind = "token" if "token" in url else "userinfo"
        _FakeIam.calls[kind] += 1
        status, payload = _FakeIam.script[kind]
        return _FakeResp(status, payload)


def _iam_env(monkeypatch, **extra):
    from infra.external import iam_client

    monkeypatch.setenv("OPENOPS_IAM_ENABLED", "1")
    monkeypatch.setenv("OPENOPS_IAM_ACCESS_TOKEN_URL", "http://iam.internal/token")
    monkeypatch.setenv("OPENOPS_IAM_USERINFO_URL", "http://iam.internal/userinfo")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(iam_client.httpx, "AsyncClient", _FakeIam)
    iam_client.clear_cache()
    _FakeIam.calls = {"token": 0, "userinfo": 0}


def test_iam_b9_double_step_and_cache(client, monkeypatch):
    """成功路径：cookie→token→userinfo→login_key 小写化；TTL 内二次请求命中缓存不重打 IAM。"""
    _iam_env(monkeypatch)
    _FakeIam.script = {"token": (200, {"code": "201", "access_token": "tk-1"}),
                       "userinfo": (200, {"id": "W123XYZ", "name": "王五"})}
    hdr = {"Cookie": "iam_sess=abc123"}
    me = unwrap(client.get("/api/openops/v1/me", headers=hdr))
    assert me["user_id"] == "w123xyz"  # strip+lower（老项目口径）
    assert me["display_name"] == "王五"
    assert me["whitelisted"] is False  # 白名单仍须管理员显式开通
    unwrap(client.get("/api/openops/v1/me", headers=hdr))  # 二次
    assert _FakeIam.calls["token"] == 1  # 命中 TokenCache
    # 未带 cookie → 401
    assert client.get("/api/openops/v1/me").status_code == 401


def test_iam_b9_rejects_and_upstream(client, monkeypatch):
    """code≠201 → 401（带 login_url）；上游 500 → 502 IAM_UPSTREAM；缺用户标识 → 401。"""
    from infra.external import iam_client

    _iam_env(monkeypatch, OPENOPS_IAM_LOGIN_URL="https://iam.example/login")
    _FakeIam.script = {"token": (200, {"code": "403"}), "userinfo": (200, {})}
    r = client.get("/api/openops/v1/me", headers={"Cookie": "iam_sess=expired"})
    assert r.status_code == 401
    assert r.json()["error"]["login_url"] == "https://iam.example/login"

    iam_client.clear_cache()
    _FakeIam.script = {"token": (500, {}), "userinfo": (200, {})}
    r = client.get("/api/openops/v1/me", headers={"Cookie": "iam_sess=x2"})
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "IAM_UPSTREAM"

    iam_client.clear_cache()
    _FakeIam.script = {"token": (200, {"code": "201", "accessToken": "tk-2"}),
                       "userinfo": (200, {"name": "无标识"})}
    r = client.get("/api/openops/v1/me", headers={"Cookie": "iam_sess=x3"})
    assert r.status_code == 401


def test_iam_b9_dot_path_fields_and_logout(client, monkeypatch):
    """点分路径字段映射（data.user.id）；logout 清 TokenCache 后需重新校验。"""
    from infra.external import iam_client

    _iam_env(monkeypatch,
             OPENOPS_IAM_LOGIN_KEY_FIELD="data.user.id",
             OPENOPS_IAM_DISPLAY_NAME_FIELD="data.user.cn")
    _FakeIam.script = {"token": (200, {"code": "201", "access_token": "tk-3"}),
                       "userinfo": (200, {"data": {"user": {"id": "P777", "cn": "赵六"}}})}
    hdr = {"Cookie": "iam_sess=dot"}
    me = unwrap(client.get("/api/openops/v1/me", headers=hdr))
    assert me["user_id"] == "p777" and me["display_name"] == "赵六"
    # logout 清缓存 → 再请求重新打 IAM
    before = _FakeIam.calls["token"]
    out = unwrap(client.post("/api/openops/v1/auth/logout", headers=hdr))
    assert "signout_url" in out
    unwrap(client.get("/api/openops/v1/me", headers=hdr))
    assert _FakeIam.calls["token"] == before + 1


def test_iam_host_placeholder_substitution(monkeypatch):
    """{host} 占位符（老项目 D5.11 口径）：login/signout/token URL 随请求域名替换，多域名共用一份配置。"""
    from types import SimpleNamespace

    from infra.external import iam_client

    monkeypatch.setenv("OPENOPS_IAM_LOGIN_URL",
                       "https://{host}/epstenant/#/login?redirect=https%3A%2F%2F{host}%2Faegisops%2F%3F")
    assert iam_client.login_url("console-a.x.com") == \
        "https://console-a.x.com/epstenant/#/login?redirect=https%3A%2F%2Fconsole-a.x.com%2Faegisops%2F%3F"
    assert "{host}" in (iam_client.login_url("") or "")  # 无 host 上下文保持原样（兜底）

    # extract_host：X-Forwarded-Host 优先（逗号取首段）> Host
    req = SimpleNamespace(headers={"x-forwarded-host": "console-b.x.com, inner.lb", "host": "backend:18082"})
    assert iam_client.extract_host(req) == "console-b.x.com"
    req2 = SimpleNamespace(headers={"host": "console-c.x.com"})
    assert iam_client.extract_host(req2) == "console-c.x.com"
    req3 = SimpleNamespace(headers={})
    assert iam_client.extract_host(req3) == ""
