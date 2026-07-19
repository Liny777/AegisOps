"""动态 MCP 工具装配（真注册表发现 → agent 工具，direct streamable-HTTP / console proxy 双路由）单元测试。"""
from __future__ import annotations

import asyncio
import json as _json

from infra.external import http_mcp_client, mcp_registry_client
from runtime import agentscope_runtime as ar


class _Resp:
    def __init__(self, payload: dict | None = None, *, text: str | None = None,
                 content_type: str = "application/json", status: int = 200) -> None:
        self._p = payload or {}
        self.status_code = status  # raise_with_body 读 status_code/text
        self.text = text if text is not None else _json.dumps(self._p, ensure_ascii=False)
        self.headers = {"content-type": content_type, "Trace-Id": "tr-test-1"}

    def raise_for_status(self) -> None:  # noqa: D401
        pass

    def json(self) -> dict:
        return self._p


class _FakeClient:
    """按 body.method 路由的多次调用桩（MCP 握手后一次业务调用 = 3 个 POST）：
    initialize → 200+Mcp-Session-Id 头；notifications/initialized → 202；其余=业务调用
    （tools/list、tools/call、proxy 信封、legacy）落 captured（兼容旧断言=最后一次业务调用）。"""

    captured: dict = {}
    calls: list = []  # 全部调用按序 {url,json,headers}
    init_kwargs: dict = {}
    resp: _Resp | None = None  # 业务调用可注入自定义响应
    session_id: str | None = "sess-1"  # initialize 响应头会话 id（None=stateless 不带头）
    expire_once: bool = False  # 模拟会话过期：下一个业务调用回 404 一次

    def __init__(self, *a, **k) -> None:
        _FakeClient.init_kwargs = dict(k)

    @classmethod
    def reset(cls) -> None:
        cls.captured, cls.calls, cls.resp = {}, [], None
        cls.session_id, cls.expire_once = "sess-1", False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeClient.calls.append({"url": url, "json": json, "headers": dict(headers or {})})
        method = (json or {}).get("method")
        if method == "initialize":
            r = _Resp({"jsonrpc": "2.0", "id": 0, "result": {"capabilities": {}}})
            if _FakeClient.session_id:
                r.headers["Mcp-Session-Id"] = _FakeClient.session_id
            return r
        if method == "notifications/initialized":
            return _Resp({}, status=202)
        _FakeClient.captured = {"url": url, "json": json, "headers": headers}
        if _FakeClient.expire_once:
            _FakeClient.expire_once = False
            return _Resp({"error": "session expired"}, status=404)
        if _FakeClient.resp is not None:
            return _FakeClient.resp
        return _Resp({"code": 0, "data": {"result": {
            "structuredContent": {"result": "告警3条：A/B/C"}, "isError": False}}})


def _inits(calls: list) -> list:
    return [c for c in calls if (c["json"] or {}).get("method") == "initialize"]


def test_mcp_direct_call_streamable_http(monkeypatch):
    """默认 direct 路由：initialize 握手（取 Mcp-Session-Id）→ tools/call 直连 server_url，
    JSON-RPC 信封 + Accept SSE；28.2 头照带、不带 console cookie。"""
    monkeypatch.setenv("OPENOPS_MCP", "real")
    monkeypatch.setenv("OPENOPS_MCPREGISTRY_COOKIE", "sid=abc123")  # 有 cookie 也不该带到 mcpgateway
    monkeypatch.delenv("OPENOPS_MCP_ROUTE", raising=False)  # 默认 direct
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    _FakeClient.reset()
    http_mcp_client._sessions.clear()
    sse = ('event: message\n'
           'data: {"jsonrpc":"2.0","id":1,"result":{"structuredContent":{"result":"告警3条：A/B/C"},"isError":false}}\n')
    _FakeClient.resp = _Resp(text=sse, content_type="text/event-stream")
    try:
        r = asyncio.run(http_mcp_client.call_tool(
            "query_alarm_list", {"project_id": "APP-REAL-1"},
            headers={"X-OpenOps-Effective-Appids": "APP-REAL-1"}, server_url="http://mcpgw/x"))
    finally:
        _FakeClient.resp = None
    # 首个调用必须是 initialize 握手（严格 stateful server 兼容，2026-07-14 内网实测）
    first = _FakeClient.calls[0]["json"]
    assert first["method"] == "initialize" and first["id"] == 0
    assert first["params"]["protocolVersion"] == "2025-03-26" and first["params"]["clientInfo"]["name"] == "openops"
    cap = _FakeClient.captured
    assert cap["url"] == "http://mcpgw/x"  # 直连 server_url，不经 console
    assert cap["json"] == {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "query_alarm_list", "arguments": {"project_id": "APP-REAL-1"}}}
    assert cap["headers"]["Accept"] == "application/json, text/event-stream"
    assert cap["headers"]["Mcp-Session-Id"] == "sess-1"  # 握手取到的会话头带到业务调用
    assert cap["headers"]["X-OpenOps-Effective-Appids"] == "APP-REAL-1"  # 28.2 头照带
    assert "Cookie" not in cap["headers"]  # console cookie 不外泄给 mcpgateway
    assert r["result_summary"] == "告警3条：A/B/C"  # SSE data 行解析 + structuredContent 抽取
    assert r["request_id"] == "tr-test-1"  # mcpgateway Trace-Id 作外部请求号


def test_mcp_direct_discover_streamable_http(monkeypatch):
    monkeypatch.setenv("OPENOPS_MCPREGISTRY", "real")
    monkeypatch.delenv("OPENOPS_MCP_ROUTE", raising=False)
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    _FakeClient.reset()
    sse = ('event: message\n'
           'data: {"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"query_alarm_list","description":"d",'
           '"inputSchema":{"type":"object","properties":{"project_id":{"type":"string"}}},'
           '"annotations":{"readOnlyHint":true}}]}}\n')
    _FakeClient.resp = _Resp(text=sse, content_type="text/event-stream")
    try:
        tools = asyncio.run(mcp_registry_client.discover_tools("http://mcpgw/x"))
    finally:
        _FakeClient.resp = None
    assert _FakeClient.calls[0]["json"]["method"] == "initialize"  # 发现面同样先握手
    cap = _FakeClient.captured
    assert cap["url"] == "http://mcpgw/x"
    assert cap["json"]["method"] == "tools/list" and cap["json"]["jsonrpc"] == "2.0"
    assert cap["headers"]["Mcp-Session-Id"] == "sess-1"
    assert tools[0]["tool_name"] == "query_alarm_list" and tools[0]["readonly"] is True


def test_mcp_session_cache_reuse(monkeypatch):
    """会话缓存：同 server 第二次 tools/call 不再握手（省 2 往返），会话头照带。"""
    monkeypatch.setenv("OPENOPS_MCP", "real")
    monkeypatch.delenv("OPENOPS_MCP_ROUTE", raising=False)
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    _FakeClient.reset()
    http_mcp_client._sessions.clear()
    asyncio.run(http_mcp_client.call_tool("t1", {}, server_url="http://mcpgw/x"))
    asyncio.run(http_mcp_client.call_tool("t2", {}, server_url="http://mcpgw/x"))
    assert len(_inits(_FakeClient.calls)) == 1  # 只握手一次
    assert _FakeClient.captured["headers"]["Mcp-Session-Id"] == "sess-1"  # 第二次直发带缓存会话


def test_mcp_session_expired_rehandshake(monkeypatch):
    """缓存会话被 server 判失效（404）→ 清缓存重握手重试一次成功。"""
    monkeypatch.setenv("OPENOPS_MCP", "real")
    monkeypatch.delenv("OPENOPS_MCP_ROUTE", raising=False)
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    _FakeClient.reset()
    http_mcp_client._sessions.clear()
    asyncio.run(http_mcp_client.call_tool("t1", {}, server_url="http://mcpgw/x"))  # 建缓存
    _FakeClient.calls = []
    _FakeClient.expire_once = True  # 下一个业务调用 404
    r = asyncio.run(http_mcp_client.call_tool("t2", {}, server_url="http://mcpgw/x"))
    assert r["status"] == "ok"  # 重握手重试后成功
    methods = [(c["json"] or {}).get("method") for c in _FakeClient.calls]
    # 序列：tools/call(404) → initialize → notifications/initialized → tools/call(ok)
    assert methods == ["tools/call", "initialize", "notifications/initialized", "tools/call"]


def test_mcp_stateless_server_no_session_header(monkeypatch):
    """stateless server（initialize 响应无 Mcp-Session-Id）：后续调用不带会话头照常工作。"""
    monkeypatch.setenv("OPENOPS_MCP", "real")
    monkeypatch.delenv("OPENOPS_MCP_ROUTE", raising=False)
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    _FakeClient.reset()
    _FakeClient.session_id = None
    http_mcp_client._sessions.clear()
    r = asyncio.run(http_mcp_client.call_tool("t1", {}, server_url="http://mcpgw/x"))
    assert r["status"] == "ok"
    assert "Mcp-Session-Id" not in _FakeClient.captured["headers"]


def test_mcp_real_call_routes_via_proxy_and_preserves_28_2_headers(monkeypatch):
    monkeypatch.setenv("OPENOPS_MCP", "real")
    monkeypatch.setenv("OPENOPS_MCP_ROUTE", "proxy")  # 显式走 console proxy 路由（direct 是默认）
    monkeypatch.setenv("OPENOPS_MCPREGISTRY_BASE_URL", "https://console.x")
    monkeypatch.setenv("OPENOPS_MCPREGISTRY_COOKIE", "sid=abc123")
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    r = asyncio.run(http_mcp_client.call_tool(
        "query_alarm_list", {"project_id": "APP-REAL-1"},
        headers={"X-OpenOps-Effective-Appids": "APP-REAL-1"}, server_url="http://mcpgw/x"))
    cap = _FakeClient.captured
    assert cap["url"].endswith("/obsv/agent/management/mcps/proxy")
    assert cap["json"] == {"url": "http://mcpgw/x", "method": "tools/call",
                           "params": {"name": "query_alarm_list", "arguments": {"project_id": "APP-REAL-1"}}}
    assert cap["headers"]["X-OpenOps-Effective-Appids"] == "APP-REAL-1"  # 28.2 头原样透传
    assert cap["headers"]["Cookie"] == "sid=abc123"  # console 鉴权 cookie 注入
    assert r["result_summary"] == "告警3条：A/B/C"  # fastmcp structuredContent.result 抽取


def test_console_tls_insecure_switch(monkeypatch):
    """OPENOPS_TLS_INSECURE=1 → console httpx 用 verify=False（内网证书不在 certifi 时的联调临时档）。"""
    monkeypatch.setenv("OPENOPS_MCP", "real")
    monkeypatch.setenv("OPENOPS_MCP_ROUTE", "proxy")
    monkeypatch.setenv("OPENOPS_MCPREGISTRY_BASE_URL", "https://console.x")
    monkeypatch.setenv("OPENOPS_TLS_INSECURE", "1")
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    asyncio.run(http_mcp_client.call_tool("t", {}, server_url="http://mcpgw/x"))
    assert _FakeClient.init_kwargs.get("verify") is False
    assert _FakeClient.init_kwargs.get("trust_env") is False  # 默认不信任环境/注册表代理（内网直连）
    monkeypatch.setenv("OPENOPS_TLS_CA_FILE", "/etc/corp-ca.pem")  # CA 文件档优先于 insecure
    monkeypatch.setenv("OPENOPS_HTTP_TRUST_ENV", "1")
    asyncio.run(http_mcp_client.call_tool("t", {}, server_url="http://mcpgw/x"))
    assert _FakeClient.init_kwargs.get("verify") == "/etc/corp-ca.pem"
    assert _FakeClient.init_kwargs.get("trust_env") is True


def test_console_discovery_sends_cookie(monkeypatch):
    monkeypatch.setenv("OPENOPS_MCPREGISTRY", "real")
    monkeypatch.setenv("OPENOPS_MCPREGISTRY_BASE_URL", "https://console.x")
    monkeypatch.setenv("OPENOPS_MCPREGISTRY_COOKIE", "sid=xyz")
    import httpx

    class _Srv(_FakeClient):
        async def post(self, url, json=None, headers=None):
            _FakeClient.captured = {"url": url, "json": json, "headers": headers}
            return _Resp({"code": 0, "data": {"items": [], "total": 0}})

    monkeypatch.setattr(httpx, "AsyncClient", _Srv)
    asyncio.run(mcp_registry_client.list_servers())
    # console 统一装配后 headers 在客户端构造器（含浏览器 UA；IAM-Client-Ip 无请求上下文不硬塞）
    ctor_headers = _FakeClient.init_kwargs.get("headers") or {}
    assert ctor_headers.get("Cookie") == "sid=xyz"
    assert "Mozilla/5.0" in ctor_headers.get("User-Agent", "")


def test_dynamic_specs_scope_from_project_id(monkeypatch):
    monkeypatch.setenv("OPENOPS_MCPREGISTRY", "real")

    async def _list():
        return [{"server_id": "alarm-server", "server_name": "a", "server_url": "http://mcpgw/x", "description": ""}]

    async def _disc(url):
        return [
            {"tool_name": "query_alarm_list", "description": "告警列表", "readonly": True,
             "input_schema": {"type": "object", "properties": {"project_id": {"type": "string"}}}},
            {"tool_name": "query_alarm_detail", "description": "告警详情", "readonly": True,
             "input_schema": {"type": "object", "properties": {"alarm_code": {"type": "string"}}}},
        ]

    monkeypatch.setattr(mcp_registry_client, "list_servers", _list)
    monkeypatch.setattr(mcp_registry_client, "discover_tools", _disc)
    by = {s["name"]: s for s in asyncio.run(ar._dynamic_mcp_specs())}
    assert by["query_alarm_list"]["scope_mode"] == "required"  # 有 project_id → 受 scope 约束（拍板 i）
    assert by["query_alarm_list"]["appid_arg_path"] == "$.project_id"
    assert by["query_alarm_list"]["readonly"] is True
    assert by["query_alarm_list"]["server_url"] == "http://mcpgw/x"
    assert by["query_alarm_detail"]["scope_mode"] == "none"  # 无 appid 字段 → 不校 scope


def test_dynamic_specs_empty_when_mock(monkeypatch):
    monkeypatch.delenv("OPENOPS_MCPREGISTRY", raising=False)
    assert asyncio.run(ar._dynamic_mcp_specs()) == []


def test_discover_tools_real_mode_placeholder_endpoint_skips_proxy(monkeypatch):
    """real 模式下占位 endpoint（seed 的 http://mock）不得发起任何网络调用（否则 504/502 噪声）。"""
    monkeypatch.setenv("OPENOPS_MCPREGISTRY", "real")
    monkeypatch.setenv("OPENOPS_MCPREGISTRY_BASE_URL", "https://console.x")
    import httpx

    class _Boom:
        def __init__(self, *a, **k) -> None:
            raise AssertionError("占位 endpoint 不应发起任何网络调用")

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    for url in ("http://mock", ""):
        tools = asyncio.run(mcp_registry_client.discover_tools(url))
        assert {t["tool_name"] for t in tools} == {"query_resource", "recover_execute"}  # 内置 demo 工具
        assert all("schema_hash" in t and "readonly" in t for t in tools)


def _toolkit_st_run():
    from runtime.task_registry import TaskState

    st = TaskState(task_id="t", run_id="r", user_id="u", instance_id="i", input_text="x")
    st.scope_ctx = {"effective_appids": ["APP-REAL-1", "APP-REAL-2"]}
    st.tool_annotations = {
        "query_resource": {"is_approval_required": False, "is_secret_required": False,
                           "scope_mode": "required", "appid_arg_path": "$.appid", "status": "allowed"},
        "recover_execute": {"is_approval_required": True, "is_secret_required": False,
                            "scope_mode": "required", "appid_arg_path": "$.appid", "status": "allowed"},
    }
    st.template_tools = {"query_resource", "recover_execute"}
    return st, {"agent_team_instance_id": "i", "framework_session_id": "s", "audit_trace_id": "tr"}


def test_toolkit_demo_retirement_and_scope_tool(monkeypatch):
    """有动态真工具 → demo 双工具退场（不再弹假审批/脚本 RCA）；list_scope_apps 恒在且返回 scope。"""
    import pytest

    pytest.importorskip("agentscope")

    spec = {"name": "query_alarm_list", "description": "d", "server_url": "http://s", "readonly": True,
            "scope_mode": "none", "appid_arg_path": None,
            "input_schema": {"type": "object", "properties": {}}}

    async def _run(specs, demo_env=None):
        if demo_env is None:
            monkeypatch.delenv("OPENOPS_DEMO_TOOLS", raising=False)
        else:
            monkeypatch.setenv("OPENOPS_DEMO_TOOLS", demo_env)

        async def _specs():
            return specs

        monkeypatch.setattr(ar, "_dynamic_mcp_specs", _specs)
        st, run = _toolkit_st_run()
        # 编排对称化：动态工具对 main 也按模板白名单裁剪（豁免已去除）——本测试关注 demo 退场
        # 与 list_scope_apps，动态工具入册以白名单放行为前提
        st.template_tools = set(st.template_tools or set()) | {s["name"] for s in specs}
        tk, pruned = await ar._build_toolkit(st, run)
        names = {n: (await tk.get_tool(n)) is not None
                 for n in ("query_resource", "recover_execute", "list_scope_apps", "query_alarm_list")}
        return tk, names, pruned

    async def scenario():
        # 自动档：有动态工具 → demo 退场
        tk, names, pruned = await _run([spec])
        assert names["query_resource"] is False and names["recover_execute"] is False
        assert names["list_scope_apps"] is True and names["query_alarm_list"] is True
        assert pruned == []  # 退场是「不提供」不是「策略拦截」，不发 tool.blocked
        # list_scope_apps 返回真 scope
        ft = await tk.get_tool("list_scope_apps")
        resp = await ft._func()
        text = resp.content[0].text  # TextBlock 对象属性访问
        assert "APP-REAL-1" in text and "APP-REAL-2" in text and "2 个应用" in text
        # 自动档：无动态工具 → demo 保留（pytest/stub 现状不回归）
        _, names, _ = await _run([])
        assert names["query_resource"] is True and names["recover_execute"] is True
        # 强制关：无动态工具也退场
        _, names, _ = await _run([], demo_env="0")
        assert names["query_resource"] is False
        # 强制开：有动态工具也保留
        _, names, _ = await _run([spec], demo_env="1")
        assert names["query_resource"] is True and names["query_alarm_list"] is True

    asyncio.run(scenario())


def test_dynamic_tool_admin_no_approval_overrides_readonly_hint(monkeypatch):
    """真机动态工具：管理员『勿审批』标注胜过 server 的 readOnlyHint（修 readOnlyHint 静默覆盖标注、
    非只读动态工具永远弹审批）；未标注的非只读动态工具仍按 readOnlyHint 弹审批。"""
    import pytest

    pytest.importorskip("agentscope")
    monkeypatch.setenv("OPENOPS_MCPREGISTRY", "real")

    specs = [
        {"name": "alarm_ack", "description": "ack", "server_url": "http://s", "readonly": False,
         "scope_mode": "none", "appid_arg_path": None, "input_schema": {"type": "object", "properties": {}}},
        {"name": "alarm_close", "description": "close", "server_url": "http://s", "readonly": False,
         "scope_mode": "none", "appid_arg_path": None, "input_schema": {"type": "object", "properties": {}}},
    ]

    async def _specs():
        return specs

    monkeypatch.setattr(ar, "_dynamic_mcp_specs", _specs)

    async def scenario():
        st, run = _toolkit_st_run()
        # 模拟 run_state_service 装入的管理员标注（annotation_id 非空）：仅 alarm_ack 标了『勿审批』
        st.tool_annotations = {
            "alarm_ack": {"is_approval_required": False, "is_secret_required": False,
                          "scope_mode": "none", "appid_arg_path": None, "status": "allowed",
                          "blocked_reason": None},
        }
        st.template_tools = {"alarm_ack", "alarm_close"}
        tk, _pruned = await ar._build_toolkit(st, run)

        # 标注胜出：alarm_ack 免审批；alarm_close 未标注 → 按 readOnlyHint（非只读）需审批。origin 恒补写。
        assert st.tool_annotations["alarm_ack"]["is_approval_required"] is False
        assert st.tool_annotations["alarm_ack"]["origin"] == "dynamic"
        assert st.tool_annotations["alarm_close"]["is_approval_required"] is True
        assert st.tool_annotations["alarm_close"]["origin"] == "dynamic"

        pctx = ar._permission_context(st)
        assert "alarm_ack" in pctx.allow_rules and "alarm_ack" not in pctx.ask_rules
        assert "alarm_close" in pctx.ask_rules and "alarm_close" not in pctx.allow_rules

        assert (await tk.get_tool("alarm_ack")) is not None
        assert (await tk.get_tool("alarm_close")) is not None

    asyncio.run(scenario())


def test_dynamic_tool_admin_blocked_not_assembled(monkeypatch):
    """真机动态工具：管理员标 status=blocked → 不装配（对齐平台工具：blocked 不进 toolkit、
    _permission_context 不给规则），记 pruned 供审计；标注保留占名 + gateway 兜底。"""
    import pytest

    pytest.importorskip("agentscope")
    monkeypatch.setenv("OPENOPS_MCPREGISTRY", "real")

    specs = [
        {"name": "alarm_purge", "description": "purge", "server_url": "http://s", "readonly": False,
         "scope_mode": "none", "appid_arg_path": None, "input_schema": {"type": "object", "properties": {}}},
    ]

    async def _specs():
        return specs

    monkeypatch.setattr(ar, "_dynamic_mcp_specs", _specs)

    async def scenario():
        st, run = _toolkit_st_run()
        st.tool_annotations = {
            "alarm_purge": {"is_approval_required": False, "is_secret_required": False,
                            "scope_mode": "none", "appid_arg_path": None, "status": "blocked",
                            "blocked_reason": "管理员禁用"},
        }
        st.template_tools = {"alarm_purge"}
        tk, pruned = await ar._build_toolkit(st, run)

        assert (await tk.get_tool("alarm_purge")) is None
        assert ("alarm_purge", "TOOL_BLOCKED") in pruned
        pctx = ar._permission_context(st)
        assert "alarm_purge" not in pctx.allow_rules and "alarm_purge" not in pctx.ask_rules
        assert "alarm_purge" in st.tool_annotations  # 占名 + gateway 兜底保留

    asyncio.run(scenario())


def test_dynamic_tool_autofills_single_scope_appid(monkeypatch):
    import pytest

    pytest.importorskip("agentscope")  # _make_dynamic_tool 需 agentscope（.venv 才有）
    from runtime import tool_gateway
    from runtime.task_registry import TaskState

    st = TaskState(task_id="t", run_id="r", user_id="u", instance_id="i", input_text="x")
    st.scope_ctx = {"effective_appids": ["APP-REAL-1"]}
    cap: dict = {}

    async def _fake_invoke(st_, run_, name, args, **kw):
        cap["args"], cap["server_url"] = args, kw.get("server_url")
        return {"result_summary": "ok"}

    monkeypatch.setattr(tool_gateway, "invoke", _fake_invoke)
    spec = {"name": "query_alarm_list", "description": "d", "server_url": "http://s",
            "readonly": True, "scope_mode": "required", "appid_arg_path": "$.project_id",
            "input_schema": {"type": "object", "properties": {"project_id": {"type": "string"}}}}
    ft = ar._make_dynamic_tool(st, {}, spec)
    asyncio.run(ft._func())  # 不传 project_id → scope 唯一 appid 自动补上
    assert cap["args"] == {"project_id": "APP-REAL-1"}
    assert cap["server_url"] == "http://s"


def test_run_platform_skill_description_injects_available_skills(monkeypatch):
    """LLM 装配集感知：toolkit 构建后 run_platform_skill 的 description 含 st.available_skills 键名
    （实测缺口：静态 docstring 时模型零感知，问「介绍 alarm-query」只答同名 MCP、传错名被 fail-closed）。"""
    import pytest

    pytest.importorskip("agentscope")

    async def _specs():
        return []

    monkeypatch.setattr(ar, "_dynamic_mcp_specs", _specs)

    async def scenario():
        st, run = _toolkit_st_run()
        st.available_skills = {"alarm-query": {"version_no": 1, "checksum": None,
                                               "source_type": "platform", "display_name": "告警查询"}}
        tk, _ = await ar._build_toolkit(st, run)
        tool = await tk.get_tool("run_platform_skill")
        desc = str(getattr(tool, "description", ""))
        assert "alarm-query" in desc and "告警查询" in desc and "/<Skill名>" in desc

        # 空装配集：明说没有，不留静态假例子
        st2, run2 = _toolkit_st_run()
        st2.available_skills = {}
        tk2, _ = await ar._build_toolkit(st2, run2)
        tool2 = await tk2.get_tool("run_platform_skill")
        assert "未装配任何 Skill" in str(getattr(tool2, "description", ""))

    asyncio.run(scenario())


def test_reconcile_isolates_bad_server(client, monkeypatch):
    """对账按 server 隔离：一家 server 坏（如严格握手拒/超时）只记错继续下一家，
    不再中断后续同步、不再把整轮标 reconcile_failed（内网实测踩坑）。"""
    from app import asset_reconcile_service as ars
    from infra.repositories import assets as assets_repo

    async def _setup():
        # 第二个平台 MCP（seed 已有「oModel 查询与恢复」）：坏 server
        await assets_repo.create_mcp(None, "platform", "坏掉的同事server", "http",
                                     {"endpoint": "http://bad.internal/mcp"}, {})

    asyncio.run(_setup())

    async def _disc(server_url):
        if "bad.internal" in (server_url or ""):
            raise RuntimeError("MCP initialize 错误：Bad Request")
        return [{"tool_name": "good_tool", "description": "d", "readonly": True,
                 "input_schema": {"type": "object", "properties": {}},
                 "schema_hash": "h1"}]

    monkeypatch.setattr(ars.mcp_registry_client, "discover_tools", _disc)
    ars._reset()
    summary = asyncio.run(ars.reconcile(force=True, trigger="test"))
    assert summary.get("failed") is not True  # 整轮不因一家坏而 failed
    assert "坏掉的同事server" in (summary.get("tool_sync_errors") or {})  # 坏家记错
    assert summary.get("tools_created", 0) >= 1  # 好家（seed mcp）照常同步


# ==================== 用户自定义 MCP 运行时装配（打通「注册了却永远加载不到」的缺口） ====================

def _user_st_run(endpoint: str = "https://cmdb.internal/mcp"):
    from runtime.task_registry import TaskState

    st = TaskState(task_id="t", run_id="r", user_id="u", instance_id="i", input_text="x")
    st.scope_ctx = {"effective_appids": ["APP-1"]}
    st.tool_annotations = {}
    st.template_tools = set()  # 模板零工具（B7-SEC-001 的最严姿态）——用户 MCP 工具仍应装配
    st.mcp_servers = [{"mcp_id": "m1", "display_name": "我的 CMDB", "endpoint": endpoint}]
    return st, {"agent_team_instance_id": "i", "framework_session_id": "s", "audit_trace_id": "tr"}


def _patch_user_discovery(monkeypatch, tool_name: str = "cmdb_query", readonly: bool = True):
    from infra import egress

    monkeypatch.setattr(egress, "check_mcp_egress", lambda _u: None)  # 本组测装配，不测 egress

    async def fake_discover(url):
        return [{"tool_name": tool_name, "description": "查 CMDB", "readonly": readonly,
                 "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}]

    monkeypatch.setattr(mcp_registry_client, "discover_tools", fake_discover)


def test_user_mcp_tool_bypasses_template_whitelist(monkeypatch):
    """用户自定义 MCP 工具豁免模板 default_tools 白名单（先例 filter_main_skills：白名单只收窄平台资产）。
    模板零工具时平台面为空，用户工具仍装配——否则本特性等于不存在（管理员模板里不可能有用户的 MCP）。"""
    import pytest

    pytest.importorskip("agentscope")
    _patch_user_discovery(monkeypatch)

    async def scenario():
        st, run = _user_st_run()
        tk, _ = await ar._build_toolkit(st, run)
        assert (await tk.get_tool("cmdb_query")) is not None
        ann = st.tool_annotations["cmdb_query"]
        assert ann["origin"] == "user" and ann["status"] == "allowed"
        assert ann["scope_mode"] == "none" and ann["appid_arg_path"] is None  # 关死平台 APPID 自动补
        assert ann["is_secret_required"] is False  # 平台 Secret 绝不注入用户 endpoint
        assert ann["is_approval_required"] is False  # readOnlyHint=true → 免审批（拍板：信任自证）

    asyncio.run(scenario())


def test_user_mcp_tool_not_attached_to_subagent(monkeypatch):
    """B7 per-agent 隔离：用户 MCP 只豁免 main；子 Agent 恒按画像 mcp_tools 裁剪。
    子 TaskState 本就不继承 mcp_servers（_child_state 不复制），_user_mcp_specs 的 agent_key 守卫是第二道锁。"""
    import pytest

    pytest.importorskip("agentscope")
    _patch_user_discovery(monkeypatch)

    async def scenario():
        st, run = _user_st_run()
        st.agent_key = "diagnose"  # 子 Agent
        tk, _ = await ar._build_toolkit(st, run)
        assert (await tk.get_tool("cmdb_query")) is None

    asyncio.run(scenario())


def test_user_mcp_write_tool_requires_approval(monkeypatch):
    """未声明 readOnlyHint（写类）→ ASK（is_approval_required=True）。"""
    import pytest

    pytest.importorskip("agentscope")
    _patch_user_discovery(monkeypatch, tool_name="cmdb_write", readonly=False)

    async def scenario():
        st, run = _user_st_run()
        await ar._build_toolkit(st, run)
        assert st.tool_annotations["cmdb_write"]["is_approval_required"] is True

    asyncio.run(scenario())


def test_user_mcp_ask_always_env_tightens(monkeypatch):
    """OPENOPS_USER_MCP_ASK=1 → 无条件 ASK（readOnlyHint 是用户 server 自证，需要时可一键收紧）。"""
    import pytest

    pytest.importorskip("agentscope")
    _patch_user_discovery(monkeypatch, readonly=True)
    monkeypatch.setattr(ar, "_USER_MCP_ASK_ALWAYS", True)

    async def scenario():
        st, run = _user_st_run()
        await ar._build_toolkit(st, run)
        assert st.tool_annotations["cmdb_query"]["is_approval_required"] is True

    asyncio.run(scenario())


def test_user_mcp_name_collision_platform_wins(monkeypatch):
    """同名冲突平台赢（与 skill 的「用户覆盖平台」刻意相反）：让用户 server 影子化平台工具名 =
    Agent 以为在调平台工具、实际打到用户 URL。**含未装配的平台名**（被裁剪的 demo 工具也占名）。"""
    import pytest

    pytest.importorskip("agentscope")
    _patch_user_discovery(monkeypatch, tool_name="query_resource")  # 撞 demo 平台工具名

    async def scenario():
        st, run = _user_st_run()
        await ar._build_toolkit(st, run)
        # query_resource 是平台 demo 工具名（本例未标注 → 被裁剪、未装配），用户工具**不得**顶上
        assert st.tool_annotations.get("query_resource", {}).get("origin") != "user"

    asyncio.run(scenario())


def test_user_mcp_invoke_uses_user_source_type(monkeypatch):
    """**承重**：用户 MCP 工具必须以 source_type='user' 穿 Gateway。invoke 的默认值是 'platform'，
    _make_dynamic_tool 此前从不传它 ⇒ 用户填的 URL 会收到本人 IAM Cookie + X-OpenOps-Effective-Appids
    （_platform_headers）。此测钉死该缺口：真实出站 header 里不得有 Cookie / X-OpenOps-*。"""
    import pytest

    pytest.importorskip("agentscope")
    _patch_user_discovery(monkeypatch)

    from infra import request_context as rc
    from infra.external import http_mcp_client
    from runtime import tool_gateway

    seen: dict = {}

    async def fake_call(tool_name, arguments, headers=None, server_url=None):
        seen["headers"] = dict(headers or {})
        seen["server_url"] = server_url
        return {"result_summary": "ok"}

    async def quiet(*a, **k):
        return None

    monkeypatch.setattr(http_mcp_client, "call_tool", fake_call)
    monkeypatch.setattr(tool_gateway, "emit", quiet)

    async def scenario():
        rc.set_request_user("u", "JSESSIONID=secret-session")  # 有登录态：平台分支会注 Cookie
        try:
            st, run = _user_st_run()
            tk, _ = await ar._build_toolkit(st, run)
            ft = await tk.get_tool("cmdb_query")
            await ft(q="x")
        finally:
            rc.clear()
        # 不传 source_type 时 invoke 默认走 platform 分支 → 本例模板零工具即 TOOL_BLOCKED，
        # 根本不出站（seen 空）；模板白名单为 None/撞名时则会出站并带上 Cookie。两者都是缺陷。
        assert seen, "用户 MCP 工具没有真正出站——说明 _make_dynamic_tool 没把 source_type='user' 传给 Gateway"
        assert seen["server_url"] == "https://cmdb.internal/mcp"
        assert "Cookie" not in seen["headers"], "用户 MCP 出站带上了用户 IAM Cookie（凭证外泄）"
        assert not any(k.startswith("X-OpenOps-") for k in seen["headers"])

    asyncio.run(scenario())


def test_user_mcp_discovery_failure_isolated(monkeypatch):
    """一家用户 server 坏/被 egress 拦 → 只 log.warning，不拖垮整轮装配（同 reconcile 的按 server 隔离口径）。"""
    import pytest

    pytest.importorskip("agentscope")
    from infra import egress

    monkeypatch.setattr(egress, "check_mcp_egress", lambda _u: None)

    async def boom(url):
        raise RuntimeError("server down")

    monkeypatch.setattr(mcp_registry_client, "discover_tools", boom)

    async def scenario():
        st, run = _user_st_run()
        tk, _ = await ar._build_toolkit(st, run)  # 不抛
        assert (await tk.get_tool("list_scope_apps")) is not None  # 其余工具照常

    asyncio.run(scenario())


def test_user_mcp_egress_blocked_endpoint_not_discovered(monkeypatch):
    """endpoint 被 egress 拦（SSRF/rebinding）→ 不出 spec、不装配（发现边界复校，登记时校过也要再校）。"""
    import pytest

    pytest.importorskip("agentscope")
    from domain.errors import ApiError, Err
    from infra import egress

    def blocked(_u):
        raise ApiError(Err.VALIDATION_FAILED, "MCP endpoint 指向受限地址")

    monkeypatch.setattr(egress, "check_mcp_egress", blocked)

    async def scenario():
        st, run = _user_st_run(endpoint="http://169.254.169.254/mcp")
        tk, _ = await ar._build_toolkit(st, run)
        assert (await tk.get_tool("cmdb_query")) is None

    asyncio.run(scenario())


def test_user_mcp_placeholder_endpoint_no_egress(monkeypatch):
    """占位 endpoint（http://mock）不出网、不出 spec——mock/seed 环境零副作用。"""
    import pytest

    pytest.importorskip("agentscope")

    async def scenario():
        st, run = _user_st_run(endpoint="http://mock")
        tk, _ = await ar._build_toolkit(st, run)
        assert (await tk.get_tool("cmdb_query")) is None

    asyncio.run(scenario())
