"""MCP Registry client（mock）：discover tools（29.3 契约面，EXT-004）。"""
from __future__ import annotations

import hashlib
import json
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
    return [{**t, "schema_hash": _schema_hash(t["input_schema"])} for t in _TOOLS]
