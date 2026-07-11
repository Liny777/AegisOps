"""动态 MCP 工具装配（真注册表发现 → agent 工具，穿 Tool Gateway 经 console proxy 路由）单元测试。"""
from __future__ import annotations

import asyncio

from infra.external import http_mcp_client, mcp_registry_client
from runtime import agentscope_runtime as ar


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._p = payload

    def raise_for_status(self) -> None:  # noqa: D401
        pass

    def json(self) -> dict:
        return self._p


class _FakeClient:
    captured: dict = {}
    init_kwargs: dict = {}

    def __init__(self, *a, **k) -> None:
        _FakeClient.init_kwargs = dict(k)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeClient.captured = {"url": url, "json": json, "headers": headers}
        return _Resp({"code": 0, "data": {"result": {
            "structuredContent": {"result": "告警3条：A/B/C"}, "isError": False}}})


def test_mcp_real_call_routes_via_proxy_and_preserves_28_2_headers(monkeypatch):
    monkeypatch.setenv("OPENOPS_MCP", "real")
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
    monkeypatch.setenv("OPENOPS_MCPREGISTRY_BASE_URL", "https://console.x")
    monkeypatch.setenv("OPENOPS_TLS_INSECURE", "1")
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    asyncio.run(http_mcp_client.call_tool("t", {}, server_url="http://mcpgw/x"))
    assert _FakeClient.init_kwargs.get("verify") is False
    monkeypatch.setenv("OPENOPS_TLS_CA_FILE", "/etc/corp-ca.pem")  # CA 文件档优先于 insecure
    asyncio.run(http_mcp_client.call_tool("t", {}, server_url="http://mcpgw/x"))
    assert _FakeClient.init_kwargs.get("verify") == "/etc/corp-ca.pem"


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
    assert _FakeClient.captured["headers"].get("Cookie") == "sid=xyz"


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
    """real 模式下占位 endpoint（seed 的 http://mock）不得发给 console proxy（否则网关连 http://mock → 504）。"""
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
