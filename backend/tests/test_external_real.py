"""外部依赖 real 变体对齐权威契约（29.7 omodel / 29.3 SkillHub·MCPRegistry / 28.2 平台 MCP）。

每个 client 用 monkeypatch httpx.AsyncClient 桩验证：URL/method、请求 body、`{code,message,data}` 信封解包、
字段映射为 OpenOps 词汇。mock 默认路径不受影响（这些 test 显式设 OPENOPS_*=real）。
"""
from __future__ import annotations

from typing import Any


class _Resp:
    def __init__(self, status: int = 200, payload: Any = None, headers: dict | None = None, content: bytes | None = None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.content = content

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _install(monkeypatch, route):
    """route(method, url, kwargs) -> _Resp。返回 captured list（每次调用的 (method, url, kwargs)）。"""
    import httpx

    captured: list[tuple[str, str, dict]] = []

    class _Client:
        def __init__(self, *a, **k):
            self._base = k.get("base_url") or ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **k):
            captured.append(("GET", self._base + url, k))
            return route("GET", self._base + url, k)

        async def post(self, url, **k):
            captured.append(("POST", self._base + url, k))
            return route("POST", self._base + url, k)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return captured


# ============================ oModel（29.7） ============================

async def test_ext_omodel_resolve_from_projects(monkeypatch):
    """resolve 走 29.5 端点 4 /{ws}/projects：effective_appids = 排序 project_id，scope_revision 私有派生。"""
    from infra.external import omodel_real

    monkeypatch.setenv("OPENOPS_OMODEL", "real")
    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "http://umodel:8080")
    projects = [{"project_id": "APP-B", "name_cn": "b"}, {"project_id": "APP-A", "name_cn": "a"}]
    cap = _install(monkeypatch, lambda m, u, k: _Resp(200, projects))

    res = await omodel_real.resolve_scope("ws-1", "old-rev", "0026demo01")
    assert cap[0][1] == "http://umodel:8080/api/v1/workspaces/ws-1/projects"
    assert res["status"] == "ok"
    assert res["effective_appids"] == ["APP-A", "APP-B"]  # 排序
    assert res["scope_revision"].startswith("sc-") and res["scope_revision"] != "old-rev"


async def test_ext_omodel_resolve_404_and_empty_failclosed(monkeypatch):
    from infra.external import omodel_real

    monkeypatch.setenv("OPENOPS_OMODEL", "real")
    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "http://umodel:8080")

    _install(monkeypatch, lambda m, u, k: _Resp(404, None))
    r404 = await omodel_real.resolve_scope("ws-x", "rev", "u")
    assert r404["status"] == "failed" and r404["effective_appids"] == []

    _install(monkeypatch, lambda m, u, k: _Resp(200, []))  # 空关联项目
    rempty = await omodel_real.resolve_scope("ws-e", "rev", "u")
    assert rempty["status"] == "ok" and rempty["effective_appids"] == []  # scope_service 兜 EMPTY_SCOPE


async def test_ext_omodel_get_and_list_map_metadata(monkeypatch):
    """WorkspaceMetadata（无信封，29.7 snake_case）→ OpenOps 词汇；list 解 Page.items；scopes 两格式兼容。"""
    from infra.external import omodel_real

    monkeypatch.setenv("OPENOPS_OMODEL", "real")
    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "http://umodel:8080")
    md = {"id": "ws-a1", "name": "支付域", "status": "active", "updated_at": "2026-07-10T08:00:00Z",
          "config": {"workspace_ui": {"scopes": [{"projectId": "APP-A"}, {"projectId": "APP-B"}]}}}

    _install(monkeypatch, lambda m, u, k: _Resp(200, md))
    ws = await omodel_real.get_workspace("ws-a1")
    assert ws["workspace_id"] == "ws-a1" and ws["name"] == "支付域"
    assert ws["sync_status"] == "ready" and ws["app_ids"] == ["APP-A", "APP-B"]
    assert ws["scope_revision"].startswith("sc-")
    assert ws["updated"] == "2026-07-10T08:00:00Z"  # 29.7 snake_case updated_at

    _install(monkeypatch, lambda m, u, k: _Resp(200, {"items": [md], "next_token": None}))
    lst = await omodel_real.list_workspaces()
    assert len(lst) == 1 and lst[0]["workspace_id"] == "ws-a1"  # 解 Page.items + 映射

    # 29.7：scopes 旧 string[] 格式（仅 projectId）也要收
    md2 = {"id": "ws-s", "name": "旧格式", "status": "active",
           "config": {"workspace_ui": {"scopes": ["APP-C", "APP-D"]}}}
    _install(monkeypatch, lambda m, u, k: _Resp(200, md2))
    ws2 = await omodel_real.get_workspace("ws-s")
    assert ws2["app_ids"] == ["APP-C", "APP-D"]


async def test_ext_omodel_cookie_and_outbound_hardening(monkeypatch):
    """OPENOPS_OMODEL_COOKIE 设置时带 Cookie；verify/trust_env 与 console 同口径（内网 SWG/证书教训）。"""
    import httpx

    from infra.external import omodel_real

    monkeypatch.setenv("OPENOPS_OMODEL", "real")
    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "http://umodel:8080")
    monkeypatch.setenv("OPENOPS_OMODEL_COOKIE", "sid=umodel-1")
    monkeypatch.setenv("OPENOPS_TLS_INSECURE", "1")
    init_kwargs: dict = {}

    class _Client:
        def __init__(self, *a, **k):
            init_kwargs.update(k)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **k):
            return _Resp(200, [])

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    res = await omodel_real.resolve_scope("ws-1", "rev", "u")
    assert res["status"] == "ok"
    assert init_kwargs["headers"]["Cookie"] == "sid=umodel-1"  # IAM session cookie 注入
    assert init_kwargs["verify"] is False  # TLS 三档（INSECURE 档）生效
    assert init_kwargs["trust_env"] is False  # 默认不信任环境/注册表代理


async def test_ext_omodel_create_workspace_scopes(monkeypatch):
    """create body：app_ids → config.workspace_ui.scopes[].projectId。"""
    from infra.external import omodel_real

    monkeypatch.setenv("OPENOPS_OMODEL", "real")
    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "http://umodel:8080")
    md = {"id": "ws-new", "name": "新域", "status": "active",
          "config": {"workspace_ui": {"scopes": [{"projectId": "APP-A"}]}}}
    cap = _install(monkeypatch, lambda m, u, k: _Resp(201, md))

    ws = await omodel_real.create_workspace("新域", ["APP-A"])
    body = cap[0][2]["json"]
    assert body["name"] == "新域"
    assert body["config"]["workspace_ui"]["scopes"] == [{"projectId": "APP-A"}]
    assert ws["workspace_id"] == "ws-new" and ws["app_ids"] == ["APP-A"]


# ============================ Skill Hub（29.3） ============================

async def test_ext_skillhub_list_unwraps_and_maps(monkeypatch):
    """list_skills：POST /skills/list/query → 解 data.items + 字段映射（is_system→source_type、latest_version→version_no）。"""
    from infra.external import skill_hub_client

    monkeypatch.setenv("OPENOPS_SKILLHUB", "real")
    monkeypatch.setenv("OPENOPS_SKILLHUB_BASE_URL", "http://skillhub")
    body = {"code": 0, "message": "success", "data": {"total": 1, "items": [
        {"skill_id": "inspection", "name": "巡检", "is_system": True, "latest_version": "2.1.0",
         "checksum_sha256": "abc", "source": "openops", "created_by": "sys", "status": "active"}]}}
    cap = _install(monkeypatch, lambda m, u, k: _Resp(200, body))

    rows = await skill_hub_client.list_skills("system")
    assert cap[0][0] == "POST" and cap[0][1].endswith("/obsv/agent/management/skills/list/query")
    r = rows[0]
    assert r["skill_key"] == "inspection" and r["display_name"] == "巡检"
    assert r["source_type"] == "platform" and r["owner_user_id"] == "sys"
    assert r["version_no"] == 20100 and r["checksum_sha256"] == "abc"  # 2.1.0 → 2*10000+1*100+0


async def test_ext_skillhub_list_code_nonzero_raises(monkeypatch):
    from infra.external import skill_hub_client

    monkeypatch.setenv("OPENOPS_SKILLHUB", "real")
    monkeypatch.setenv("OPENOPS_SKILLHUB_BASE_URL", "http://skillhub")
    _install(monkeypatch, lambda m, u, k: _Resp(200, {"code": 5001, "message": "boom", "data": None}))
    try:
        await skill_hub_client.list_skills("system")
        assert False, "code!=0 应 raise"
    except RuntimeError as e:
        assert "5001" in str(e)


async def test_ext_skillhub_download_url_has_skill_id(monkeypatch):
    """download：GET /skills/download?skill_id=（flat + query，省略 version=latest）。"""
    import io
    import zipfile

    from infra.external import skill_hub_client

    monkeypatch.setenv("OPENOPS_SKILLHUB", "real")
    monkeypatch.setenv("OPENOPS_SKILLHUB_BASE_URL", "http://skillhub")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SKILL.md", b"---\nentrypoint: python3 run.py\n---\n")
        z.writestr("run.py", b"print(1)\n")
    cap = _install(monkeypatch, lambda m, u, k: _Resp(200, None, headers={}, content=buf.getvalue()))

    pkg = await skill_hub_client.download_skill_package("inspection", 2)
    assert cap[0][1].endswith("/obsv/agent/management/skills/download")
    assert cap[0][2]["params"] == {"skill_id": "inspection"}  # 省略 version
    assert pkg["entrypoint"] == "python3 run.py"


# ============================ MCP Registry（29.3） ============================

async def test_ext_mcpregistry_discover_unwraps_result_tools(monkeypatch):
    """discover_tools：POST /mcps/proxy {url,method} → 解 data.result.tools。"""
    from infra.external import mcp_registry_client

    monkeypatch.setenv("OPENOPS_MCPREGISTRY", "real")
    monkeypatch.setenv("OPENOPS_MCPREGISTRY_BASE_URL", "http://registry")
    monkeypatch.setenv("OPENOPS_MCP_ROUTE", "proxy")  # 本用例锚定 console proxy 契约（默认已切 direct）
    body = {"code": 0, "message": "ok", "data": {"jsonrpc": "2.0", "id": 1, "result": {"tools": [
        {"name": "query_resource", "description": "查资源", "inputSchema": {"type": "object"}}]}}}
    cap = _install(monkeypatch, lambda m, u, k: _Resp(200, body))

    tools = await mcp_registry_client.discover_tools("http://mcp-server/sse")
    assert cap[0][1].endswith("/obsv/agent/management/mcps/proxy")
    assert cap[0][2]["json"]["url"] == "http://mcp-server/sse"  # proxy 必填 url
    assert cap[0][2]["json"]["method"] == "tools/list"
    assert tools[0]["tool_name"] == "query_resource" and tools[0]["schema_hash"]


async def test_ext_mcpregistry_discover_code_nonzero_raises(monkeypatch):
    from infra.external import mcp_registry_client

    monkeypatch.setenv("OPENOPS_MCPREGISTRY", "real")
    monkeypatch.setenv("OPENOPS_MCPREGISTRY_BASE_URL", "http://registry")
    monkeypatch.setenv("OPENOPS_MCP_ROUTE", "proxy")  # 本用例锚定 console proxy 契约（默认已切 direct）
    _install(monkeypatch, lambda m, u, k: _Resp(200, {"code": 6006, "message": "bad param", "data": None}))
    try:
        await mcp_registry_client.discover_tools("http://x")
        assert False, "code!=0 应 raise"
    except RuntimeError as e:
        assert "6006" in str(e)


# ============================ 平台 MCP + Tool Gateway header（28.2） ============================

async def test_ext_platform_mcp_body_has_tool_name(monkeypatch):
    """call_tool body 同时含 tool_name + arguments（28.2 出站契约）。"""
    from infra.external import http_mcp_client

    monkeypatch.setenv("OPENOPS_MCP", "real")
    monkeypatch.setenv("OPENOPS_MCP_BASE_URL", "http://mcp-gw")
    cap = _install(monkeypatch, lambda m, u, k: _Resp(200, {"request_id": "r1", "status": "ok"}))

    await http_mcp_client.call_tool("query_resource", {"appid": "APP-A"}, headers={"X-OpenOps-User-Id": "u"})
    body = cap[0][2]["json"]
    assert body["tool_name"] == "query_resource" and body["arguments"] == {"appid": "APP-A"}


def test_ext_platform_headers_include_audit_trace(monkeypatch):
    """tool_gateway._platform_headers 含 28.2 X-OpenOps-Audit-Trace-Id。"""
    from runtime.task_registry import TaskState
    from runtime.tool_gateway import _platform_headers

    st = TaskState(task_id="tk1", run_id="run1", user_id="0026demo01", instance_id="inst1", input_text="x")
    st.scope_ctx = {"effective_appids": ["APP-A"], "scope_snapshot_id": "snap1"}
    run = {"framework_session_id": "sess1", "config_version_id": "cfg1", "audit_trace_id": "trace-xyz"}
    h = _platform_headers(st, run)
    assert h["X-OpenOps-Audit-Trace-Id"] == "trace-xyz"
    assert h["X-OpenOps-User-Id"] == "0026demo01" and h["X-OpenOps-Effective-Appids"] == "APP-A"
