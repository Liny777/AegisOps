from __future__ import annotations

from fastapi import APIRouter

from api.deps import User
from api.responses import ok
from app import model_asset_service, model_template_service, secret_model_gateway
from domain.schemas import CreateLlmConfigRequest, CreateSecretRequest, TestConnectionRequest

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


@router.get("/models/templates")
async def list_model_templates(user: User):
    """按当前用户授权过滤的模型模板（38 号：主、子槽位模型均需授权，fail-closed）；
    行内带主/子模型展示信息与 is_default，InitWizard「选一套模板」数据源。"""
    return ok(await model_template_service.list_available(user))


@router.post("/llm-configs:test-connection")
async def test_llm_connection(req: TestConnectionRequest, _user: User):
    """存前「测试连接」：egress 校验 + tool-calling 探测，不落库。返回 {ok, supports_tool_calling, reason}。"""
    return ok(await secret_model_gateway.test_connection(req))


@router.post("/llm-configs")
async def create_llm(req: CreateLlmConfigRequest, user: User):
    return ok(await secret_model_gateway.create_llm_config(user, req))
