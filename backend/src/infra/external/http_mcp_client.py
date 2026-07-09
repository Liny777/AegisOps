"""平台 HTTP MCP client（mock）：tools/call（EXT-005/006）。"""
from __future__ import annotations

import uuid
from typing import Any


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    rid = "req_" + uuid.uuid4().hex[:10]
    if tool_name == "recover_execute":
        return {"request_id": rid, "status": "accepted", "execution_id": "exec_" + uuid.uuid4().hex[:8]}
    return {"request_id": rid, "status": "ok", "result_summary": f"{tool_name} 查询完成（mock）"}
