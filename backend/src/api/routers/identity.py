from __future__ import annotations

import os

from fastapi import APIRouter, Request

from api.deps import AnyUser
from api.responses import ok
from app import identity_service
from domain.errors import ApiError, Err
from infra.external import iam_client

router = APIRouter(prefix="/api/openops/v1", tags=["identity"])


@router.get("/me")
async def me(user: AnyUser):
    # /me 对未白名单用户也返回（前端据此分流到 /not-whitelisted）
    return ok(await identity_service.me(user))


@router.get("/me/profile")
async def profile(user: AnyUser):
    if not user["whitelisted"]:
        raise ApiError(Err.NOT_WHITELISTED, "尚未开通 OpenOps")
    return ok(user)


@router.post("/auth/logout")
async def logout(request: Request):
    """登出（B9，老项目 d4.1 口径）：清本 cookie 的 IAM TokenCache；IAM signout 由前端按返回配置
    自行调用（可选 env）。不要求登录态——过期会话也能登出。"""
    cookie = request.headers.get("cookie")
    if cookie:
        iam_client.clear_cache(cookie)
    host = iam_client.browser_host(request)
    signout = os.getenv("OPENOPS_IAM_SIGNOUT_URL", "").strip()
    if host:
        signout = signout.replace("{host}", host)
    return ok({
        "signout_url": signout or None,
        "login_url": iam_client.login_url(host),
    })
