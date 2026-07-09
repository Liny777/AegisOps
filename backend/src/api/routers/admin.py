from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.deps import Admin
from api.responses import ok
from app import audit_trace_service, mcp_tool_annotation_service, runtime_config_service, template_service
from domain.schemas import UpdateRuntimeConfigRequest, WhitelistRequest
from infra.repositories import users

router = APIRouter(prefix="/api/openops/v1/admin", tags=["admin"])


@router.get("/templates")
async def templates(_admin: Admin):
    return ok(await template_service.admin_list())


@router.get("/mcp-tools")
async def mcp_tools(_admin: Admin):
    return ok(await mcp_tool_annotation_service.list_catalog())


@router.put("/mcp-tools/{tool_catalog_id}/annotation")
async def save_annotation(tool_catalog_id: str, payload: dict[str, Any], admin: Admin):
    await mcp_tool_annotation_service.save(tool_catalog_id, payload, admin["user_id"])
    return ok({"saved": True})


@router.get("/users")
async def list_users(_admin: Admin):
    return ok([dict(r) for r in await users.list_users_with_whitelist()])


@router.post("/users/whitelist")
async def add_whitelist(req: WhitelistRequest, admin: Admin):
    await users.upsert_user(req.user_id, req.display_name or req.user_id, req.role)
    await users.add_whitelist(req.user_id, admin["user_id"])
    return ok({"added": True})


@router.get("/sandbox")
async def sandbox(_admin: Admin):
    return ok(await runtime_config_service.get_sandbox())


@router.put("/sandbox")
async def update_sandbox(req: UpdateRuntimeConfigRequest, admin: Admin):
    await runtime_config_service.update_sandbox(req.updates, req.reason, admin["user_id"])
    return ok({"saved": True})


@router.get("/models")
async def platform_models(_admin: Admin):
    return ok(await runtime_config_service.list_platform_models())


@router.post("/models")
async def register_model(model: dict[str, Any], admin: Admin):
    await runtime_config_service.register_platform_model(model, admin["user_id"])
    return ok({"registered": True})


@router.get("/audit/recent")
async def audit_recent(_admin: Admin):
    return ok(await audit_trace_service.admin_recent())
