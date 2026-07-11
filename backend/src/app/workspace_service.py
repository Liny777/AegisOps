"""Workspace（oModel 契约面，EXT-001/002）：包 omodel_client，router 只调本服务（22 号分层）。"""
from __future__ import annotations

from typing import Any

from domain.errors import ApiError, Err
from infra.external import apptree_client, omodel_client


async def list_apps(user_id: str) -> list[dict[str, Any]]:
    """「从应用创建系统范围」选源：该用户可见的应用（平铺）。

    失败包成 ApiError 带上游原因（401=cookie 失效 / 404=enterprise·project 段错 / 非 OK 信封），
    前端对话框直接显示——联调时"静默空列表"比报错更难定位。
    """
    try:
        return await apptree_client.list_user_apps(user_id)
    except ApiError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ApiError(Err.INTERNAL_ERROR, f"应用目录查询失败：{str(e)[:300]}", retryable=True) from e


async def list_workspaces() -> list[dict[str, Any]]:
    return await omodel_client.list_workspaces()


async def create_workspace(name: str, app_ids: list[str], *,
                           apps: list[dict[str, Any]] | None = None, owner: str = "") -> dict[str, Any]:
    """失败包成 ApiError 带上游原因（umodel 400 的信封 message 是唯一定位线索），前端对话框直接显示。"""
    try:
        return await omodel_client.create_workspace(name, app_ids, apps=apps, owner=owner)
    except ApiError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ApiError(Err.INTERNAL_ERROR, f"创建系统范围失败：{str(e)[:300]}", retryable=True) from e


async def status(workspace_id: str) -> dict[str, Any]:
    ws = await omodel_client.get_workspace(workspace_id)
    if ws is None:
        raise ApiError(Err.NOT_FOUND, "workspace 不存在")
    return {
        "workspace_id": workspace_id,
        "sync_status": ws["sync_status"],
        "scope_revision": ws["scope_revision"],
    }
