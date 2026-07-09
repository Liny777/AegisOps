from __future__ import annotations

from fastapi import APIRouter

from api.deps import User
from api.responses import ok
from app import model_asset_service, secret_model_gateway
from domain.schemas import CreateLlmConfigRequest, CreateSecretRequest

router = APIRouter(prefix="/api/openops/v1", tags=["secrets"])


@router.get("/secrets")
async def list_secrets(user: User):
    return ok(await secret_model_gateway.list_secrets(user))


@router.post("/secrets")
async def create_secret(req: CreateSecretRequest, user: User):
    return ok(await secret_model_gateway.create_secret(user, req))


@router.get("/llm-configs")
async def list_llm(user: User):
    return ok(await secret_model_gateway.list_llm_configs(user))


@router.get("/models/platform")
async def list_platform_models(user: User):
    """按当前用户授权过滤的平台模型（B7 模型 ACL：scope=all ∪ 有 active grant）。"""
    return ok(await model_asset_service.list_available(user))


@router.post("/llm-configs")
async def create_llm(req: CreateLlmConfigRequest, user: User):
    return ok(await secret_model_gateway.create_llm_config(user, req))
