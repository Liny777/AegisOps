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

    def __init__(self, *a, **k) -> None:
        pass

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
    assert r["result_summary"] == "告警3条：A/B/C"  # fastmcp structuredContent.result 抽取


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
