"""Workspace（oModel 契约面，EXT-001/002）：包 omodel_client，router 只调本服务（22 号分层）。"""
from __future__ import annotations

import os
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


def console_page_base() -> str:
    """omodel 控制台页面前缀（设置页 iframe 用，前端追加 workspace_id）。

    OPENOPS_OMODEL_PAGE_URL 显式覆盖 > 从 OPENOPS_OMODEL_BASE_URL 域根派生
    `{base}/wesee/omodel/index.html?dataSource=api&workspace=`；两者皆空（mock/未配置）
    返回 ""——前端显示「内网环境可用」空态。清洗口径与 omodel_real._base 一致
    （剥地址栏 #fragment + 尾斜杠；用户常整串贴地址栏）。
    """
    override = os.environ.get("OPENOPS_OMODEL_PAGE_URL", "").strip()
    if override:
        return override
    from infra.request_context import expand_host

    raw = expand_host(os.environ.get("OPENOPS_OMODEL_BASE_URL", "").split("#", 1)[0].strip())
    if not raw or "{host}" in raw:  # 无请求上下文无法展开 → 空态（不下发字面占位符）
        return ""
    # 只取 scheme://host——BASE_URL 常带 API 路径后缀（如 .../omodel），页面在 host 根的
    # /wesee/omodel/index.html，不能拼在后缀之后（否则 /omodel/wesee/omodel/... 双前缀）
    from urllib.parse import urlparse

    u = urlparse(raw)
    if not u.scheme or not u.netloc:
        return ""
    return f"{u.scheme}://{u.netloc}/wesee/omodel/index.html?dataSource=api&workspace="
