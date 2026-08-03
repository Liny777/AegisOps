from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from api.deps import Admin
from api.responses import ok
from app import (
    audit_trace_service,
    identity_service,
    mcp_tool_annotation_service,
    model_asset_service,
    model_template_service,
    runtime_config_service,
    sandbox_admin_service,
    template_service,
)
from domain.schemas import (
    CreateModelTemplateRequest,
    ModelGrantsRequest,
    ModelStatusRequest,
    ModelTemplateActionRequest,
    ModelTemplateStatusRequest,
    RegisterModelAssetRequest,
    TestModelAssetRequest,
    SandboxDestroyRequest,
    SaveTemplateVersionRequest,
    SetRoleRequest,
    SetTagsRequest,
    TemplateVersionActionRequest,
    UpdateModelAssetRequest,
    UpdateModelTemplateRequest,
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
async def list_users(
    _admin: Admin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),  # 上限对齐 29.3 §2.2
    q: str | None = Query(default=None, max_length=200),  # 按 user_id/display_name 模糊搜（服务端过滤）
    tag: str | None = Query(default=None, max_length=100),  # 按领域标签精确过滤（与 q 为 AND）
):
    """分页 + 搜索 + 标签过滤 → {items,total,page,page_size}。"""
    return ok(await identity_service.list_users(page=page, page_size=page_size, q=q, tag=tag))


@router.get("/users/tags")
async def list_user_tags(_admin: Admin):
    """标签下拉候选：所有未删用户已用的领域标签（去重、排序）。"""
    return ok(await identity_service.list_user_tags())


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, admin: Admin):
    """删除用户（软删 + 连带撤白名单；写 user.deleted 审计）。不能删自己（防管理面锁死）。"""
    await identity_service.delete_user(user_id, admin["user_id"])
    return ok({"deleted": True})


@router.post("/users/whitelist")
async def add_whitelist(req: WhitelistRequest, admin: Admin):
    await identity_service.add_whitelist(req.user_id, req.display_name, req.role, admin["user_id"], tags=req.tags)
    return ok({"added": True})


@router.post("/users/whitelist:revoke")
async def revoke_whitelist(req: WhitelistRequest, admin: Admin):
    await identity_service.revoke_whitelist(req.user_id, admin["user_id"])
    return ok({"revoked": True})


@router.post("/users/{user_id}:set-role")
async def set_role(user_id: str, req: SetRoleRequest, admin: Admin):
    """升/降级已有用户（B7·三补链）：role ∈ user|platform_admin；不能改自己；写 role.changed 审计。"""
    return ok(await identity_service.set_role(user_id, req.role, admin["user_id"]))


@router.post("/users/{user_id}:set-tags")
async def set_tags(user_id: str, req: SetTagsRequest, admin: Admin):
    """改领域标签（整体替换，[] 清空）；写 user.tags_changed 审计。"""
    return ok(await identity_service.set_tags(user_id, req.tags, admin["user_id"]))


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


@router.post("/sandbox:reclaim")
async def reclaim_idle_runs(_admin: Admin):
    """手动触发一次无活动 run 回收（运维/验证用；后台 sweep 循环也会定期执行）。"""
    return ok({"reclaimed": await sandbox_admin_service.reclaim_once()})


# ---- 模型资产与白名单授权（B7，30.6 五；替代旧 /models 端点） ----
@router.get("/model-assets")
async def model_assets_list(_admin: Admin):
    return ok(await model_asset_service.admin_list())


@router.post("/model-assets:test-connection")
async def model_assets_test_connection(req: TestModelAssetRequest, _admin: Admin):
    """平台模型注册前「测试连接」：Key 由服务器读 secret_env_var 环境变量探测，不落库。"""
    return ok(await model_asset_service.test_connection(req))


@router.post("/model-assets")
async def model_assets_register(req: RegisterModelAssetRequest, admin: Admin):
    return ok(await model_asset_service.register(req, admin["user_id"]))


@router.put("/model-assets/{model_asset_id}:status")
async def model_assets_status(model_asset_id: str, req: ModelStatusRequest, admin: Admin):
    await model_asset_service.set_status(model_asset_id, req.status, admin["user_id"])
    return ok({"status": req.status})


# 注意：本路由必须留在 `:status` 之后——路径参数不匹配 `/` 但**匹配 `:`**，先注册的话
# `/model-assets/abc:status` 会被它吃掉（model_asset_id="abc:status"）。`/grants` 带 `/`，不受影响。
@router.put("/model-assets/{model_asset_id}")
async def model_assets_update(model_asset_id: str, req: UpdateModelAssetRequest, admin: Admin):
    """更新模型资产连接配置（PATCH 语义，只改请求体里显式给出的键）。"""
    return ok(await model_asset_service.update(model_asset_id, req, admin["user_id"]))


@router.get("/model-assets/{model_asset_id}/grants")
async def model_assets_grants(model_asset_id: str, _admin: Admin):
    return ok(await model_asset_service.get_grants(model_asset_id))


@router.put("/model-assets/{model_asset_id}/grants")
async def model_assets_save_grants(model_asset_id: str, req: ModelGrantsRequest, admin: Admin):
    return ok(await model_asset_service.save_grants(model_asset_id, req, admin["user_id"]))


# ---- 模型模板（38 号：主/子 Agent 槽位模型编排；用户初始化「选一套模板」的管理侧数据源） ----
@router.get("/model-templates")
async def model_templates_list(_admin: Admin):
    return ok(await model_template_service.admin_list())


@router.post("/model-templates")
async def model_templates_create(req: CreateModelTemplateRequest, admin: Admin):
    return ok(await model_template_service.create(req, admin["user_id"]))


@router.put("/model-templates/{model_template_id}:status")
async def model_templates_status(model_template_id: str, req: ModelTemplateStatusRequest, admin: Admin):
    await model_template_service.set_status(model_template_id, req.status, admin["user_id"])
    return ok({"status": req.status})


@router.post("/model-templates/{model_template_id}:set-default")
async def model_templates_set_default(model_template_id: str, _req: ModelTemplateActionRequest, admin: Admin):
    return ok(await model_template_service.set_default(model_template_id, admin["user_id"]))


# 注意：与 /model-assets 同款冒号坑（见上方注释）——PUT /{id} 必须留在 `:status` 之后
@router.put("/model-templates/{model_template_id}")
async def model_templates_update(model_template_id: str, req: UpdateModelTemplateRequest, admin: Admin):
    """更新模型模板（PATCH 语义，只改请求体里显式给出的键）。"""
    return ok(await model_template_service.update(model_template_id, req, admin["user_id"]))


@router.get("/audit/recent")
async def audit_recent(_admin: Admin):
    return ok(await audit_trace_service.admin_recent())
