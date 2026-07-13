"""鉴权依赖：登录态 → DB 角色/白名单（事实在 PG）。

B9：`OPENOPS_IAM_ENABLED=true` 时走公司 IAM 双步握手（infra/external/iam_client，
老项目 D4 机制）——取请求 Cookie 整串校验，login_key 作 user_id；401 响应带
login_url（如配 OPENOPS_IAM_LOGIN_URL）供前端跳登录。默认 false 走
X-OpenOps-Mock-* 头（本地/pytest 零回归）。白名单/角色裁决两种模式同一条链。
"""
from __future__ import annotations

import ipaddress
import os
from typing import Annotated, Any
from urllib.parse import unquote

from fastapi import Depends, Header, Request

from app import identity_service
from domain.errors import ApiError, Err
from infra import request_context
from infra.external import iam_client


def _client_ip(request: Request) -> str | None:
    """解析 IAM 绑 IP：只信显式可信代理，并从 XFF 右侧剥离可信代理链。

    非可信 peer 忽略 XFF 并使用真实 peer；可信链缺失/畸形或代理配置非法、过宽时返回
    None，由 IAM 路径在任何上游调用前 fail-closed。因此伪造的 XFF 首段不会被带出。
    """
    peer_text = request.client.host if request.client else ""
    try:
        peer = ipaddress.ip_address(peer_text)
    except ValueError:
        return None

    raw_cidrs = os.getenv("OPENOPS_TRUSTED_PROXY_CIDRS", "").strip()
    try:
        trusted = [ipaddress.ip_network(part.strip(), strict=False)
                   for part in raw_cidrs.split(",") if part.strip()]
        if not trusted or any(network.prefixlen < (8 if network.version == 4 else 16)
                              for network in trusted):
            return None if raw_cidrs else str(peer)
    except ValueError:
        return None

    def is_trusted(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return any(address.version == network.version and address in network for network in trusted)

    if not is_trusted(peer):
        return str(peer)
    xff = request.headers.get("x-forwarded-for", "")
    if not xff:
        return None
    try:
        forwarded = [ipaddress.ip_address(part.strip()) for part in xff.split(",")]
    except ValueError:
        return None
    for address in reversed(forwarded):
        if not is_trusted(address):
            return str(address)
    return None


async def current_user(
    request: Request,
    x_openops_mock_user: Annotated[str | None, Header()] = None,
    x_openops_mock_name: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    request_context.capture_request_host(request)  # console 系兼容路径及浏览器回跳的域名上下文
    resolved_client_ip = _client_ip(request)
    request_context.set_client_ip(resolved_client_ip or "")  # 出站 omodel 的 IAM 绑 IP 会话校验用
    if iam_client.enabled():  # B9 真 IAM：cookie 双步握手（TokenCache TTL 内不重打）
        host = iam_client.browser_host(request)  # login_url 的 {host} 源：仅 XFH，缺失留占位给前端填
        cookie = request.headers.get("cookie")
        if not cookie:
            raise ApiError(Err.UNAUTHORIZED, "未登录（缺少 IAM Cookie）",
                           extra={"login_url": iam_client.login_url(host)})
        if not resolved_client_ip:
            raise ApiError(Err.IAM_UPSTREAM, "无法从可信代理链确定客户端 IP", retryable=False)
        try:
            ident = await iam_client.verify(cookie, resolved_client_ip)
        except iam_client.IamError as e:
            if e.status == 401:
                raise ApiError(Err.UNAUTHORIZED, e.message,
                               extra={"login_url": iam_client.login_url(host)}) from None
            raise ApiError(Err.IAM_UPSTREAM, e.message, retryable=True) from None
        user = await identity_service.resolve_user(ident["login_key"], ident["display_name"])
        request_context.set_request_user(user["user_id"], cookie)  # 出站 console 系透传+按用户缓存登录态
        return user

    if not x_openops_mock_user:
        raise ApiError(Err.UNAUTHORIZED, "未登录")  # IAM-001
    name = unquote(x_openops_mock_name) if x_openops_mock_name else None  # 头仅 ISO-8859-1，前端 URI 编码
    return await identity_service.resolve_user(x_openops_mock_user, name)


async def whitelisted_user(user: Annotated[dict[str, Any], Depends(current_user)]) -> dict[str, Any]:
    if not user["whitelisted"]:
        raise ApiError(Err.NOT_WHITELISTED, "尚未开通 OpenOps，请联系管理员")  # IAM-002
    return user


async def admin_user(user: Annotated[dict[str, Any], Depends(whitelisted_user)]) -> dict[str, Any]:
    if user["role"] != "platform_admin":
        raise ApiError(Err.FORBIDDEN, "仅平台管理员可访问")  # IAM-003
    return user


User = Annotated[dict[str, Any], Depends(whitelisted_user)]
Admin = Annotated[dict[str, Any], Depends(admin_user)]
AnyUser = Annotated[dict[str, Any], Depends(current_user)]
