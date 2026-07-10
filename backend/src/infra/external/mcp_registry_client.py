"""MCP Registry client：discover tools（29.3 契约面，EXT-004）。

`OPENOPS_MCPREGISTRY=mock(默认)|real`：real 经 29.3 `POST /obsv/agent/management/mcps/proxy`
（body `{url, method:"tools/list"}`，url=目标 MCP server）转发，解 `{code,message,data:{result:{tools}}}` 信封，
OpenOps 侧自算 schema_hash（未联环境 raise）。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

_TOOLS = [
    {
        "tool_name": "query_resource",
        "description": "按 APPID 查询资源与指标",
        "input_schema": {"type": "object", "properties": {"appid": {"type": "string"}}},
    },
    {
        "tool_name": "recover_execute",
        "description": "执行受控恢复动作（需审批）",
        "input_schema": {"type": "object", "properties": {"appid": {"type": "string"}, "action": {"type": "string"}}},
    },
]


def _schema_hash(schema: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:16]


async def list_servers() -> list[dict[str, Any]]:
    """列注册表里 source=openops 的 MCP 服务器（29.3 `POST /obsv/agent/management/mcps/list/query`）。
    real 拉真 console（翻页取全、只留 active + 有 server_url）；mock 返回内置一个（配合 discover_tools 的 _TOOLS）。"""
    if os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() == "real":
        base = os.getenv("OPENOPS_MCPREGISTRY_BASE_URL")
        if not base:
            raise RuntimeError("OPENOPS_MCPREGISTRY=real 需配 OPENOPS_MCPREGISTRY_BASE_URL（29.3 未联）")
        import httpx

        url = f"{base.rstrip('/')}/obsv/agent/management/mcps/list/query"
        out: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=15) as cli:
            page, page_size = 1, 50
            while True:
                r = await cli.post(url, json={"page": page, "page_size": page_size, "source": "openops"})
                r.raise_for_status()
                body = r.json()
                if int(body.get("code", -1)) != 0:
                    raise RuntimeError(f"mcps/list/query 业务错误：code={body.get('code')} {body.get('message', '')}")
                data = body.get("data") or {}
                items = data.get("items") or []
                for it in items:
                    if str(it.get("status")) == "active" and it.get("server_url"):
                        out.append({"server_id": it.get("server_id"), "server_name": it.get("server_name"),
                                    "server_url": it.get("server_url"), "description": it.get("description", "")})
                if not items or page * page_size >= int(data.get("total", 0)):
                    break
                page += 1
        return out
    return [{"server_id": "mock-mcp", "server_name": "mock MCP", "server_url": "http://mock", "description": "mock"}]


async def discover_tools(server_url: str) -> list[dict[str, Any]]:
    """平台 MCP `tools/list`（29.3 §4.1 Proxy）。real 经 `POST /obsv/agent/management/mcps/proxy` 转发到目标 MCP server。

    `server_url` = 平台 MCP 资产的 endpoint（目标 MCP server URL，proxy 必填 `url`）；mock 忽略它返回硬编码 `_TOOLS`。
    OpenOps 侧自算 schema_hash（29.3 分工：Registry 不做发现，OpenOps 落 catalog）。
    """
    if os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() == "real":
        base = os.getenv("OPENOPS_MCPREGISTRY_BASE_URL")
        if not base:
            raise RuntimeError("OPENOPS_MCPREGISTRY=real 需配 OPENOPS_MCPREGISTRY_BASE_URL（29.3 未联）")
        import httpx

        url = f"{base.rstrip('/')}/obsv/agent/management/mcps/proxy"
        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.post(url, json={"url": server_url, "method": "tools/list", "params": {}})
            r.raise_for_status()
            body = r.json()
        if int(body.get("code", -1)) != 0:  # 29.3 业务信封 {code:0, message, data}
            raise RuntimeError(f"MCP Registry proxy 业务错误：code={body.get('code')} {body.get('message', '')}")
        # data = 上游 JSON-RPC {jsonrpc,id,result:{tools}}；工具在 data.result.tools
        tools = (((body.get("data") or {}).get("result")) or {}).get("tools", [])
        return [{"tool_name": t.get("name"), "description": t.get("description", ""),
                 "input_schema": t.get("inputSchema", {}),
                 "readonly": bool((t.get("annotations") or {}).get("readOnlyHint", False)),
                 "schema_hash": _schema_hash(t.get("inputSchema", {}))} for t in tools]
    return [{**t, "readonly": t.get("readonly", False), "schema_hash": _schema_hash(t["input_schema"])} for t in _TOOLS]
