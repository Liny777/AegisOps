from __future__ import annotations

import pytest

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
    from api import deps
    from infra.external import iam_client

    monkeypatch.setenv("OPENOPS_IAM_ENABLED", "1")
    monkeypatch.setenv("OPENOPS_IAM_ACCESS_TOKEN_URL", "https://iam.internal/token")
    monkeypatch.setenv("OPENOPS_IAM_USERINFO_URL", "https://iam.internal/userinfo")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(iam_client.httpx, "AsyncClient", _FakeIam)
    monkeypatch.setattr(deps, "_client_ip", lambda _request: "127.0.0.1")
    iam_client.clear_cache()
    _FakeIam.calls = {"token": 0, "userinfo": 0}


def _install_iam_omodel_http(monkeypatch, *, token_code: str = "201"):
    """同一 httpx 桩贯穿 IAM 双步校验与 oModel create，捕获最终请求上下文。"""
    import httpx

    from infra import request_context

    captured = {"inits": [], "gets": [], "posts": [], "contexts": []}

    class _OModelResp(_FakeResp):
        def __init__(self):
            super().__init__(201, {
                "id": "0026demo01-ws-api0001", "name": "API 链路范围", "status": "active",
                "config": {"workspace_ui": {"scopes": [
                    {"projectId": "APP-A"}, {"projectId": "APP-B"},
                ]}},
            })
            self.headers = {"X-Request-Id": "omodel-up-1"}
            self.text = ""

    class _UnifiedClient:
        def __init__(self, *args, **kwargs):
            self.base = str(kwargs.get("base_url") or "")
            self.default_headers = dict(kwargs.get("headers") or {})
            captured["inits"].append(dict(kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            captured["gets"].append({"url": str(url), "headers": dict(headers or {})})
            if "token" in str(url):
                return _FakeResp(200, {"code": token_code, "access_token": "fake-access"})
            return _FakeResp(200, {"id": "0026demo01", "name": "林一"})

        async def post(self, url, **kwargs):
            effective_headers = {**self.default_headers, **dict(kwargs.get("headers") or {})}
            full_url = self.base.rstrip("/") + "/" + str(url).lstrip("/")
            captured["posts"].append({"url": full_url, "headers": effective_headers,
                                      "json": kwargs.get("json")})
            captured["contexts"].append((request_context.current_user_id(), request_context.user_cookie()))
            return _OModelResp()

    monkeypatch.setattr(httpx, "AsyncClient", _UnifiedClient)
    return captured


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


def test_iam_cookie_reaches_omodel_create_with_target_origin(client, monkeypatch):
    from api import deps

    _iam_env(monkeypatch)
    captured = _install_iam_omodel_http(monkeypatch)
    monkeypatch.setattr(deps, "_client_ip", lambda _request: "10.20.30.40")
    monkeypatch.setenv("OPENOPS_OMODEL", "real")
    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "https://console.example/omodel")
    monkeypatch.setenv("OPENOPS_OMODEL_TENANT_ID", "T-CURRENT")
    monkeypatch.delenv("OPENOPS_OMODEL_COOKIE", raising=False)
    monkeypatch.delenv("OPENOPS_CONSOLE_COOKIE", raising=False)
    cookie = "iam_sess=valid-api; EnterpriseId=T-CURRENT"
    response = client.post(
        "/api/openops/v1/workspaces",
        headers={"Cookie": cookie, "Origin": "https://frontend.example"},
        json={
            "client_request_id": "api-cookie-chain", "name": "API 链路范围",
            "app_ids": ["APP-A", "APP-B"],
            "apps": [
                {"app_id": "APP-A", "name": "应用甲", "tenant_id": "T-OTHER"},
                {"app_id": "APP-B", "name": "应用乙", "tenant_id": "T-CURRENT"},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["workspace_id"] == "0026demo01-ws-api0001"
    assert captured["gets"][0]["headers"]["Cookie"] == cookie
    assert captured["gets"][0]["headers"]["IAM-Client-Ip"] == "10.20.30.40"
    post = captured["posts"][0]
    assert post["url"] == "https://console.example/omodel/api/v1/workspaces"
    assert post["headers"]["Cookie"] == cookie  # 用户登录态透传
    assert "Mozilla/5.0" in post["headers"].get("User-Agent", "")  # 浏览器 UA（绕网关脚本 UA 拦截）
    assert post["headers"]["Origin"] == "https://console.example"  # CSRF 同源头（从目标 base 派生）
    assert post["headers"]["Referer"] == "https://console.example/"
    assert post["headers"]["Sec-Fetch-Site"] == "same-origin"
    assert post["headers"]["IAM-Client-Ip"] == "10.20.30.40"
    assert post["headers"]["X-Forwarded-For"] == "10.20.30.40"
    assert post["headers"]["Origin"] != "https://frontend.example"
    assert captured["contexts"] == [("0026demo01", cookie)]
    assert post["json"]["config"]["workspace_ui"] == {
        "tenantId": "T-CURRENT",
        "scopes": [
            {"projectId": "APP-A", "projectCn": "应用甲", "tenantId": "T-OTHER"},
            {"projectId": "APP-B", "projectCn": "应用乙", "tenantId": "T-CURRENT"},
        ],
        "status": "running", "owner": "林一",
    }
    assert "client_request_id" not in post["json"]


def test_invalid_iam_session_never_calls_omodel(client, monkeypatch):
    _iam_env(monkeypatch)
    captured = _install_iam_omodel_http(monkeypatch, token_code="403")
    monkeypatch.setenv("OPENOPS_OMODEL", "real")
    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "https://console.example/omodel")

    response = client.post(
        "/api/openops/v1/workspaces",
        headers={"Cookie": "iam_sess=expired"},
        json={"client_request_id": "expired", "name": "不会创建", "app_ids": ["APP-A"]},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert len(captured["gets"]) == 1
    assert captured["posts"] == []
    assert not any(init.get("base_url") for init in captured["inits"])


def test_iam_b9_rejects_and_upstream(client, monkeypatch):
    """code≠201 → 401（带 login_url）；上游 500 → 502 IAM_UPSTREAM；缺用户标识 → 401。"""
    from infra.external import iam_client

    _iam_env(monkeypatch, OPENOPS_IAM_LOGIN_URL="https://iam.example/login")
    _FakeIam.script = {"token": (200, {"code": "403"}), "userinfo": (200, {})}
    r = client.get("/api/openops/v1/me", headers={"Cookie": "iam_sess=expired"})
    assert r.status_code == 401
    assert r.json()["error"]["login_url"] == "https://iam.example/login"

    # token 接口本身 HTTP 401（token 过期）→ 401 带 login_url（跳登录），非 502 服务异常屏
    iam_client.clear_cache()
    _FakeIam.script = {"token": (401, {}), "userinfo": (200, {})}
    r = client.get("/api/openops/v1/me", headers={"Cookie": "iam_sess=expired-http"})
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


def test_iam_browser_host_placeholder_substitution(monkeypatch):
    """{host} 只用于 login/signout 浏览器导航；鉴权出站目标不读取请求 Host。"""
    from types import SimpleNamespace

    from infra.external import iam_client

    monkeypatch.setenv("OPENOPS_IAM_LOGIN_URL",
                       "https://{host}/epstenant/#/login?redirect=https%3A%2F%2F{host}%2Fopenops%2F%3F")
    assert iam_client.login_url("console-a.x.com") == \
        "https://console-a.x.com/epstenant/#/login?redirect=https%3A%2F%2Fconsole-a.x.com%2Fopenops%2F%3F"
    assert "{host}" in (iam_client.login_url("") or "")  # 无 host 上下文保持原样（兜底）

    # browser_host 只信网关写入的 X-Forwarded-Host；不回退可能是后端地址的 Host。
    req = SimpleNamespace(headers={"x-forwarded-host": "console-b.x.com, inner.lb", "host": "backend:18082"})
    assert iam_client.browser_host(req) == "console-b.x.com"
    req2 = SimpleNamespace(headers={"host": "console-c.x.com"})
    assert iam_client.browser_host(req2) == ""
    req3 = SimpleNamespace(headers={})
    assert iam_client.browser_host(req3) == ""




@pytest.mark.asyncio
@pytest.mark.parametrize("bad_target", [
    "http://iam.internal/token",
    "https://{host}/token",
    "https://user:secret@iam.internal/token",
    "https://bad_host.internal/token",
    "https://iam.internal:bad/token",
    "https://iam.internal/token?redirect=1",
    "https://iam.internal\\@attacker.example/token",
])
async def test_iam_auth_target_runtime_fail_closed(monkeypatch, bad_target):
    """即使跳过发布门禁，携带 Cookie 的 IAM 请求也不能发往动态/歧义目标。"""
    from infra.external import iam_client

    _iam_env(monkeypatch, OPENOPS_IAM_ACCESS_TOKEN_URL=bad_target)
    with pytest.raises(iam_client.IamError) as exc:
        await iam_client.verify("sid=must-not-leave", "127.0.0.1")
    assert exc.value.status == 502
    assert _FakeIam.calls == {"token": 0, "userinfo": 0}


@pytest.mark.asyncio
async def test_iam_cached_identity_does_not_mask_target_drift(monkeypatch):
    from infra.external import iam_client

    _iam_env(monkeypatch)
    _FakeIam.script = {
        "token": (200, {"code": "201", "access_token": "short-lived-test-token"}),
        "userinfo": (200, {"id": "user-1", "name": "User One"}),
    }
    await iam_client.verify("sid=cached", "127.0.0.1")
    monkeypatch.setenv("OPENOPS_IAM_ACCESS_TOKEN_URL", "https://{host}/token")
    with pytest.raises(iam_client.IamError) as exc:
        await iam_client.verify("sid=cached", "127.0.0.1")
    assert exc.value.status == 502


@pytest.mark.asyncio
async def test_iam_cache_is_bound_to_client_ip(monkeypatch):
    """同 Cookie 换 IP 必须重新打 IAM，不能复用已绑定其他 IP 的身份缓存。"""
    from infra.external import iam_client

    _iam_env(monkeypatch)
    _FakeIam.script = {
        "token": (200, {"code": "201", "access_token": "ip-bound-test-token"}),
        "userinfo": (200, {"id": "user-1", "name": "User One"}),
    }
    await iam_client.verify("sid=ip-bound", "10.20.30.40")
    await iam_client.verify("sid=ip-bound", "10.20.30.40")
    assert _FakeIam.calls["token"] == 1

    _FakeIam.script["token"] = (200, {"code": "403"})
    with pytest.raises(iam_client.IamError) as exc:
        await iam_client.verify("sid=ip-bound", "10.20.30.41")
    assert exc.value.status == 401
    assert _FakeIam.calls["token"] == 2


def test_console_cookie_passthrough_priority(monkeypatch):
    """B9 cookie 三档终版（2026-07-14）：用户透传(缓存优先)最高 > 专属 env > 共享 env——
    env 两档仅本地调试缝，生产不配。"""
    from infra import request_context
    from infra.external.mcp_registry_client import console_cookie

    request_context.clear()
    request_context.set_request_user("l00833445", "iam=user-cookie")
    monkeypatch.setenv("OPENOPS_CONSOLE_COOKIE", "svc-shared")

    # 真实环境唯一正道：透传最高——即使配了专属/共享调试 env 也不生效（2026-07-14 终版）
    monkeypatch.setenv("OPENOPS_OMODEL_COOKIE", "svc-omodel")
    assert console_cookie("OPENOPS_OMODEL_COOKIE") == "iam=user-cookie"
    monkeypatch.delenv("OPENOPS_OMODEL_COOKIE")

    # 缓存反查（无 contextvar 但已知 user_id 的路径）
    assert request_context.cached_user_cookie("l00833445") == "iam=user-cookie"
    assert request_context.cached_user_cookie("nobody") == ""

    # 本地调试（无透传）：专属 env > 共享 env
    request_context.set_request_user("", "")
    request_context.clear()
    monkeypatch.setenv("OPENOPS_OMODEL_COOKIE", "svc-omodel")
    assert console_cookie("OPENOPS_OMODEL_COOKIE") == "svc-omodel"
    monkeypatch.delenv("OPENOPS_OMODEL_COOKIE")
    assert console_cookie("OPENOPS_OMODEL_COOKIE") == "svc-shared"
    monkeypatch.delenv("OPENOPS_CONSOLE_COOKIE")
    assert console_cookie("OPENOPS_OMODEL_COOKIE") == ""


def test_omodel_page_base_host_only(monkeypatch):
    """iframe 页面前缀只取 scheme://host——BASE_URL 带 /omodel 后缀不产双前缀。"""
    from app.workspace_service import console_page_base

    monkeypatch.delenv("OPENOPS_OMODEL_PAGE_URL", raising=False)
    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "https://console.his-op-beta.huawei.com/omodel")
    assert console_page_base() == "https://console.his-op-beta.huawei.com/wesee/omodel/index.html?dataSource=api&workspace="
    monkeypatch.delenv("OPENOPS_OMODEL_BASE_URL")
    assert console_page_base() == ""


def test_omodel_base_rejects_request_derived_host(monkeypatch):
    """携带 IAM Cookie 的 oModel 出站必须固定目标域，不接受请求头派生的 `{host}`。"""
    from app.workspace_service import console_page_base
    from infra.external.omodel_real import _base

    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "https://{host}/omodel")
    monkeypatch.delenv("OPENOPS_OMODEL_PAGE_URL", raising=False)
    assert _base() == ""
    assert console_page_base() == ""
