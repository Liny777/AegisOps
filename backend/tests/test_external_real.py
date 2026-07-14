"""外部依赖 real 变体对齐权威契约（29.7 omodel / 29.3 SkillHub·MCPRegistry / 28.2 平台 MCP）。

每个 client 用 monkeypatch httpx.AsyncClient 桩验证：URL/method、请求 body、`{code,message,data}` 信封解包、
字段映射为 OpenOps 词汇。mock 默认路径不受影响（这些 test 显式设 OPENOPS_*=real）。
"""
from __future__ import annotations

from typing import Any

import pytest


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
    """create body 镜像 umodel **新版** UI 实抓包（2026-07-11 对端部署"id 服务端生成"后）：
    id 不传；labels/workspace_ui 的 tenantId=当前企业（与 scope 顺序无关，env 覆盖优先）且**不带
    projectId**（服务端自回填）；scopes=object[]{projectId,projectCn,tenantId}（per-项目租户）；
    status:running + owner。"""
    import pytest as _pytest

    from infra.external import omodel_real

    monkeypatch.setenv("OPENOPS_OMODEL", "real")
    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "http://umodel:8080")
    monkeypatch.delenv("OPENOPS_OMODEL_TENANT_ID", raising=False)
    monkeypatch.setenv("OPENOPS_APPTREE_ENTERPRISE_ID", "T-CURRENT")
    monkeypatch.delenv("OPENOPS_APPTREE_URL", raising=False)
    monkeypatch.delenv("OPENOPS_APPTREE_BASE_URL", raising=False)
    md = {"id": "l0001-ws-d5d17150", "name": "pay 域", "status": "active",
          "config": {"workspace_ui": {"scopes": [{"projectId": "APP-A", "projectCn": "应用甲"},
                                                 {"projectId": "APP-B", "projectCn": "APP-B"}]}}}
    cap = _install(monkeypatch, lambda m, u, k: _Resp(201, md))

    ws = await omodel_real.create_workspace(
        "pay 域", ["APP-A", "APP-B"],
        apps=[{"app_id": "APP-A", "name": "应用甲", "tenant_id": "T-1111"},
              {"app_id": "APP-B", "name": "", "tenant_id": "T-CURRENT"}],
        owner="林一")
    body = cap[0][2]["json"]
    assert body["name"] == "pay 域" and body["description"] == ""
    assert "id" not in body  # 拍板：id 由 umodel 服务端生成（{W3}-ws-{hex8}），调用方不传
    assert body["labels"] == {"tenantId": "T-CURRENT"}  # 当前企业；不带 projectId（服务端自回填）
    assert body["config"]["workspace_ui"] == {
        "tenantId": "T-CURRENT",
        "scopes": [{"projectId": "APP-A", "projectCn": "应用甲", "tenantId": "T-1111"},
                   {"projectId": "APP-B", "projectCn": "APP-B", "tenantId": "T-CURRENT"}],
        "status": "running", "owner": "林一",
    }
    assert ws["workspace_id"] == "l0001-ws-d5d17150" and ws["app_ids"] == ["APP-A", "APP-B"]

    # oModel tenant env 覆盖 AppTree 当前企业；未传 owner 不带
    monkeypatch.setenv("OPENOPS_OMODEL_TENANT_ID", "T-ENV")
    cap2 = _install(monkeypatch, lambda m, u, k: _Resp(201, md))
    await omodel_real.create_workspace("观测联调域", ["APP-A"])
    b2 = cap2[0][2]["json"]
    assert "id" not in b2 and "projectId" not in b2["labels"] if isinstance(b2["labels"], dict) else True
    assert b2["labels"]["tenantId"] == "T-ENV" and b2["config"]["workspace_ui"]["tenantId"] == "T-ENV"
    assert b2["config"]["workspace_ui"]["scopes"] == [{"projectId": "APP-A", "projectCn": "APP-A"}]
    assert "owner" not in b2["config"]["workspace_ui"]

    monkeypatch.delenv("OPENOPS_OMODEL_TENANT_ID", raising=False)
    monkeypatch.delenv("OPENOPS_APPTREE_ENTERPRISE_ID", raising=False)
    cap3 = _install(monkeypatch, lambda m, u, k: _Resp(201, md))
    await omodel_real.create_workspace("无租户信息域", ["APP-C"])
    from infra.external.apptree_client import _DEFAULT_ENTERPRISE
    assert cap3[0][2]["json"]["labels"]["tenantId"] == _DEFAULT_ENTERPRISE

    # 400 结构化为 validation，并只提取已知 code/message 字段
    from infra.external.omodel_client import OModelError

    _install(monkeypatch, lambda m, u, k: _Resp(400, None, text='{"code":"INVALID_ARGUMENT","message":"missing x"}'))
    with _pytest.raises(OModelError, match="INVALID_ARGUMENT") as exc:
        await omodel_real.create_workspace("新域", ["APP-A"])
    assert exc.value.kind == "validation" and exc.value.status_code == 400


@pytest.mark.parametrize(
    ("status", "payload", "expected_kind"),
    [
        (401, {"error": {"code": "Auth401", "message": "Not logged in"}}, "auth"),
        (403, {"error": {"code": "Auth403", "message": "Forbidden"}}, "auth"),
        (422, {"code": "INVALID_ARGUMENT", "message": "bad scope"}, "validation"),
        (503, {"code": "UNAVAILABLE", "message": "maintenance"}, "upstream"),
    ],
)
async def test_ext_omodel_create_classifies_http_errors(monkeypatch, status, payload, expected_kind):
    from infra.external import omodel_real
    from infra.external.omodel_client import OModelError

    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "https://console.example/omodel")
    monkeypatch.setenv("OPENOPS_OMODEL_TENANT_ID", "T-CURRENT")
    _install(monkeypatch, lambda m, u, k: _Resp(status, payload, headers={"X-Request-Id": "up-123"}))

    with pytest.raises(OModelError) as exc:
        await omodel_real.create_workspace("新域", ["APP-A"])
    assert exc.value.kind == expected_kind
    assert exc.value.status_code == status
    assert exc.value.request_id == "up-123"


async def test_ext_omodel_sanitizes_upstream_request_id(monkeypatch):
    from infra.external import omodel_real
    from infra.external.omodel_client import OModelError

    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "https://console.example/omodel")
    monkeypatch.setenv("OPENOPS_OMODEL_TENANT_ID", "T-CURRENT")
    _install(monkeypatch, lambda m, u, k: _Resp(
        503, {"code": "UNAVAILABLE"}, headers={"X-Request-Id": "up-1\r\nforged-log"}))

    with pytest.raises(OModelError) as exc:
        await omodel_real.create_workspace("新域", ["APP-A"])
    assert exc.value.request_id == "up-1forged-log"


async def test_ext_omodel_list_debug_never_logs_payload(monkeypatch, caplog):
    import logging

    from infra.external import omodel_real

    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "https://console.example/omodel")
    monkeypatch.setenv("OPENOPS_HTTP_DEBUG", "1")
    _install(monkeypatch, lambda m, u, k: _Resp(
        200, {"items": [], "sensitive_marker": "must-not-reach-log"},
        headers={"X-Request-Id": "list-1"}))
    caplog.set_level(logging.WARNING, logger="openops.omodel")

    assert await omodel_real.list_workspaces() == []
    assert "status=200" in caplog.text and "request_id=list-1" in caplog.text
    assert "must-not-reach-log" not in caplog.text


async def test_ext_omodel_create_classifies_timeout_and_network(monkeypatch):
    import httpx

    from infra.external import omodel_real
    from infra.external.omodel_client import OModelError

    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "https://console.example/omodel")
    monkeypatch.setenv("OPENOPS_OMODEL_TENANT_ID", "T-CURRENT")
    for failure, expected_kind in ((httpx.ReadTimeout("slow"), "timeout"),
                                   (httpx.ConnectError("down"), "upstream")):
        def _raise(_m, _u, _k, error=failure):
            raise error

        _install(monkeypatch, _raise)
        with pytest.raises(OModelError) as exc:
            await omodel_real.create_workspace("新域", ["APP-A"])
        assert exc.value.kind == expected_kind
        assert exc.value.status_code is None


# ============================ Skill Hub（29.3） ============================

async def test_ext_skillhub_list_unwraps_and_maps(monkeypatch):
    """list_skills：POST /skills/list/query → 解 data.items + 字段映射（is_system→source_type、latest_version→version_no）。"""
    from infra.external import skill_hub_client

    monkeypatch.setenv("OPENOPS_SKILLHUB", "real")
    monkeypatch.setenv("OPENOPS_SKILLHUB_BASE_URL", "http://skillhub")
    body = {"code": 200, "message": "success", "data": {"total": 1, "items": [
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
    body = {"code": 200, "message": "ok", "data": {"jsonrpc": "2.0", "id": 1, "result": {"tools": [
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
        {"dimension_code": "APP-423", "current_name_zh": "日志分析", "dimension_type": "HIS-OP", "role_code": "Reader",
         "tenant_id": "T-8888"},
        {"dimension_code": "APP-423", "current_name_zh": "日志分析", "dimension_type": "HIS-OP", "role_code": "Admin",
         "tenant_id": "T-8888"},  # 同 appid 多角色→去重
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
        {"app_id": "APP-423", "name": "日志分析", "type": "HIS-OP", "tenant_id": "T-8888"},
        {"app_id": "APP-425", "name": "统一查询", "type": "HIS-OP", "tenant_id": ""},
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
    assert rows == [{"app_id": "APP-1", "name": "应用一", "type": "HIS-OP", "tenant_id": ""}]

    # env 覆盖优先于 URL 提取段
    monkeypatch.setenv("OPENOPS_APPTREE_ENTERPRISE_ID", "E-ENV")
    base, ent, proj = apptree_client._endpoint()
    assert (base, ent, proj) == ("http://wesee.console.hissit.huawei.com", "E-ENV", "PPP")


def test_ext_apptree_current_enterprise_precedence(monkeypatch):
    from infra.external import apptree_client

    monkeypatch.delenv("OPENOPS_APPTREE_ENTERPRISE_ID", raising=False)
    monkeypatch.delenv("OPENOPS_APPTREE_URL", raising=False)
    monkeypatch.delenv("OPENOPS_APPTREE_BASE_URL", raising=False)
    assert apptree_client.current_enterprise_id() == apptree_client._DEFAULT_ENTERPRISE

    monkeypatch.setenv(
        "OPENOPS_APPTREE_BASE_URL",
        "https://base.example/observe/unifieduery/verification/api/v1/E-BASE/P-BASE/userid_search_appid",
    )
    assert apptree_client.current_enterprise_id() == "E-BASE"

    # 生效的 verbatim URL 优先于 BASE，且兼容自定义文根
    monkeypatch.setenv("OPENOPS_APPTREE_URL", "https://api.example/custom/v9/E-URL/P-URL/userid_search_appid")
    assert apptree_client.current_enterprise_id() == "E-URL"

    monkeypatch.setenv("OPENOPS_APPTREE_ENTERPRISE_ID", "E-ENV")
    assert apptree_client.current_enterprise_id() == "E-ENV"


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


@pytest.mark.asyncio
async def test_ext_skillhub_cookie_base_fallback_and_html_guard(monkeypatch):
    """Skill Hub 接真三护栏：cookie（专属>共享）随请求带出；BASE_URL 未配回退 MCPREGISTRY 同 console 网关；
    下载响应是 HTML（登录页）→ 显式报错而非 BadZipFile。"""
    import pytest as _pytest

    from infra.external import skill_hub_client

    monkeypatch.setenv("OPENOPS_SKILLHUB", "real")
    monkeypatch.delenv("OPENOPS_SKILLHUB_BASE_URL", raising=False)
    monkeypatch.setenv("OPENOPS_MCPREGISTRY_BASE_URL", "https://console")  # 回退源
    monkeypatch.delenv("OPENOPS_SKILLHUB_COOKIE", raising=False)
    monkeypatch.setenv("OPENOPS_CONSOLE_COOKIE", "sid=shared-9")

    body = {"code": 0, "message": "ok", "data": {"items": []}}
    cap = _install(monkeypatch, lambda m, u, k: _Resp(200, body))
    await skill_hub_client.list_skills("u1")
    method, url, kwargs = cap[0]
    assert url == "https://console/obsv/agent/management/skills/list/query"  # base 回退 + console 文根
    assert kwargs["headers"] == {"Cookie": "sid=shared-9"}  # 共享 cookie 回退带出

    # 成功码兼容：内网实测 skills 面成功返回 code=200/success（29.3 文档写 0，mcps 面实测 0）——两收
    body200 = {"code": 200, "message": "success", "data": {"items": [
        {"skill_id": "inspection", "name": "巡检", "is_system": True, "latest_version": "2.0.0",
         "checksum_sha256": "abc", "source": "openops", "created_by": "sys", "status": "active"}]}}
    _install(monkeypatch, lambda m, u, k: _Resp(200, body200))
    rows = await skill_hub_client.list_skills("u1")
    assert rows and rows[0]["skill_key"] == "inspection"

    # 下载：HTML 响应显式报错（cookie 失效场景）
    class _HtmlResp(_Resp):
        def __init__(self):
            super().__init__(200, None, headers={}, text="<html>login</html>")
            self.content = b"<html>login page</html>"

    _install(monkeypatch, lambda m, u, k: _HtmlResp())
    with _pytest.raises(RuntimeError, match="HTML"):
        await skill_hub_client.download_skill_package("inspection", 2)


@pytest.mark.asyncio
async def test_ext_skillhub_download_json_envelope_explicit(monkeypatch):
    """下载响应是 JSON 信封而非裸 ZIP（29.3 §2.5 约定直接回 ZIP 字节）→ 显式报错带信封内容，
    不再退化成难懂的 BadZipFile（内网头号嫌疑，拿到真实形状后再适配）。"""
    import pytest as _pytest

    from infra.external import skill_hub_client

    monkeypatch.setenv("OPENOPS_SKILLHUB", "real")
    monkeypatch.setenv("OPENOPS_SKILLHUB_BASE_URL", "https://console")

    class _JsonResp(_Resp):
        def __init__(self):
            super().__init__(200, None, text="")
            self.content = b'{"code":200,"message":"success","data":{"download_url":"..."}}'

    _install(monkeypatch, lambda m, u, k: _JsonResp())
    with _pytest.raises(RuntimeError, match="JSON 信封"):
        await skill_hub_client.download_skill_package("alarm-query", 1)


@pytest.mark.asyncio
async def test_ext_omodel_outbound_headers(monkeypatch, caplog):
    """oModel 同时携带登录态、浏览器 UA，以及从 target base 派生的 CSRF 同源头。"""
    monkeypatch.setenv("OPENOPS_OMODEL", "real")
    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "https://console.example:8443/omodel")
    monkeypatch.delenv("OPENOPS_OMODEL_COOKIE", raising=False)
    monkeypatch.delenv("OPENOPS_CONSOLE_COOKIE", raising=False)
    monkeypatch.setenv("OPENOPS_HTTP_DEBUG", "1")
    monkeypatch.setenv("OPENOPS_UNSAFE_LOG_COOKIE", "1")  # 即使误设也绝不记录凭据
    import logging

    caplog.set_level(logging.WARNING, logger="openops.omodel")
    from infra import request_context
    from infra.external.omodel_real import _client_kwargs, _base

    request_context.set_request_user("u1", "sid=user-session")
    request_context.set_request_host("frontend.example")
    request_context.set_client_ip("10.20.30.40")
    headers = _client_kwargs(_base()).get("headers", {})
    assert headers.get("Cookie") == "sid=user-session"  # 用户透传
    assert "Mozilla/5.0" in headers.get("User-Agent", "")  # 浏览器 UA
    assert headers.get("Origin") == "https://console.example:8443"
    assert headers.get("Referer") == "https://console.example:8443/"
    assert headers.get("Sec-Fetch-Site") == "same-origin"
    assert headers.get("Origin") != "https://frontend.example"
    assert "sid=user-session" not in caplog.text
    assert "10.20.30.40" not in caplog.text  # 客户端 IP 也不属于允许日志字段
    request_context.set_request_user("", "")
    request_context.set_request_host("")
    request_context.set_client_ip("")


@pytest.mark.asyncio
async def test_ext_omodel_response_hook_never_logs_payload(caplog):
    """真实执行 response hook，锁定 URL/header/body 中的凭据均不进入调试日志。"""
    import logging
    from types import SimpleNamespace

    from infra.external.omodel_real import _log_outbound_response

    caplog.set_level(logging.WARNING, logger="openops.omodel")
    response = SimpleNamespace(
        status_code=401,
        headers={"X-Request-Id": "upstream-req-1"},
        text='{"access_token":"response-secret"}',
        content=b'{"access_token":"response-secret"}',
        request=SimpleNamespace(
            url="https://user:password@console.example/omodel?token=url-secret",
            headers={"Cookie": "sid=request-secret"},
            content=b'{"credential":"request-body-secret"}',
        ),
    )
    await _log_outbound_response(response)
    assert "status=401" in caplog.text and "request_id=upstream-req-1" in caplog.text
    for secret in ("response-secret", "request-secret", "url-secret", "request-body-secret", "password"):
        assert secret not in caplog.text


def test_ext_omodel_rejects_credentialed_or_ambiguous_base():
    from infra.external.omodel_client import OModelError
    from infra.external.omodel_real import _client_kwargs

    for base in (
        "https://user:secret@console.example/omodel",
        "https://console.example/omodel?redirect=elsewhere",
        "https://console.example/omodel#fragment",
        "https:///omodel",
        "https://{host}/omodel",
        "https://console.example/{tenant}/omodel",
        "https://%7Bhost%7D/omodel",
        "https://bad_host.example/omodel",
    ):
        with pytest.raises(OModelError) as exc:
            _client_kwargs(base)
        assert exc.value.kind == "config"


def test_ext_omodel_list_multi_shape():
    """omodel list 响应多形状兼容：{items}/裸数组/{data:{items}}/{data:[]}/{workspaces}。"""
    from infra.external.omodel_real import _extract_ws_items

    a, b = {"id": "w1"}, {"id": "w2"}
    assert _extract_ws_items({"items": [a, b]}) == [a, b]
    assert _extract_ws_items([a, b]) == [a, b]
    assert _extract_ws_items({"data": {"items": [a]}}) == [a]
    assert _extract_ws_items({"data": [a, b]}) == [a, b]
    assert _extract_ws_items({"workspaces": [a]}) == [a]
    assert _extract_ws_items({"total": 0}) == []
    assert _extract_ws_items(None) == []


def _release_env_file(tmp_path, *, omit=(), **overrides):
    """生成不含真实凭据的生产门禁输入；避免测试依赖开发机 OPENOPS_* 环境。"""
    import shlex

    values = {
        "OPENOPS_ENCRYPTION_KEY": "A" * 43 + "=",
        "OPENOPS_PLATFORM_GLM_API_KEY": "fake-model-key-at-least-16",
        "OPENOPS_IAM_ENABLED": "true",
        "OPENOPS_IAM_ACCESS_TOKEN_URL": "https://iam.example/token",
        "OPENOPS_IAM_USERINFO_URL": "https://iam.example/userinfo",
        "OPENOPS_OMODEL": "real",
        "OPENOPS_OMODEL_BASE_URL": "https://console.example/omodel",
        "OPENOPS_OMODEL_TENANT_ID": "8888888888888888",
        "OPENOPS_APPTREE": "real",
        "OPENOPS_APPTREE_ENTERPRISE_ID": "8888888888888888",
        "OPENOPS_APPTREE_BASE_URL": "http://apptree.internal/root",
        "OPENOPS_HTTP_DEBUG": "0",
        "OPENOPS_TLS_INSECURE": "0",
        "OPENOPS_HTTP_TRUST_ENV": "0",
    }
    values.update(overrides)
    for key in omit:
        values.pop(key, None)
    path = tmp_path / "production.env"
    path.write_text("\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items()) + "\n")
    return path


def _run_release_gate(env_file, ambient=None):
    import os
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    clean_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(root)),
        "LC_ALL": "C",
    }
    clean_env.update(ambient or {})
    return subprocess.run(
        ["bash", "scripts/release_check.sh", "--production-env", str(env_file)],
        cwd=root, env=clean_env, text=True, capture_output=True, check=False,
    )


def test_release_gate_accepts_fixed_iam_targets(tmp_path):
    result = _run_release_gate(_release_env_file(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("variable", ["OPENOPS_IAM_ACCESS_TOKEN_URL", "OPENOPS_IAM_USERINFO_URL"])
@pytest.mark.parametrize("bad_target", [
    "http://iam.example/iam",
    "https://{host}/iam",
    "https://user:secret@iam.example/iam",
    "https://bad_host.example/iam",
    "https://iam.example:bad/iam",
    "https://iam.example\\@attacker.example/iam",
    "https://%7Bhost%7D/iam",
])
def test_release_gate_rejects_unsafe_iam_targets(tmp_path, variable, bad_target):
    result = _run_release_gate(_release_env_file(tmp_path, **{variable: bad_target}))
    assert result.returncode != 0
    assert f"{variable} 必须是" in result.stdout


def test_release_gate_accepts_fixed_ipv6_iam_target(tmp_path):
    result = _run_release_gate(_release_env_file(
        tmp_path, OPENOPS_IAM_ACCESS_TOKEN_URL="https://[2001:db8::1]/token"))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("missing", [
    "OPENOPS_IAM_ACCESS_TOKEN_URL",
    "OPENOPS_IAM_USERINFO_URL",
    "OPENOPS_OMODEL_TENANT_ID",
])
def test_release_gate_does_not_inherit_openops_values(tmp_path, missing):
    """目标 env 漏项时，即使调用者导出了同名变量也必须失败。"""
    if missing.endswith("URL"):
        ambient_value = "https://iam.example/ambient"
    else:
        ambient_value = "8888888888888888"
    env_file = _release_env_file(tmp_path, omit=(missing,))
    result = _run_release_gate(env_file, ambient={missing: ambient_value})
    assert result.returncode != 0
    assert f"{missing} 未配置" in result.stdout




def test_ext_omodel_sends_client_ip(monkeypatch):
    """华为 IAM 会话绑 IP：omodel 出站带 IAM-Client-Ip + X-Forwarded-For（用户真实浏览器 IP）。"""
    monkeypatch.setenv("OPENOPS_OMODEL", "real")
    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "https://console.example/omodel")
    from infra import request_context
    from infra.external.omodel_real import _client_kwargs, _base

    request_context.set_client_ip("10.20.30.40")
    headers = _client_kwargs(_base()).get("headers", {})
    assert headers.get("IAM-Client-Ip") == "10.20.30.40"
    assert headers.get("X-Forwarded-For") == "10.20.30.40"
    request_context.set_client_ip("")
    headers2 = _client_kwargs(_base()).get("headers", {})
    assert "IAM-Client-Ip" not in headers2  # 无 IP 上下文不硬塞
