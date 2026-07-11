"""平台 HTTP MCP client：tools/call（EXT-005/006，28.2 出站契约）。

`OPENOPS_MCP=mock(默认)|real`：real 经平台 MCP 网关 `OPENOPS_MCP_BASE_URL` 发 `POST /tools/{name}:call`，
Tool Gateway 构建的 headers（平台=Cookie/X-EC2-IP/X-OpenOps-*/effective_appids；用户 MCP=空）原样透传。
mock 记录 last_call 供测试断言 header 注入口径；两实现签名一致。
"""
from __future__ import annotations

import os
import uuid
from typing import Any

from infra.external.mcp_registry_client import console_tls_verify, http_trust_env, raise_with_body  # 内网证书 TLS 三档（CA 文件/insecure/默认）

last_call: dict[str, Any] | None = None  # 测试钩子：最近一次调用的 {tool, arguments, headers}


_SUMMARY_CAP = int(os.getenv("OPENOPS_MCP_RESULT_CAP", "24000"))


def _summarize(result: dict[str, Any]) -> str:
    """从 MCP tools/call 的 JSON-RPC result 抽给模型的文本。fastmcp：优先 structuredContent.result，
    否则 content[0].text，再否则整体 str。上限 OPENOPS_MCP_RESULT_CAP（防单条撑爆窗口，同 D7 口径）。"""
    sc = result.get("structuredContent")
    if isinstance(sc, dict) and "result" in sc:
        return str(sc["result"])[:_SUMMARY_CAP]
    content = result.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        return str(content[0].get("text", ""))[:_SUMMARY_CAP]
    return str(result)[:_SUMMARY_CAP]


async def call_tool(
    tool_name: str, arguments: dict[str, Any], headers: dict[str, str] | None = None,
    server_url: str | None = None,
) -> dict[str, Any]:
    """调一个 MCP 工具。server_url 给定=动态 MCP（经 console registry proxy 的 tools/call 路由到该 server）；
    否则=旧单一平台网关（demo query_resource/recover_execute）。两者都透传 Tool Gateway 构建的 headers（28.2）。"""
    if os.getenv("OPENOPS_MCP", "mock").lower() == "real":
        import httpx

        if server_url:  # 动态 MCP：走注册表 proxy 的 tools/call（url=目标 server，name+arguments 在 params）
            base = os.getenv("OPENOPS_MCPREGISTRY_BASE_URL")
            if not base:
                raise RuntimeError("动态 MCP 调用需 OPENOPS_MCPREGISTRY_BASE_URL（经 console proxy 路由 tools/call）")
            url = f"{base.rstrip('/')}/obsv/agent/management/mcps/proxy"
            hdrs = dict(headers or {})  # console 需用户 Cookie 鉴权（同发现路径）；真 IAM 网关透传时不覆盖
            cookie = os.getenv("OPENOPS_MCPREGISTRY_COOKIE")
            if cookie and "Cookie" not in hdrs:
                hdrs["Cookie"] = cookie
            async with httpx.AsyncClient(timeout=float(os.getenv("OPENOPS_MCP_TIMEOUT_S", "30")), verify=console_tls_verify(), trust_env=http_trust_env()) as cli:
                r = await cli.post(url, json={"url": server_url, "method": "tools/call",
                                              "params": {"name": tool_name, "arguments": arguments}},
                                   headers=hdrs)
                raise_with_body(r)
                body = r.json()
            if int(body.get("code", -1)) != 0:  # 29.3 业务信封
                raise RuntimeError(f"MCP proxy tools/call 业务错误：code={body.get('code')} {body.get('message', '')}")
            result = (((body.get("data") or {}).get("result")) or {})  # 上游 JSON-RPC result
            if result.get("isError"):
                raise RuntimeError(f"MCP 工具 {tool_name} 返回错误：{_summarize(result)}")
            return {"request_id": "mcp_" + uuid.uuid4().hex[:10], "status": "ok", "result_summary": _summarize(result)}

        base = os.getenv("OPENOPS_MCP_BASE_URL")  # legacy 单网关（demo query_resource/recover_execute）
        if not base:
            raise RuntimeError("OPENOPS_MCP=real 需配 OPENOPS_MCP_BASE_URL（平台 MCP 网关未联）")
        async with httpx.AsyncClient(timeout=float(os.getenv("OPENOPS_MCP_TIMEOUT_S", "30")), verify=console_tls_verify(), trust_env=http_trust_env()) as cli:
            r = await cli.post(f"{base.rstrip('/')}/tools/{tool_name}:call",
                               json={"tool_name": tool_name, "arguments": arguments}, headers=headers or {})
            raise_with_body(r)
            return r.json()

    global last_call
    last_call = {"tool": tool_name, "arguments": dict(arguments), "headers": dict(headers or {}), "server_url": server_url}
    rid = "req_" + uuid.uuid4().hex[:10]
    if tool_name == "recover_execute":
        return {"request_id": rid, "status": "accepted", "execution_id": "exec_" + uuid.uuid4().hex[:8]}
    return {"request_id": rid, "status": "ok", "result_summary": f"{tool_name} 查询完成（mock）"}
