from __future__ import annotations

from fastapi import APIRouter

from api.deps import AnyUser
from api.responses import ok
from app import identity_service
from domain.errors import ApiError, Err

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
