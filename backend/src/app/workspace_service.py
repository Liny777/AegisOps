"""Workspace（oModel 契约面，EXT-001/002）：包 omodel_client，router 只调本服务（22 号分层）。"""
from __future__ import annotations

from typing import Any

from domain.errors import ApiError, Err
from infra.external import omodel_client


async def list_workspaces() -> list[dict[str, Any]]:
    return await omodel_client.list_workspaces()


async def create_workspace(name: str, app_ids: list[str]) -> dict[str, Any]:
    return await omodel_client.create_workspace(name, app_ids)


async def status(workspace_id: str) -> dict[str, Any]:
    ws = await omodel_client.get_workspace(workspace_id)
    if ws is None:
        raise ApiError(Err.NOT_FOUND, "workspace 不存在")
    return {
        "workspace_id": workspace_id,
        "sync_status": ws["sync_status"],
        "scope_revision": ws["scope_revision"],
    }
