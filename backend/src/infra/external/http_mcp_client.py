"""平台 HTTP MCP client：tools/call（EXT-005/006，28.2 出站契约）。

`OPENOPS_MCP=mock(默认)|real`：real 经平台 MCP 网关 `OPENOPS_MCP_BASE_URL` 发 `POST /tools/{name}:call`，
Tool Gateway 构建的 headers（平台=Cookie/X-EC2-IP/X-OpenOps-*/effective_appids；用户 MCP=空）原样透传。
mock 记录 last_call 供测试断言 header 注入口径；两实现签名一致。
"""
from __future__ import annotations

import os
import uuid
from typing import Any

last_call: dict[str, Any] | None = None  # 测试钩子：最近一次调用的 {tool, arguments, headers}


async def call_tool(
    tool_name: str, arguments: dict[str, Any], headers: dict[str, str] | None = None
) -> dict[str, Any]:
    if os.getenv("OPENOPS_MCP", "mock").lower() == "real":
        base = os.getenv("OPENOPS_MCP_BASE_URL")
        if not base:
            raise RuntimeError("OPENOPS_MCP=real 需配 OPENOPS_MCP_BASE_URL（平台 MCP 网关未联）")
        import httpx

        async with httpx.AsyncClient(timeout=float(os.getenv("OPENOPS_MCP_TIMEOUT_S", "30"))) as cli:
            # 28.2 出站契约：body 同时含 tool_name + arguments（平台上下文走 header，不作 body 主事实源）。
            # ⚠URL 路由 28.2 未 pin（网关直连 vs registry proxy）——保留可配默认，待联调确认。
            r = await cli.post(f"{base.rstrip('/')}/tools/{tool_name}:call",
                               json={"tool_name": tool_name, "arguments": arguments}, headers=headers or {})
            r.raise_for_status()
            return r.json()

    global last_call
    last_call = {"tool": tool_name, "arguments": dict(arguments), "headers": dict(headers or {})}
    rid = "req_" + uuid.uuid4().hex[:10]
    if tool_name == "recover_execute":
        return {"request_id": rid, "status": "accepted", "execution_id": "exec_" + uuid.uuid4().hex[:8]}
    return {"request_id": rid, "status": "ok", "result_summary": f"{tool_name} 查询完成（mock）"}
