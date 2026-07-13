"""鉴权依赖：登录态 → DB 角色/白名单（事实在 PG）。

B9：`OPENOPS_IAM_ENABLED=true` 时走公司 IAM 双步握手（infra/external/iam_client，
老项目 D4 机制）——取请求 Cookie 整串校验，login_key 作 user_id；401 响应带
login_url（如配 OPENOPS_IAM_LOGIN_URL）供前端跳登录。默认 false 走
X-OpenOps-Mock-* 头（本地/pytest 零回归）。白名单/角色裁决两种模式同一条链。
"""
from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import unquote

from fastapi import Depends, Header, Request

from app import identity_service
from domain.errors import ApiError, Err
from infra import request_context
from infra.external import iam_client


def _client_ip(request: Request) -> str | None:
    """XFF 首跳（IAM 绑 IP 校验用）；无代理链回退直连地址。"""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


async def current_user(
    request: Request,
    x_openops_mock_user: Annotated[str | None, Header()] = None,
    x_openops_mock_name: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    if iam_client.enabled():  # B9 真 IAM：cookie 双步握手（TokenCache TTL 内不重打）
        host = iam_client.extract_host(request)  # {host} 占位符替换源（多域名共用配置）
        cookie = request.headers.get("cookie")
        if not cookie:
            raise ApiError(Err.UNAUTHORIZED, "未登录（缺少 IAM Cookie）",
                           extra={"login_url": iam_client.login_url(host)})
        try:
            ident = await iam_client.verify(cookie, _client_ip(request), host)
        except iam_client.IamError as e:
            if e.status == 401:
                raise ApiError(Err.UNAUTHORIZED, e.message,
                               extra={"login_url": iam_client.login_url(host)}) from None
            raise ApiError(Err.IAM_UPSTREAM, e.message, retryable=True) from None
        request_context.set_user_cookie(cookie)  # 出站 console 系调用透传用户登录态（同一 IAM 体系）
        return await identity_service.resolve_user(ident["login_key"], ident["display_name"])

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
