from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.deps import Admin
from api.responses import ok
from app import (
    audit_trace_service,
    identity_service,
    mcp_tool_annotation_service,
    model_asset_service,
    runtime_config_service,
    sandbox_admin_service,
    template_service,
)
from domain.schemas import (
    ModelGrantsRequest,
    ModelStatusRequest,
    RegisterModelAssetRequest,
    SandboxDestroyRequest,
    SaveTemplateVersionRequest,
    SetRoleRequest,
    TemplateVersionActionRequest,
    UpdateRuntimeConfigRequest,
    WhitelistRequest,
)

router = APIRouter(prefix="/api/openops/v1/admin", tags=["admin"])


@router.get("/templates")
async def templates(_admin: Admin):
    return ok(await template_service.admin_list())


# ---- 模板版本写闭环（B7·二，21 号契约） ----
@router.get("/templates/{template_id}")
async def template_detail(template_id: str, _admin: Admin):
    return ok(await template_service.admin_detail(template_id))


@router.post("/templates/{template_id}/versions")
async def save_template_version(template_id: str, req: SaveTemplateVersionRequest, admin: Admin):
    return ok(await template_service.save_draft(template_id, req.content_json, admin["user_id"]))


@router.post("/template-versions/{template_version_id}:publish")
async def publish_template_version(template_version_id: str, _req: TemplateVersionActionRequest, admin: Admin):
    return ok(await template_service.publish(template_version_id, admin["user_id"]))


@router.post("/template-versions/{template_version_id}:disable")
async def disable_template_version(template_version_id: str, _req: TemplateVersionActionRequest, admin: Admin):
    await template_service.disable_version(template_version_id, admin["user_id"])
    return ok({"disabled": True})


@router.get("/mcp-tools")
async def mcp_tools(_admin: Admin):
    return ok(await mcp_tool_annotation_service.list_catalog())


@router.put("/mcp-tools/{tool_catalog_id}/annotation")
async def save_annotation(tool_catalog_id: str, payload: dict[str, Any], admin: Admin):
    await mcp_tool_annotation_service.save(tool_catalog_id, payload, admin["user_id"])
    return ok({"saved": True})


@router.get("/users")
async def list_users(_admin: Admin):
    return ok(await identity_service.list_users())


@router.post("/users/whitelist")
async def add_whitelist(req: WhitelistRequest, admin: Admin):
    await identity_service.add_whitelist(req.user_id, req.display_name, req.role, admin["user_id"])
    return ok({"added": True})


@router.post("/users/whitelist:revoke")
async def revoke_whitelist(req: WhitelistRequest, admin: Admin):
    await identity_service.revoke_whitelist(req.user_id, admin["user_id"])
    return ok({"revoked": True})


@router.post("/users/{user_id}:set-role")
async def set_role(user_id: str, req: SetRoleRequest, admin: Admin):
    """升/降级已有用户（B7·三补链）：role ∈ user|platform_admin；不能改自己；写 role.changed 审计。"""
    return ok(await identity_service.set_role(user_id, req.role, admin["user_id"]))


@router.get("/sandbox")
async def sandbox(_admin: Admin):
    return ok(await runtime_config_service.get_sandbox())


@router.put("/sandbox")
async def update_sandbox(req: UpdateRuntimeConfigRequest, admin: Admin):
    await runtime_config_service.update_sandbox(req.updates, req.reason, admin["user_id"])
    return ok({"saved": True})


@router.get("/sandbox/containers")
async def sandbox_containers(_admin: Admin):
    """当前用户容器列表（B8-4：runtime_status / active_run_count / image / idle）。"""
    return ok(await sandbox_admin_service.list_containers())


@router.post("/sandbox/containers/{target_user_id}:destroy")
async def destroy_sandbox_container(target_user_id: str, req: SandboxDestroyRequest, admin: Admin):
    """强制销毁指定用户容器（中断运行中 run；写审计 + 推用户可见事件）。"""
    return ok(await sandbox_admin_service.destroy_container(target_user_id, req.reason, admin["user_id"]))


# ---- 模型资产与白名单授权（B7，30.6 五；替代旧 /models 端点） ----
@router.get("/model-assets")
async def model_assets_list(_admin: Admin):
    return ok(await model_asset_service.admin_list())


@router.post("/model-assets")
async def model_assets_register(req: RegisterModelAssetRequest, admin: Admin):
    return ok(await model_asset_service.register(req, admin["user_id"]))


@router.put("/model-assets/{model_asset_id}:status")
async def model_assets_status(model_asset_id: str, req: ModelStatusRequest, admin: Admin):
    await model_asset_service.set_status(model_asset_id, req.status, admin["user_id"])
    return ok({"status": req.status})


@router.get("/model-assets/{model_asset_id}/grants")
async def model_assets_grants(model_asset_id: str, _admin: Admin):
    return ok(await model_asset_service.get_grants(model_asset_id))


@router.put("/model-assets/{model_asset_id}/grants")
async def model_assets_save_grants(model_asset_id: str, req: ModelGrantsRequest, admin: Admin):
    return ok(await model_asset_service.save_grants(model_asset_id, req, admin["user_id"]))


@router.get("/audit/recent")
async def audit_recent(_admin: Admin):
    return ok(await audit_trace_service.admin_recent())
