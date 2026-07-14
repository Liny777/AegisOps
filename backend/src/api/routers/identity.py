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


@router.get("/whitelist")
async def whitelist_query(user_id: str | None = None):
    """白名单开放查询（**免 IAM/token 鉴权**，老项目 1cd7ef0 口径）：外部系统（告警面板
    拼外链 ?q= 前）判断用户是否已开通。只读；全量只出 user_id/display_name，点查只出
    是否开通——不含 role/last_login 等内部字段。写操作仍走 /admin/users/whitelist
    （platform_admin 会话鉴权），本端点无任何写能力。

    - GET /whitelist            → {users: [{user_id, display_name}]}（active 未过期）
    - GET /whitelist?user_id=x  → {user_id, whitelisted}
    """
    if user_id is not None:
        uid = user_id.strip()
        if not uid:
            raise ApiError(Err.VALIDATION_FAILED, "user_id 不能为空")
        return ok({"user_id": uid, "whitelisted": await identity_service.check_whitelist(uid)})
    return ok({"users": await identity_service.whitelist_overview()})


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
