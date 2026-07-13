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


def console_api_prefix() -> str:
    """console 系 API 文根（mcps 列表/代理、skills 列表/下载同一网关文根）：默认 29.3 的
    `/obsv/agent/management`；测试/生产网关文根不同、或对端改文根时设 `OPENOPS_CONSOLE_API_PREFIX`
    覆盖，免改码（与共享 cookie 同思路：console 面的部署差异全走 env）。"""
    p = os.getenv("OPENOPS_CONSOLE_API_PREFIX", "/obsv/agent/management").strip()
    return ("/" + p.strip("/")) if p.strip("/") else ""


def console_cookie(specific_env: str) -> str:
    """console 系 IAM 会话 cookie 统一读取（2026-07-14 终版口径）：

      ① 用户登录态透传（请求内 contextvar > 按 user_id 缓存）—— **真实环境唯一正道**：
         IAM 开启即有，umodel/console 校验的就是这份，权限=操作者本人，无过期维护成本。
      ② 专属 env（如 OPENOPS_OMODEL_COOKIE）③ 共享 OPENOPS_CONSOLE_COOKIE ——
         **仅本地调试缝**（无 IAM 登录态时手工贴浏览器会话联调用），生产环境一律不配。
    """
    from infra.request_context import cached_user_cookie, current_user_id, user_cookie

    passthrough = user_cookie() or cached_user_cookie(current_user_id())
    return passthrough or os.getenv(specific_env) or os.getenv("OPENOPS_CONSOLE_COOKIE") or ""


def _console_headers() -> dict[str, str]:
    """console（mcps/list/query、mcps/proxy）需带用户 Cookie 鉴权：OPENOPS_MCPREGISTRY_COOKIE（回退共享）。
    联调用（本地无 IAM 登录）；生产由真 IAM 网关透传用户 cookie。未设=不带（会 401，需配）。cookie 是会话态，会过期。"""
    cookie = console_cookie("OPENOPS_MCPREGISTRY_COOKIE")
    return {"Cookie": cookie} if cookie else {}


def http_trust_env() -> bool:
    """出站是否信任环境代理（HTTP(S)_PROXY / Windows 注册表 IE 代理——httpx 经 urllib getproxies
    在 Windows 会读注册表）。默认 **False**：console/GLM 都是内网直连目标，公司 SWG 代理够不到它们
    （实测被代理劫走 → 504 / HIS Proxy 错误页，且是否中招随 shell 环境漂移）。真需要走代理时
    OPENOPS_HTTP_TRUST_ENV=1 恢复。"""
    return os.getenv("OPENOPS_HTTP_TRUST_ENV") == "1"


def raise_with_body(r: Any) -> None:
    """raise_for_status 但带响应体前 300 字——console 的 502/504 页面里往往写着网关侧原因，
    吞掉 body 只剩状态码没法定位（如 proxy 转发不到目标 MCP server vs 请求体形状不符）。"""
    if r.status_code >= 400:
        raise RuntimeError(f"console HTTP {r.status_code}：{r.text[:300]}")


def mcp_route() -> str:
    """MCP 发现/调用怎么到达目标 server：`OPENOPS_MCP_ROUTE=direct(默认)|proxy`。
    direct=按标准 MCP streamable-HTTP 直连 server_url（JSON-RPC 信封 + SSE 响应；实测 mcpgateway
    直连 200、且无需 console cookie）；proxy=经 console `mcps/proxy` 转发（实测其上游转发 404，
    console 侧待修——修好可切回，生产 IAM 收口路径）。"""
    return os.getenv("OPENOPS_MCP_ROUTE", "direct").strip().lower()


def parse_mcp_response(r: Any) -> dict[str, Any]:
    """解 streamable-HTTP MCP 响应：SSE（text/event-stream 的 `data:` 行）或纯 JSON，返回 JSON-RPC 对象。"""
    if "text/event-stream" in str(r.headers.get("content-type", "")):
        for line in r.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                try:
                    obj = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                    return obj
        raise RuntimeError(f"MCP SSE 响应无 JSON-RPC data 消息：{r.text[:200]}")
    return r.json()


_MCP_ACCEPT = "application/json, text/event-stream"  # streamable-HTTP：server 可回 JSON 或 SSE


def console_tls_verify() -> bool | str:
    """console 是 https 内网证书：Python httpx 用 certifi CA（无公司内部 CA）会
    CERTIFICATE_VERIFY_FAILED（Windows curl 用系统证书库所以通）。三档：
    OPENOPS_TLS_CA_FILE=<公司CA.pem>（正解）＞ OPENOPS_TLS_INSECURE=1（联调临时，等 curl -k）＞ 默认 certifi。
    另一正解：pip install truststore 后 run.py 会自动注入系统证书库（见 run.py）。"""
    ca = os.getenv("OPENOPS_TLS_CA_FILE", "").strip()
    if ca:
        return ca
    return False if os.getenv("OPENOPS_TLS_INSECURE") == "1" else True


async def list_servers() -> list[dict[str, Any]]:
    """列注册表里 source=openops 的 MCP 服务器（29.3 `POST /obsv/agent/management/mcps/list/query`）。
    real 拉真 console（翻页取全、只留 active + 有 server_url）；mock 返回内置一个（配合 discover_tools 的 _TOOLS）。"""
    if os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() == "real":
        from infra.request_context import expand_host

        base = expand_host(os.getenv("OPENOPS_MCPREGISTRY_BASE_URL") or "")
        if not base:
            raise RuntimeError("OPENOPS_MCPREGISTRY=real 需配 OPENOPS_MCPREGISTRY_BASE_URL（29.3 未联）")
        import httpx

        url = f"{base.rstrip('/')}{console_api_prefix()}/mcps/list/query"
        out: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=15, verify=console_tls_verify(), trust_env=http_trust_env()) as cli:
            page, page_size = 1, 50
            while True:
                r = await cli.post(url, json={"page": page, "page_size": page_size, "source": "openops"},
                                   headers=_console_headers())
                raise_with_body(r)
                body = r.json()
                if int(body.get("code", -1)) not in (0, 200):  # 2026-07-13 对端统一 200；0 兼容旧版
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
        # 占位 endpoint 防呆：seed 的 demo MCP 资产 endpoint 是 "http://mock"，真发给 console proxy
        # 会让网关去连 http://mock → 504（reconcile 登录触发即中招）。占位符直接走内置工具。
        from urllib.parse import urlparse

        # 只认已知占位（空 / host=mock）；其它 URL 照走 real 校验链（无 BASE_URL 仍 fail-loud，EXT-007）
        if not server_url or urlparse(server_url).hostname == "mock":
            return [{**t, "readonly": t.get("readonly", False), "schema_hash": _schema_hash(t["input_schema"])}
                    for t in _TOOLS]
        import httpx

        if mcp_route() == "direct":  # 标准 MCP streamable-HTTP 直连 server_url（实测通；无需 console cookie）
            async with httpx.AsyncClient(timeout=15, verify=console_tls_verify(), trust_env=http_trust_env()) as cli:
                r = await cli.post(server_url,
                                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                                   headers={"Accept": _MCP_ACCEPT})
                raise_with_body(r)
                rpc = parse_mcp_response(r)
            if "error" in rpc:
                raise RuntimeError(f"MCP tools/list 错误：{str(rpc['error'])[:200]}")
            tools = (rpc.get("result") or {}).get("tools", [])
        else:  # proxy：经 console mcps/proxy 转发（console 侧上游转发待修）
            from infra.request_context import expand_host

            base = expand_host(os.getenv("OPENOPS_MCPREGISTRY_BASE_URL") or "")
            if not base:
                raise RuntimeError("OPENOPS_MCPREGISTRY=real 需配 OPENOPS_MCPREGISTRY_BASE_URL（29.3 未联）")
            url = f"{base.rstrip('/')}{console_api_prefix()}/mcps/proxy"
            async with httpx.AsyncClient(timeout=15, verify=console_tls_verify(), trust_env=http_trust_env()) as cli:
                r = await cli.post(url, json={"url": server_url, "method": "tools/list", "params": {}},
                                   headers=_console_headers())
                raise_with_body(r)
                body = r.json()
            if int(body.get("code", -1)) not in (0, 200):  # 29.3 信封；2026-07-13 对端统一 200，0 兼容旧版
                raise RuntimeError(f"MCP Registry proxy 业务错误：code={body.get('code')} {body.get('message', '')}")
            # data = 上游 JSON-RPC {jsonrpc,id,result:{tools}}；工具在 data.result.tools
            tools = (((body.get("data") or {}).get("result")) or {}).get("tools", [])
        return [{"tool_name": t.get("name"), "description": t.get("description", ""),
                 "input_schema": t.get("inputSchema", {}),
                 "readonly": bool((t.get("annotations") or {}).get("readOnlyHint", False)),
                 "schema_hash": _schema_hash(t.get("inputSchema", {}))} for t in tools]
    return [{**t, "readonly": t.get("readonly", False), "schema_hash": _schema_hash(t["input_schema"])} for t in _TOOLS]
