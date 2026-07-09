"""MCP Registry client：discover tools（29.3 契约面，EXT-004）。

`OPENOPS_MCPREGISTRY=mock(默认)|real`：real 经 29.3 `POST /mcps/proxy`（method=`tools/list`）转发，
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


async def discover_tools(mcp_name: str) -> list[dict[str, Any]]:
    if os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() == "real":
        base = os.getenv("OPENOPS_MCPREGISTRY_BASE_URL")
        if not base:
            raise RuntimeError("OPENOPS_MCPREGISTRY=real 需配 OPENOPS_MCPREGISTRY_BASE_URL（29.3 未联）")
        import httpx

        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.post(f"{base}/mcps/proxy",
                               json={"server_id": mcp_name, "method": "tools/list", "params": {}})
            r.raise_for_status()
            body = r.json()
            tools = body.get("tools") or body.get("result", {}).get("tools", [])
        # OpenOps 侧自算 schema_hash（29.3 分工：Registry 不做发现，OpenOps 落 catalog）
        return [{"tool_name": t.get("name"), "description": t.get("description", ""),
                 "input_schema": t.get("inputSchema", {}),
                 "schema_hash": _schema_hash(t.get("inputSchema", {}))} for t in tools]
    return [{**t, "schema_hash": _schema_hash(t["input_schema"])} for t in _TOOLS]
