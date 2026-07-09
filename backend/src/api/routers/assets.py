from __future__ import annotations

from fastapi import APIRouter

from api.deps import User
from api.responses import ok
from app import asset_reconcile_service, asset_registry_service
from domain.schemas import RegisterMcpRequest, UploadSkillRequest

router = APIRouter(prefix="/api/openops/v1/assets", tags=["assets"])


@router.post(":reconcile")
async def reconcile(_user: User):
    """配置页 refresh：立即对账 Skill Hub / MCP Registry（28.7）。"""
    return ok(await asset_reconcile_service.reconcile(force=True, trigger="refresh"))


@router.get("/skills")
async def list_skills(user: User):
    return ok(await asset_registry_service.list_skills(user))


@router.post("/skills")
async def upload_skill(req: UploadSkillRequest, user: User):
    return ok(await asset_registry_service.upload_skill(user, req))


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str, user: User):
    await asset_registry_service.delete_skill(user, skill_id)
    return ok({"deleted": True})


@router.get("/mcps")
async def list_mcps(user: User):
    return ok(await asset_registry_service.list_mcps(user))


@router.post("/mcps")
async def register_mcp(req: RegisterMcpRequest, user: User):
    return ok(await asset_registry_service.register_mcp(user, req))


@router.delete("/mcps/{mcp_id}")
async def delete_mcp(mcp_id: str, user: User):
    await asset_registry_service.delete_mcp(user, mcp_id)
    return ok({"deleted": True})
