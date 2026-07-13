from __future__ import annotations

from fastapi import APIRouter

from api.deps import User
from api.responses import ok
from app import template_service, workspace_service
from domain.schemas import CreateWorkspaceRequest

router = APIRouter(prefix="/api/openops/v1", tags=["templates"])


@router.get("/templates/available")
async def available(_user: User):
    return ok(await template_service.available())


# workspace（oModel 契约面；EXT-001/002）——router 只调 workspace_service
@router.get("/apps")
async def list_apps(_user: User):
    """「从应用创建系统范围」选源：当前用户可见的应用（平铺）。"""
    return ok(await workspace_service.list_apps(_user["user_id"]))


@router.get("/workspaces")
async def list_workspaces(_user: User):
    return ok(await workspace_service.list_workspaces())


@router.post("/workspaces")
async def create_workspace(req: CreateWorkspaceRequest, _user: User):
    apps = [{"app_id": a.app_id, "name": a.name or "", "tenant_id": a.tenant_id or ""} for a in (req.apps or [])]
    return ok(await workspace_service.create_workspace(
        req.name, req.app_ids, apps=apps,
        owner=str(_user.get("display_name") or _user["user_id"]),
    ))


@router.get("/omodel/console-page")
async def omodel_console_page(_user: User):
    """omodel 控制台页面前缀（设置页 iframe；空串=未配置，前端显示空态）。"""
    return ok({"page_base": workspace_service.console_page_base()})


@router.get("/workspaces/{workspace_id}/status")
async def workspace_status(workspace_id: str, _user: User):
    return ok(await workspace_service.status(workspace_id))
