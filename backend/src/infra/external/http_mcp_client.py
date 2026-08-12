"""平台 HTTP MCP client：tools/call（EXT-005/006，28.2 出站契约）。

`OPENOPS_MCP=mock(默认)|real`：real 经平台 MCP 网关 `OPENOPS_MCP_BASE_URL` 发 `POST /tools/{name}:call`，
Tool Gateway 构建的 headers（平台=Cookie/x-ec2-ip/X-OpenOps-*/effective_appids；用户 MCP=空字典，
**含 x-ec2-ip 亦不带**）原样透传。mock 记录 last_call 供测试断言 header 注入口径；两实现签名一致。
"""
from __future__ import annotations

import os
import uuid
from typing import Any

from infra.external.mcp_registry_client import console_api_prefix, console_tls_verify, http_trust_env, raise_with_body  # 同 console 口径

last_call: dict[str, Any] | None = None  # 测试钩子：最近一次调用的 {tool, arguments, headers}

# direct 路由会话缓存：(server_url, 握手头指纹) → Mcp-Session-Id（None=已知 stateless，直发不带头）。
# 命中省 2 个握手往返；400/404 视为会话过期（MCP 规范 404=session 失效）清缓存重握手重试一次。
# 键**必须**含握手头指纹而非只有 server_url：若某用户把自注册 MCP 的 endpoint 填成与某平台 MCP
# 相同的 URL，平台侧带 x-ec2-ip 握手换来的 sid 会被随后的用户侧 tools/call 直接复用——用户请求实际
# 骑在一个由平台身份换来的会话上（对端按会话授权时即越权）。指纹只取头名不取值：值是进程内常量，
# 而头名集合恰好表达「这个会话是以什么身份换来的」。
_sessions: dict[tuple[str, str], str | None] = {}


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
    server_url: str | None = None, *, handshake_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """调一个 MCP 工具。server_url 给定=动态 MCP（经 console registry proxy 的 tools/call 路由到该 server）；
    否则=旧单一平台网关（demo query_resource/recover_execute）。两者都透传 Tool Gateway 构建的 headers（28.2）。

    `handshake_headers`：direct 路由下随 MCP 握手（initialize / notifications/initialized）一并发送的头，
    由 Tool Gateway 按 source_type **显式**传入（平台=host_ip.ec2_ip_headers()，用户=None）——**不**从
    `headers` 里按名过滤反推来源：那等于把「某个头在场」当作平台身份判据，未配置 OPENOPS_EC2_IP 时
    平台支路会被误判成用户支路，而 direct 路由下这类错判只表现为对端 4xx，极难定位。
    ⚠仅限对端可能在 initialize 阶段就校验的头（当前是 x-ec2-ip 与服务态 IAM Authorization——
    两者都是平台自身准入凭据，IAM 网关拦在 server 前、握手缺 Authorization 会直接 401）；
    **不得**用它把 Cookie / X-OpenOps-* 这类**用户**身份头扩散到握手——那部分口径保持现状不变。
    """
    if os.getenv("OPENOPS_MCP", "mock").lower() == "real":
        import httpx

        if server_url:  # 动态 MCP：direct=streamable-HTTP 直连 server_url（默认）；proxy=经 console mcps/proxy
            from infra.external.mcp_registry_client import mcp_initialize, mcp_request, mcp_route, parse_mcp_response

            if mcp_route() == "direct":
                # headers 由 Tool Gateway 构建：X-OpenOps-* 上下文头 + 用户登录态透传（Cookie/IAM-Client-Ip/
                # X-Forwarded-For，IAM 保护的内网 MCP 靠它鉴权，见 tool_gateway._platform_headers）。
                # 严格 stateful server 须先 initialize（Mcp-Session-Id 会话头），见 mcp_initialize
                params = {"name": tool_name, "arguments": arguments}
                # 会话按「以什么身份握的手」分区（见 _sessions 注释）：同一 server_url 的平台会话与
                # 用户会话互不复用。
                skey = (server_url, ",".join(sorted(k.lower() for k in (handshake_headers or {}))))
                async with httpx.AsyncClient(timeout=float(os.getenv("OPENOPS_MCP_TIMEOUT_S", "30")),
                                             verify=console_tls_verify(), trust_env=http_trust_env()) as cli:
                    was_cached = skey in _sessions
                    if was_cached:
                        sid = _sessions[skey]
                    else:
                        sid = await mcp_initialize(cli, server_url, handshake_headers)
                        _sessions[skey] = sid
                    r = await mcp_request(cli, server_url, "tools/call", params, sid, dict(headers or {}))
                    if was_cached and r.status_code in (400, 404):
                        # 缓存会话已被 server 判失效 → 清缓存重握手重试一次（新会话失败即真错误照抛）
                        _sessions.pop(skey, None)
                        sid = await mcp_initialize(cli, server_url, handshake_headers)
                        _sessions[skey] = sid
                        r = await mcp_request(cli, server_url, "tools/call", params, sid, dict(headers or {}))
                    raise_with_body(r)
                    rpc = parse_mcp_response(r)
                if "error" in rpc:
                    raise RuntimeError(f"MCP 工具 {tool_name} JSON-RPC 错误：{str(rpc['error'])[:200]}")
                result = rpc.get("result") or {}
                if result.get("isError"):
                    raise RuntimeError(f"MCP 工具 {tool_name} 返回错误：{_summarize(result)}")
                rid = r.headers.get("Trace-Id") or "mcp_" + uuid.uuid4().hex[:10]  # mcpgateway Trace-Id 作外部请求号
                return {"request_id": rid, "status": "ok", "result_summary": _summarize(result)}

            base = os.getenv("OPENOPS_MCPREGISTRY_BASE_URL")
            if not base:
                raise RuntimeError("动态 MCP 调用需 OPENOPS_MCPREGISTRY_BASE_URL（经 console proxy 路由 tools/call）")
            url = f"{base.rstrip('/')}{console_api_prefix()}/mcps/proxy"
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
            if int(body.get("code", -1)) not in (0, 200):  # 29.3 信封；2026-07-13 对端统一 200，0 兼容旧版
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
    last_call = {"tool": tool_name, "arguments": dict(arguments), "headers": dict(headers or {}),
                 "server_url": server_url, "handshake_headers": handshake_headers}
    rid = "req_" + uuid.uuid4().hex[:10]
    if tool_name == "recover_execute":
        return {"request_id": rid, "status": "accepted", "execution_id": "exec_" + uuid.uuid4().hex[:8]}
    return {"request_id": rid, "status": "ok", "result_summary": f"{tool_name} 查询完成（mock）"}
