"""鉴权依赖：登录态（mock 头模拟 W3 Cookie）→ DB 角色/白名单（事实在 PG）。

B8 块换真 IAM 双步握手；依赖签名不变。
"""
from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import unquote

from fastapi import Depends, Header

from app import identity_service
from domain.errors import ApiError, Err


async def current_user(
    x_openops_mock_user: Annotated[str | None, Header()] = None,
    x_openops_mock_name: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
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
