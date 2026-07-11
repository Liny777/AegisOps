"""外部依赖 real 变体对齐权威契约（29.7 omodel / 29.3 SkillHub·MCPRegistry / 28.2 平台 MCP）。

每个 client 用 monkeypatch httpx.AsyncClient 桩验证：URL/method、请求 body、`{code,message,data}` 信封解包、
字段映射为 OpenOps 词汇。mock 默认路径不受影响（这些 test 显式设 OPENOPS_*=real）。
"""
from __future__ import annotations

from typing import Any


class _Resp:
    def __init__(self, status: int = 200, payload: Any = None, headers: dict | None = None,
                 content: bytes | None = None, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.content = content
        self.text = text  # HTML 检测/raise_with_body 读 .text；默认空串不影响旧用例

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
    import pytest as _pytest

    from infra.external import omodel_real

    monkeypatch.setenv("OPENOPS_OMODEL", "real")
    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "http://umodel:8080")
    monkeypatch.delenv("OPENOPS_OMODEL_TENANT_ID", raising=False)
    md = {"id": "ws-new", "name": "新域", "status": "active",
          "config": {"workspace_ui": {"scopes": ["APP-A", "APP-B"]}}}
    cap = _install(monkeypatch, lambda m, u, k: _Resp(201, md))

    ws = await omodel_real.create_workspace("新域", ["APP-A", "APP-B"])
    body = cap[0][2]["json"]
    assert body["name"] == "新域"
    # 29.7 样例最大兼容面：scopes=string[] 旧格式 + labels/config 的 projectId=首个 appid
    assert body["config"]["workspace_ui"] == {"scopes": ["APP-A", "APP-B"], "projectId": "APP-A"}
    assert body["labels"] == {"projectId": "APP-A"}  # 未设 OPENOPS_OMODEL_TENANT_ID 不带 tenantId
    assert ws["workspace_id"] == "ws-new" and ws["app_ids"] == ["APP-A", "APP-B"]

    monkeypatch.setenv("OPENOPS_OMODEL_TENANT_ID", "huawei")
    cap2 = _install(monkeypatch, lambda m, u, k: _Resp(201, md))
    await omodel_real.create_workspace("新域", ["APP-A"])
    assert cap2[0][2]["json"]["labels"] == {"projectId": "APP-A", "tenantId": "huawei"}

    # 400 必须透出响应体（umodel 错误信封 message 是唯一定位线索；内网 400 教训）
    _install(monkeypatch, lambda m, u, k: _Resp(400, None, text='{"code":"INVALID_ARGUMENT","message":"missing x"}'))
    with _pytest.raises(RuntimeError, match="INVALID_ARGUMENT"):
        await omodel_real.create_workspace("新域", ["APP-A"])


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


# ====================== 应用目录 apptree（"从应用创建系统范围"选源） ======================
import pytest


@pytest.mark.asyncio
async def test_ext_apptree_mock_default_no_http(monkeypatch):
    """默认 mock：不打网、返回内置应用集（前端向导无真环境也能演示平铺选择）。"""
    monkeypatch.delenv("OPENOPS_APPTREE", raising=False)
    from infra.external import apptree_client

    rows = await apptree_client.list_user_apps("0026demo01")
    assert rows and all({"app_id", "name", "type"} <= set(r) for r in rows)


@pytest.mark.asyncio
async def test_ext_apptree_real_maps_and_dedups(monkeypatch):
    """real：POST userid_search_appid，映射 dimension_code/current_name_zh/dimension_type，按 app_id 去重。"""
    monkeypatch.setenv("OPENOPS_APPTREE", "real")
    monkeypatch.setenv("OPENOPS_APPTREE_BASE_URL", "http://wesee")
    monkeypatch.setenv("OPENOPS_APPTREE_ENTERPRISE_ID", "E1")
    monkeypatch.setenv("OPENOPS_APPTREE_PROJECT_ID", "P1")
    monkeypatch.setenv("OPENOPS_APPTREE_USER_ID", "l00833445")
    from infra.external import apptree_client

    payload = {"status": "OK", "data": {"datas": [
        {"dimension_code": "APP-423", "current_name_zh": "日志分析", "dimension_type": "HIS-OP", "role_code": "Reader"},
        {"dimension_code": "APP-423", "current_name_zh": "日志分析", "dimension_type": "HIS-OP", "role_code": "Admin"},  # 同 appid 多角色→去重
        {"dimension_code": "APP-425", "current_name_zh": "统一查询", "dimension_type": "HIS-OP"},
        {"dimension_code": "", "current_name_zh": "空 ID 丢弃"},
    ]}}
    cap = _install(monkeypatch, lambda m, u, k: _Resp(200, payload))
    rows = await apptree_client.list_user_apps("0026demo01")

    method, url, kwargs = cap[0]
    assert method == "POST"
    assert url == "http://wesee/observe/unifieduery/verification/api/v1/E1/P1/userid_search_appid"
    assert kwargs["json"] == {"uesrId": "l00833445"}  # env 覆盖 + 上游拼写 uesrId
    assert rows == [
        {"app_id": "APP-423", "name": "日志分析", "type": "HIS-OP"},
        {"app_id": "APP-425", "name": "统一查询", "type": "HIS-OP"},
    ]


@pytest.mark.asyncio
async def test_ext_apptree_real_requires_base_url(monkeypatch):
    monkeypatch.setenv("OPENOPS_APPTREE", "real")
    monkeypatch.delenv("OPENOPS_APPTREE_BASE_URL", raising=False)
    from infra.external import apptree_client

    with pytest.raises(RuntimeError):
        await apptree_client.list_user_apps("u")


@pytest.mark.asyncio
async def test_ext_console_cookie_shared_fallback(monkeypatch):
    """三面（mcpregistry/omodel/apptree）同一登录态：专属 env 优先，未设回退 OPENOPS_CONSOLE_COOKIE。"""
    import httpx

    from infra.external.mcp_registry_client import _console_headers, console_cookie

    monkeypatch.delenv("OPENOPS_MCPREGISTRY_COOKIE", raising=False)
    monkeypatch.delenv("OPENOPS_OMODEL_COOKIE", raising=False)
    monkeypatch.delenv("OPENOPS_APPTREE_COOKIE", raising=False)
    monkeypatch.setenv("OPENOPS_CONSOLE_COOKIE", "sid=shared-1")
    assert console_cookie("OPENOPS_MCPREGISTRY_COOKIE") == "sid=shared-1"
    assert _console_headers() == {"Cookie": "sid=shared-1"}
    monkeypatch.setenv("OPENOPS_OMODEL_COOKIE", "sid=omodel-own")
    assert console_cookie("OPENOPS_OMODEL_COOKIE") == "sid=omodel-own"  # 专属优先

    # apptree real 出站真的带上共享 cookie
    monkeypatch.setenv("OPENOPS_APPTREE", "real")
    monkeypatch.setenv("OPENOPS_APPTREE_BASE_URL", "http://wesee")
    from infra.external import apptree_client

    seen: dict = {}

    class _Client:
        def __init__(self, *a, **k):
            seen.update(k)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **k):
            return _Resp(200, {"status": "OK", "data": {"datas": []}})

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    await apptree_client.list_user_apps("u")
    assert seen.get("headers", {}).get("Cookie") == "sid=shared-1"


@pytest.mark.asyncio
async def test_ext_apptree_error_envelope_and_html_raise(monkeypatch):
    """联调"空对话框"止血：200+status 非 OK / HTML 响应必须显式报错，不得静默吞成空列表。"""
    monkeypatch.setenv("OPENOPS_APPTREE", "real")
    monkeypatch.setenv("OPENOPS_APPTREE_BASE_URL", "http://wesee")
    from infra.external import apptree_client

    _install(monkeypatch, lambda m, u, k: _Resp(200, {"status": "FAILED", "message": "no permission"}))
    with pytest.raises(RuntimeError, match="status=FAILED"):
        await apptree_client.list_user_apps("u")

    _install(monkeypatch, lambda m, u, k: _Resp(200, None, text="<!doctype html><title>登录</title>"))
    with pytest.raises(RuntimeError, match="HTML"):
        await apptree_client.list_user_apps("u")

    _install(monkeypatch, lambda m, u, k: _Resp(401, None, text="unauthorized"))
    with pytest.raises(RuntimeError, match="401"):
        await apptree_client.list_user_apps("u")


@pytest.mark.asyncio
async def test_ext_apptree_full_url_paste_truncates_and_extracts(monkeypatch):
    """实测坑复现：BASE_URL 贴整条 curl URL → 路径双拼 → 网关 200+status=ERROR。
    现自动截回 host 根并提取 enterprise/project 两段（env 覆盖仍最高优先）。"""
    monkeypatch.setenv("OPENOPS_APPTREE", "real")
    monkeypatch.setenv(
        "OPENOPS_APPTREE_BASE_URL",
        "http://wesee.console.hissit.huawei.com/observe/unifieduery/verification/api/v1/EEE/PPP/userid_search_appid",
    )
    monkeypatch.delenv("OPENOPS_APPTREE_ENTERPRISE_ID", raising=False)
    monkeypatch.delenv("OPENOPS_APPTREE_PROJECT_ID", raising=False)
    monkeypatch.setenv("OPENOPS_APPTREE_USER_ID", "l00833445")
    from infra.external import apptree_client

    payload = {"status": "OK", "data": {"datas": [
        {"dimension_code": "APP-1", "current_name_zh": "应用一", "dimension_type": "HIS-OP"}]}}
    cap = _install(monkeypatch, lambda m, u, k: _Resp(200, payload))
    rows = await apptree_client.list_user_apps("u")

    # 单拼路径（host 根 + 一次模板路径），enterprise/project 用 URL 里提取的两段
    assert cap[0][1] == ("http://wesee.console.hissit.huawei.com"
                         "/observe/unifieduery/verification/api/v1/EEE/PPP/userid_search_appid")
    assert rows == [{"app_id": "APP-1", "name": "应用一", "type": "HIS-OP"}]

    # env 覆盖优先于 URL 提取段
    monkeypatch.setenv("OPENOPS_APPTREE_ENTERPRISE_ID", "E-ENV")
    base, ent, proj = apptree_client._endpoint()
    assert (base, ent, proj) == ("http://wesee.console.hissit.huawei.com", "E-ENV", "PPP")


@pytest.mark.asyncio
async def test_ext_api_prefix_env_overrides(monkeypatch):
    """文根全 env 可覆盖（测试/生产文根不同、对端改文根只改 env 不改码）：
    console 系 OPENOPS_CONSOLE_API_PREFIX、oModel OPENOPS_OMODEL_API_PREFIX、apptree OPENOPS_APPTREE_URL 原样。"""
    from infra.external import apptree_client, omodel_real
    from infra.external.mcp_registry_client import console_api_prefix

    # console 系：默认 29.3 文根；覆盖后 list_servers 实际 URL 跟随
    assert console_api_prefix() == "/obsv/agent/management"
    monkeypatch.setenv("OPENOPS_CONSOLE_API_PREFIX", "/newroot/mgmt/")
    assert console_api_prefix() == "/newroot/mgmt"
    monkeypatch.setenv("OPENOPS_MCPREGISTRY", "real")
    monkeypatch.setenv("OPENOPS_MCPREGISTRY_BASE_URL", "https://console")
    from infra.external import mcp_registry_client

    cap = _install(monkeypatch, lambda m, u, k: _Resp(200, {"code": 0, "data": {"items": [], "total": 0}}))
    await mcp_registry_client.list_servers()
    assert cap[0][1] == "https://console/newroot/mgmt/mcps/list/query"

    # oModel：默认 29.7 文根；覆盖后 resolve URL 跟随
    monkeypatch.setenv("OPENOPS_OMODEL", "real")
    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "http://umodel:8080")
    monkeypatch.setenv("OPENOPS_OMODEL_API_PREFIX", "/omodel-api/v2/workspaces")
    cap2 = _install(monkeypatch, lambda m, u, k: _Resp(200, []))
    await omodel_real.resolve_scope("ws-1", "rev", "u")
    assert cap2[0][1] == "http://umodel:8080/omodel-api/v2/workspaces/ws-1/projects"

    # apptree：OPENOPS_APPTREE_URL 整条原样（最高优先，无视 BASE_URL/模板/两段）
    monkeypatch.setenv("OPENOPS_APPTREE", "real")
    monkeypatch.setenv("OPENOPS_APPTREE_URL", "http://other-env/custom/root/v9/E2/P2/userid_search_appid")
    monkeypatch.setenv("OPENOPS_APPTREE_BASE_URL", "http://ignored")
    payload = {"status": "OK", "data": {"datas": []}}
    cap3 = _install(monkeypatch, lambda m, u, k: _Resp(200, payload))
    await apptree_client.list_user_apps("u")
    assert cap3[0][1] == "http://other-env/custom/root/v9/E2/P2/userid_search_appid"
