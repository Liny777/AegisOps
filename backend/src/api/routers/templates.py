from __future__ import annotations

from fastapi import APIRouter

from api.deps import User
from api.responses import ok
from app import template_service
from domain.schemas import CreateWorkspaceRequest
from infra.external import omodel_client

router = APIRouter(prefix="/api/openops/v1", tags=["templates"])


@router.get("/templates/available")
async def available(_user: User):
    return ok(await template_service.available())


# workspace（oModel mock 透传；EXT-001/002 契约面）
@router.get("/workspaces")
async def list_workspaces(_user: User):
    return ok(await omodel_client.list_workspaces())


@router.post("/workspaces")
async def create_workspace(req: CreateWorkspaceRequest, _user: User):
    return ok(await omodel_client.create_workspace(req.name, req.app_ids))


@router.get("/workspaces/{workspace_id}/status")
async def workspace_status(workspace_id: str, _user: User):
    ws = await omodel_client.get_workspace(workspace_id)
    from domain.errors import ApiError, Err  # noqa: PLC0415

    if ws is None:
        raise ApiError(Err.NOT_FOUND, "workspace 不存在")
    return ok({"workspace_id": workspace_id, "sync_status": ws["sync_status"], "scope_revision": ws["scope_revision"]})
