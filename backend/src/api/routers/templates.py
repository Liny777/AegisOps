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
@router.get("/workspaces")
async def list_workspaces(_user: User):
    return ok(await workspace_service.list_workspaces())


@router.post("/workspaces")
async def create_workspace(req: CreateWorkspaceRequest, _user: User):
    return ok(await workspace_service.create_workspace(req.name, req.app_ids))


@router.get("/workspaces/{workspace_id}/status")
async def workspace_status(workspace_id: str, _user: User):
    return ok(await workspace_service.status(workspace_id))
