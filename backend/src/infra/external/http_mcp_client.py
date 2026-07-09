"""平台 HTTP MCP client（mock）：tools/call（EXT-005/006）。

headers 由 Tool Gateway 构建传入（平台=X-OpenOps-*，用户=空）；mock 记录 last_call 供测试断言
header 注入口径，真实实现替换本模块时签名不变。
"""
from __future__ import annotations

import uuid
from typing import Any

last_call: dict[str, Any] | None = None  # 测试钩子：最近一次调用的 {tool, arguments, headers}


async def call_tool(
    tool_name: str, arguments: dict[str, Any], headers: dict[str, str] | None = None
) -> dict[str, Any]:
    global last_call
    last_call = {"tool": tool_name, "arguments": dict(arguments), "headers": dict(headers or {})}
    rid = "req_" + uuid.uuid4().hex[:10]
    if tool_name == "recover_execute":
        return {"request_id": rid, "status": "accepted", "execution_id": "exec_" + uuid.uuid4().hex[:8]}
    return {"request_id": rid, "status": "ok", "result_summary": f"{tool_name} 查询完成（mock）"}
