"""MCP Registry client：discover tools（29.3 契约面，EXT-004）。

`OPENOPS_MCPREGISTRY=mock(默认)|real`：real 经 29.3 `POST /obsv/agent/management/mcps/proxy`
（body `{url, method:"tools/list"}`，url=目标 MCP server）转发，解 `{code,message,data:{result:{tools}}}` 信封，
OpenOps 侧自算 schema_hash（未联环境 raise）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

from infra.logging_config import WindowGate, suppressed_suffix

log = logging.getLogger("openops.mcpreg")

# console 网关与 omodel 同一套边界策略（omodel_real._BROWSER_UA 内网实锤）：脚本类 UA 会被拦，
# 出站以浏览器 UA 代表登录用户操作。两处常量保持一致；不从 omodel_real import（它反向依赖本模块）。
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

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


class ConsoleError(RuntimeError):
    """console 网关（skills / mcps 两面）的结构化失败，供 app 层按类别映射 HTTP 语义。

    kind: "biz"=信封业务码错误（biz_code 有值，如 2003/2004 名称冲突、1003 非超管）｜
    "http"=非 2xx 且无信封（status_code 有值，404=对端接口未上线）｜
    "network"=传输层不可达/超时（重试语义）。
    继承 RuntimeError：既有 `except RuntimeError` 兜底与测试断言不受影响。

    定义在本模块（而非 skill_hub_client）是为了避免 import 环——skill_hub_client 单向
    import 本模块，基类放在下层两面都能用。"""

    def __init__(self, kind: str, message: str, *, biz_code: int | None = None,
                 status_code: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.biz_code = biz_code
        self.status_code = status_code


class McpRegistryError(ConsoleError):
    """MCP Registry 面（register/delete）的结构化失败。与 SkillHubError 并列，
    让 app 层能只捕获 MCP 面而不误吞 Skill 面的异常。"""


def raise_biz_or_http(r: Any, exc_cls: type[ConsoleError]) -> None:
    """非 2xx 收口（写接口用；list/download 仍走 raise_with_body 保持既有文案）。

    29.3 约定业务码随 HTTP 200 信封走，但对端形状常漂移（2003/2004 也可能随 4xx 返回）——
    先试解 JSON 信封取业务码归入 "biz"，解不出（HTML 登录页/网关 5xx 裸文本）才归 "http"。"""
    if r.status_code < 400:
        return
    try:
        body = r.json()
        code = int(body.get("code"))
    except Exception:  # noqa: BLE001 —— 非 JSON / 无 code：登录页 HTML、网关裸错误
        raise exc_cls("http", f"console HTTP {r.status_code}：{r.text[:300]}",
                      status_code=r.status_code) from None
    raise exc_cls("biz", str(body.get("message", "")) or f"console HTTP {r.status_code}",
                  biz_code=code, status_code=r.status_code)


def unwrap_console_data(body: dict[str, Any], exc_cls: type[ConsoleError],
                        ok_codes: tuple[int, ...] = (0, 200)) -> dict[str, Any]:
    """业务信封 `{code, message, data}` 解包（code 是业务状态，与 HTTP 状态分离）。

    成功码默认收 0 和 200 两种：29.3 文档写 `code:0`，但内网实测 skills 面成功返回 `code:200`
    （2026-07-13 起 console 网关两面已统一 200）；两收兼容旧版。register 类接口 HTTP 201，
    信封里也可能带 `code:201`，由调用方经 ok_codes 显式放行。"""
    code = int(body.get("code", -1))
    if code not in ok_codes:
        raise exc_cls("biz", f"console 返回业务错误：code={body.get('code')} {body.get('message', '')}",
                      biz_code=code)
    return body.get("data") or {}


def console_api_prefix() -> str:
    """console 系 API 文根（mcps 列表/代理、skills 列表/下载同一网关文根）：默认 29.3 的
    `/obsv/agent/management`；测试/生产网关文根不同、或对端改文根时设 `OPENOPS_CONSOLE_API_PREFIX`
    覆盖，免改码（与共享 cookie 同思路：console 面的部署差异全走 env）。"""
    p = os.getenv("OPENOPS_CONSOLE_API_PREFIX", "/obsv/agent/management").strip()
    return ("/" + p.strip("/")) if p.strip("/") else ""


def console_service_user_id() -> str:
    """机机态兜底身份（29.9 §1.3 三级鉴权）：无 Cookie 且无 `user_id` 入参 → 对端直接 401。

    后台对账（background_loop / 无请求上下文的路径）两样都没有，整段 ingest 会静默失败在
    `mcp_ingest_error` 里。配 `OPENOPS_CONSOLE_SERVICE_USER_ID`（服务账号工号）后，
    调用方未显式传 user_id 时用它兜底。注意：**该工号的可见范围决定我们能同步到哪些资产**。
    用户态请求仍优先走 cookie 透传（console_cookie ①），本值只是兜底。"""
    return os.getenv("OPENOPS_CONSOLE_SERVICE_USER_ID", "").strip()


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


def _http_debug() -> bool:
    return os.getenv("OPENOPS_HTTP_DEBUG", "").strip().lower() in ("1", "true", "yes")


#: cookie_src 行的时间窗闸门（与 omodel_real._debug_gate 同款、各自独立计数）。
_debug_gate = WindowGate("OPENOPS_HTTP_DEBUG_SAMPLE_S")


async def _log_outbound_request(request: Any) -> None:
    """门控诊断（OPENOPS_HTTP_DEBUG=1）：出站的方法/URL/头/体。SEC-001：Cookie 只打长度、
    IAM-Client-Ip/X-Forwarded-For 只打在场与否——凭据与客户端 IP 不入日志。"""
    masked = []
    for k, v in request.headers.items():
        lk = k.lower()
        if lk == "cookie":
            masked.append(f"{k}=<len={len(v)}>")
        elif lk in ("iam-client-ip", "x-forwarded-for"):
            masked.append(f"{k}=<set>")
        else:
            masked.append(f"{k}={v}")
    # multipart（files=，如 skills:upload）的体是 httpx MultipartStream 流式体，构造时不预读 _content：
    # 直接读 request.content 会 raise RequestNotRead（"…without having called read()"），诊断钩子反把上传
    # 打成 502。流式/二进制体（含 ZIP）也不该 decode 进日志——按 content-type 打占位（与
    # _log_outbound_response 对二进制的处理一致；hasattr(_content) 即 httpx .content 内部同款判定）。
    if hasattr(request, "_content"):
        body = request.content.decode("utf-8", "replace")[:500] if request.content else ""
    else:
        body = f"<streaming body {request.headers.get('content-type', '')}>"
    # info 而非 warning：本行只在 OPENOPS_HTTP_DEBUG=1 时存在，warning 会把它混进真告警里。
    # 不做窗口抑制——这两行的全部价值就是逐请求全量细节，压掉等于关掉。
    log.info("[OpenOps][mcpreg][debug] → %s %s body=%s", request.method, request.url, body)
    log.info("[OpenOps][mcpreg][debug] → headers: %s", "; ".join(masked))


async def _log_outbound_response(response: Any) -> None:
    await response.aread()  # hook 时机在体读取前；先读全才能打响应体
    ct = str(response.headers.get("content-type", ""))
    body = ((response.text or "")[:1000] if ("json" in ct or "text" in ct)
            else f"<{len(response.content)} bytes {ct}>")  # ZIP 等二进制不进日志
    log.info("[OpenOps][mcpreg][debug] ← status=%s body=%s", response.status_code, body)


def console_client_kwargs(base: str, cookie_env: str, timeout: float = 15) -> dict[str, Any]:
    """console 系（mcps/skills）出站统一装配——与 omodel_real._client_kwargs 同款（88a8fc1 根因链）：

    登录态 Cookie + 浏览器 UA + IAM-Client-Ip/X-Forwarded-For（华为 IAM 会话绑客户端 IP，
    只带 Cookie 从服务器 IP 出站会被判「登录态已失效」code=1001）+ 从目标 base 派生的
    CSRF 同源头 + TLS/代理三档 + OPENOPS_HTTP_DEBUG 门控诊断钩子。"""
    from urllib.parse import urlparse

    from infra.request_context import cached_user_cookie, client_ip, current_user_id, user_cookie

    kwargs: dict[str, Any] = {"timeout": timeout, "verify": console_tls_verify(),
                              "trust_env": http_trust_env()}
    headers: dict[str, str] = {"User-Agent": _BROWSER_UA}
    cookie = console_cookie(cookie_env)
    if cookie:
        headers["Cookie"] = cookie
    ip = client_ip()
    if ip:
        headers["IAM-Client-Ip"] = ip
        headers["X-Forwarded-For"] = ip
    try:
        t = urlparse(base)
        origin = f"{t.scheme}://{t.netloc}" if t.scheme and t.netloc else ""
    except ValueError:
        origin = ""
    if origin:
        headers.update({
            "Origin": origin,
            "Referer": origin + "/",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        })
    from infra.iam_headers import iam_auth_headers
    for k, v in iam_auth_headers().items():  # 服务态 IAM（内网 j2c_utils，缺失即空）：console 系皆内网平台服务，setdefault 防未来占用者被覆盖
        headers.setdefault(k, v)
    kwargs["headers"] = headers
    if _http_debug():
        src = ("passthrough" if user_cookie()
               else "cache" if cached_user_cookie(current_user_id())
               else f"env:{cookie_env}" if os.getenv(cookie_env)
               else "env:shared" if cookie
               else "none")
        # 与 omodel 同款窗口抑制：client 每次调用现建，这行本会随出站频率线性刷屏。
        ip_state, iam_state = ("set" if ip else "missing"), ("set" if headers.get("Authorization") else "missing")
        ok, dropped = _debug_gate.allow(f"cookie:{src}:{ip_state}:{iam_state}")
        if ok:
            log.info("[OpenOps][mcpreg][debug] cookie_src=%s cookie_len=%d client_ip=%s iam=%s%s",
                     src, len(cookie or ""), ip_state, iam_state, suppressed_suffix(dropped))
        kwargs["event_hooks"] = {"request": [_log_outbound_request],
                                 "response": [_log_outbound_response]}
    return kwargs


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


def mcp_headers(session_id: str | None, extra: dict[str, str] | None = None) -> dict[str, str]:
    """MCP 出站头：Accept 双形态 + 会话头（stateful server 的 Mcp-Session-Id；None=不带）。

    ⚠本函数是 platform / user 两条支路**共用**的协议层合并点：**严禁**在此注入 x-ec2-ip、Cookie
    或任何身份/拓扑信息——在这里加等于同时发给用户自注册的 MCP（URL 由用户任填）。来源相关的头
    一律由调用方经 `extra` 显式传入（平台侧 host_ip.ec2_ip_headers()，用户侧不传）。
    """
    hdrs = dict(extra or {})
    hdrs["Accept"] = _MCP_ACCEPT
    if session_id:
        hdrs["Mcp-Session-Id"] = session_id
    return hdrs


async def mcp_initialize(cli: Any, server_url: str, extra_headers: dict[str, str] | None = None) -> str | None:
    """MCP streamable-HTTP 会话握手（2026-07-14 内网实测：严格 stateful server 必须先 initialize
    再 tools/list，否则不响应）：initialize → 取响应头 Mcp-Session-Id → notifications/initialized。

    返回 session id；stateless server（如 FastMCP stateless_http）无该头返回 None，后续不带头。
    initialized 通知的任何错误忽略——严格 SDK 需要它、部分 stateless 实现会 4xx 拒绝，两类都兼容。

    extra_headers：对端可能**在 initialize 阶段就校验**的头（当前只有平台侧 x-ec2-ip）。握手与业务
    请求的头必须对称：只在 tools/call 带、握手不带，会表现为「工具静默消失 / 每次调用都 4xx」——
    发现与调用三处外层都是 `except Exception: log.warning` 吞掉，且 direct 路由没有 debug 钩子，
    是最难定位的一类失败。默认 None ⇒ 用户支路与既有调用点零行为变化。"""
    r = await cli.post(server_url,
                       json={"jsonrpc": "2.0", "id": 0, "method": "initialize",
                             "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                        "clientInfo": {"name": "openops", "version": "1"}}},
                       headers=mcp_headers(None, extra_headers))
    raise_with_body(r)
    rpc = parse_mcp_response(r)
    if "error" in rpc:
        raise RuntimeError(f"MCP initialize 错误：{str(rpc['error'])[:200]}")
    sid = r.headers.get("Mcp-Session-Id")  # httpx 头大小写不敏感
    try:
        await cli.post(server_url, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                       headers=mcp_headers(sid, extra_headers))
    except Exception:  # noqa: BLE001 —— 通知失败不阻断（见 docstring）
        pass
    return sid


async def mcp_request(cli: Any, server_url: str, method: str, params: dict[str, Any],
                      session_id: str | None, extra_headers: dict[str, str] | None = None) -> Any:
    """单发 JSON-RPC 请求（带会话头若有）。返回原始 httpx 响应——调用方自行 raise/parse
    （调用面需先看 400/404 判会话过期做重握手，不能在这里一律抛）。"""
    return await cli.post(server_url,
                          json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                          headers=mcp_headers(session_id, extra_headers))


def console_tls_verify() -> bool | str:
    """console 是 https 内网证书：Python httpx 用 certifi CA（无公司内部 CA）会
    CERTIFICATE_VERIFY_FAILED（Windows curl 用系统证书库所以通）。三档：
    OPENOPS_TLS_CA_FILE=<公司CA.pem>（正解）＞ OPENOPS_TLS_INSECURE=1（联调临时，等 curl -k）＞ 默认 certifi。
    另一正解：pip install truststore 后 run.py 会自动注入系统证书库（见 run.py）。"""
    ca = os.getenv("OPENOPS_TLS_CA_FILE", "").strip()
    if ca:
        return ca
    return False if os.getenv("OPENOPS_TLS_INSECURE") == "1" else True


async def list_servers(user_id: str = "") -> list[dict[str, Any]]:
    """列注册表里 source=openops 的 MCP 服务器（29.3 `POST /obsv/agent/management/mcps/list/query`）。
    real 拉真 console（翻页取全、只留 active + 有 server_url）；mock 返回内置一个（配合 discover_tools 的 _TOOLS）。

    user_id=登录工号（=login_key）：机机态（告警诊断）无 cookie，
    console 靠此入参识别用户返回「平台+该用户自定义」的 server；空=不带该字段
    （平台资产对账等无用户语义路径，期望对端只返回平台 server——联调确认项①）。
    """
    if os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() == "real":
        from infra.request_context import expand_host

        base = expand_host(os.getenv("OPENOPS_MCPREGISTRY_BASE_URL") or "")
        if not base:
            raise RuntimeError("OPENOPS_MCPREGISTRY=real 需配 OPENOPS_MCPREGISTRY_BASE_URL（29.3 未联）")
        import httpx

        url = f"{base.rstrip('/')}{console_api_prefix()}/mcps/list/query"
        out: list[dict[str, Any]] = []
        async with httpx.AsyncClient(**console_client_kwargs(base, "OPENOPS_MCPREGISTRY_COOKIE")) as cli:
            page, page_size = 1, 50
            while True:
                body_req: dict[str, Any] = {"page": page, "page_size": page_size, "source": "openops"}
                # 键名 user_id（2026-08-17 对端定案，userId 已废）；每页都带（翻页循环内）。
                # 调用方没给就用服务账号兜底——后台对账无 cookie 无 user_id 会被判 401（§1.3）。
                _uid = user_id or console_service_user_id()
                if _uid:
                    body_req["user_id"] = _uid
                r = await cli.post(url, json=body_req)
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


MCP_TRANSPORTS: tuple[str, ...] = ("jsonrpc", "sse", "streamable_http")  # 29.9 §3.1；非法值上游回 1005


def _registry_base() -> str:
    """MCP Registry host 根（real 模式必配，未配即 fail-loud）。三处 real 分支共用。"""
    from infra.request_context import expand_host

    base = expand_host(os.getenv("OPENOPS_MCPREGISTRY_BASE_URL") or "")
    if not base:
        raise RuntimeError("OPENOPS_MCPREGISTRY=real 需配 OPENOPS_MCPREGISTRY_BASE_URL（29.3 未联）")
    return base.rstrip("/")


async def register_server(*, server_name: str, server_url: str, description: str = "",
                          version: str = "1.0.0", category: str = "", tags: list[str] | None = None,
                          source: str = "openops", is_system: bool = False,
                          transport: str = "streamable_http") -> dict[str, Any]:
    """注册 MCP Server（29.9 §3.1 `POST /mcps/register`，成功 HTTP 201）→ data{server_id, server_name, …}。

    `server_name` 兼作 `server_id`（对端契约），必须唯一；已存在回 2001。
    `is_system=True`（平台级）需 console 超级管理员权限，否则 1003。
    mock：合成成功信封，管理台注册链路离线端到端可跑（无需 monkeypatch）。"""
    if os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() == "real":
        base = _registry_base()
        import httpx

        body: dict[str, Any] = {
            "server_name": server_name, "server_url": server_url, "source": source,
            "is_system": is_system, "transport": transport,
        }
        for k, v in (("description", description), ("version", version), ("category", category)):
            if v:
                body[k] = v
        if tags:
            body["tags"] = tags
        try:
            async with httpx.AsyncClient(**console_client_kwargs(base, "OPENOPS_MCPREGISTRY_COOKIE")) as cli:
                r = await cli.post(f"{base}{console_api_prefix()}/mcps/register", json=body)
        except httpx.HTTPError as e:  # 传输层不是 RuntimeError，不收口会漏成 500（同 skill_hub upload）
            raise McpRegistryError("network", f"MCP Registry 不可达：{type(e).__name__}: {e}") from None
        raise_biz_or_http(r, McpRegistryError)  # 非 2xx：优先解信封业务码（2001/1003/1005 随 4xx 返回也归 biz）
        return unwrap_console_data(r.json(), McpRegistryError, ok_codes=(0, 200, 201))
    return {"server_id": server_name, "server_name": server_name, "server_url": server_url,
            "description": description, "version": version, "category": category, "tags": tags or [],
            "transport": transport, "status": "active", "is_system": is_system, "source": source}


async def delete_server(server_id: str) -> dict[str, Any]:
    """删除 MCP Server（29.9 §3.6 `POST /mcps/delete`，**物理删除不可恢复**）→ data{server_id, deleted_by, deleted_at}。

    异常分类供调用方降级判定（同 skill_hub_client.delete_skill）：kind="http" 且 404=对端接口未上线；
    "biz" 1002=资源不存在/无权限、1003=系统级需超管；"network"=不可达可重试。
    mock：直接回成功（本地删照走，离线端到端闭环）。"""
    if os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() == "real":
        base = _registry_base()
        import httpx

        try:
            async with httpx.AsyncClient(**console_client_kwargs(base, "OPENOPS_MCPREGISTRY_COOKIE")) as cli:
                r = await cli.post(f"{base}{console_api_prefix()}/mcps/delete", json={"server_id": server_id})
        except httpx.HTTPError as e:
            raise McpRegistryError("network", f"MCP Registry 不可达：{type(e).__name__}: {e}") from None
        raise_biz_or_http(r, McpRegistryError)
        return unwrap_console_data(r.json(), McpRegistryError)
    return {"server_id": server_id, "deleted_by": "mock", "deleted_at": "2026-01-01 00:00:00"}


async def get_mcp_detail(server_id: str) -> dict[str, Any]:
    """MCP Server 详情（29.3 §3.3 `POST /mcps/detail/query`）→ {description, server_url, transport,
    version, category, tags, status, …}。供插件页「说明」展示真详情；只在用户点开时调（会递增 access_count）。"""
    if os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() == "real":
        from infra.request_context import expand_host

        base = expand_host(os.getenv("OPENOPS_MCPREGISTRY_BASE_URL") or "")
        if not base:
            raise RuntimeError("OPENOPS_MCPREGISTRY=real 需配 OPENOPS_MCPREGISTRY_BASE_URL（29.3 未联）")
        import httpx

        async with httpx.AsyncClient(**console_client_kwargs(base, "OPENOPS_MCPREGISTRY_COOKIE")) as cli:
            r = await cli.post(f"{base.rstrip('/')}{console_api_prefix()}/mcps/detail/query",
                               json={"server_id": server_id})
            raise_with_body(r)
            body = r.json()
            if int(body.get("code", -1)) not in (0, 200):  # 2026-07-13 对端统一 200；0 兼容旧版
                raise RuntimeError(f"mcps/detail/query 业务错误：code={body.get('code')} {body.get('message', '')}")
            return body.get("data") or {}
    return {"server_id": server_id, "server_name": server_id, "server_url": "http://mock",
            "description": "mock MCP 服务（离线合成详情）", "transport": "jsonrpc", "status": "active"}


def is_placeholder_endpoint(server_url: str) -> bool:
    """已知占位 endpoint（空 / host=mock）——seed 的 demo MCP 资产 endpoint 就是 "http://mock"，
    真发给 console proxy 会让网关去连 http://mock → 504（reconcile 登录触发即中招）。
    口径三处共用：discover_tools（走内置工具）、asset_reconcile_service、_user_mcp_specs（不出网）。
    """
    from urllib.parse import urlparse

    return not server_url or urlparse(server_url).hostname == "mock"


async def discover_tools(server_url: str, extra_headers: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """平台 MCP `tools/list`（29.3 §4.1 Proxy）。real 经 `POST /obsv/agent/management/mcps/proxy` 转发到目标 MCP server。

    `server_url` = 平台 MCP 资产的 endpoint（目标 MCP server URL，proxy 必填 `url`）；mock 忽略它返回硬编码 `_TOOLS`。
    OpenOps 侧自算 schema_hash（29.3 分工：Registry 不做发现，OpenOps 落 catalog）。

    `extra_headers`：**仅平台调用点**传 `host_ip.ec2_ip_headers()`（agentscope_runtime._dynamic_mcp_specs、
    asset_reconcile_service）。默认 None = 不带，是**承重的 fail-closed 默认值**：用户自注册 MCP 的 URL
    由用户任填，带上等于把后端主机内网 IP 送给任意外部地址（同 28.2「用户支路不透传 Cookie」的理由），
    且发现路径每轮无条件出网，泄露发生在任何审批/标注之前。新增调用点漏传只会「少带一个头」——
    **勿把默认值改成带头**。
    """
    if os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() == "real":
        # 占位符直接走内置工具；其它 URL 照走 real 校验链（无 BASE_URL 仍 fail-loud，EXT-007）
        if is_placeholder_endpoint(server_url):
            return [{**t, "readonly": t.get("readonly", False), "schema_hash": _schema_hash(t["input_schema"])}
                    for t in _TOOLS]
        import httpx

        if mcp_route() == "direct":  # 标准 MCP streamable-HTTP 直连 server_url（实测通；无需 console cookie）
            async with httpx.AsyncClient(timeout=15, verify=console_tls_verify(), trust_env=http_trust_env()) as cli:
                # 严格 stateful server 必须先握手（发现是一次性动作，不缓存会话）
                sid = await mcp_initialize(cli, server_url, extra_headers)
                r = await mcp_request(cli, server_url, "tools/list", {}, sid, extra_headers)
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
            async with httpx.AsyncClient(**console_client_kwargs(base, "OPENOPS_MCPREGISTRY_COOKIE")) as cli:
                # extra_headers 作 per-request 头（与 client 级的 Cookie/UA/CSRF 合并，不覆盖）。
                # 能力边界：proxy 路由下该头只到达 console 网关，**能否前递给目标 MCP 取决于 console 侧实现**
                # ——本特性的端到端保证仅在 OPENOPS_MCP_ROUTE=direct（默认）下成立。
                r = await cli.post(url, json={"url": server_url, "method": "tools/list", "params": {}},
                                   headers=extra_headers or None)
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
