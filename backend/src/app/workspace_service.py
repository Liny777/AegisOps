"""Workspace（oModel 契约面，EXT-001/002）：包 omodel_client，router 只调本服务（22 号分层）。"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from domain.errors import ApiError, Err
from infra.external import apptree_client, omodel_client
from infra.repositories import audit

log = logging.getLogger("openops.workspace")


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


def _map_omodel_error(e: "omodel_client.OModelError", *, action: str) -> ApiError:
    """OModelError → ApiError 的稳定映射（create/update/delete 共用）。

    action = 创建 / 更新 / 删除，只进文案；上游 status/request_id 只用于服务端诊断（extra），不回显原文。
    """
    extra = {"upstream_status": e.status_code or None,
             "upstream_request_id": e.request_id or None}
    if e.kind == "auth":
        return ApiError(Err.OMODEL_AUTH_FAILED,
                        "oModel 鉴权或同源校验失败，请刷新登录后重试；若持续失败请联系管理员",
                        extra=extra)
    if e.kind == "validation":
        return ApiError(Err.VALIDATION_FAILED,
                        f"oModel 拒绝 workspace {action}参数，请检查范围名称、当前企业和所选应用",
                        extra=extra)
    if e.kind == "timeout":
        return ApiError(Err.OMODEL_UPSTREAM, f"oModel {action}请求超时",
                        retryable=True, status=504, extra=extra)
    if e.kind == "config":
        return ApiError(Err.OMODEL_UPSTREAM, f"oModel 配置错误：{e.message}", extra=extra)
    retryable = e.status_code is None or e.status_code >= 500 or e.status_code < 400
    if e.status_code is not None and 400 <= e.status_code < 500:
        message = f"oModel 拒绝{action}请求（HTTP {e.status_code}）"
    elif e.status_code is not None and e.status_code >= 500:
        message = f"oModel 服务暂时不可用（HTTP {e.status_code}）"
    else:
        message = e.message
    return ApiError(Err.OMODEL_UPSTREAM, message, retryable=retryable, extra=extra)


async def create_workspace(name: str, app_ids: list[str], *,
                           apps: list[dict[str, Any]] | None = None, owner: str = "",
                           manual_app_ids: list[str] | None = None, reason: str = "",
                           user_id: str = "", user_role: str = "") -> dict[str, Any]:
    """创建系统范围；上游细节只用于服务端诊断，不向浏览器回显。

    manual_app_ids 非空 = 管理员代查通道（手输 APPID，未经 apptree 权限过滤）：
    仅 platform_admin 可用（否则审计会出现「非管理员的 admin_created」事件，污染留痕语义），
    reason 必填，创建成功后写 workspace.admin_created 审计（失败不留痕，同 set_role 只审计实际变更）。
    """
    manual = [a.strip() for a in (manual_app_ids or []) if a.strip()]
    if manual and user_role != "platform_admin":
        raise ApiError(Err.FORBIDDEN, "仅平台管理员可手动输入 APPID 创建范围")
    if manual and not reason.strip():
        raise ApiError(Err.VALIDATION_FAILED, "手动输入 APPID 需填写原因/工单号")
    try:
        ws = await omodel_client.create_workspace(name, app_ids, apps=apps, owner=owner)
    except ApiError:
        raise
    except omodel_client.OModelError as e:
        raise _map_omodel_error(e, action="创建") from e
    except Exception as e:  # noqa: BLE001
        raise ApiError(Err.INTERNAL_ERROR, f"创建系统范围失败：{str(e)[:300]}", retryable=True) from e
    if manual:
        await audit.insert_event(  # 管理面动作必审计（B7·三）
            audit_trace_id=str(uuid.uuid4()), event_type="workspace.admin_created",
            user_id=user_id, actor_type="user", action="manual_appid_scope",
            payload_redacted={"workspace_id": ws.get("workspace_id"), "name": name,
                              "app_ids": app_ids, "manual_app_ids": manual,
                              "reason": reason.strip()})
    return ws


async def get(workspace_id: str) -> dict[str, Any]:
    """系统范围详情（编辑向导预填用）：含 name + app_ids（status() 只投影就绪态，故另开一支）。"""
    ws = await omodel_client.get_workspace(workspace_id)
    if ws is None:
        raise ApiError(Err.NOT_FOUND, "workspace 不存在")
    return {
        "workspace_id": ws["workspace_id"],
        "name": ws["name"],
        "app_ids": ws["app_ids"],
        "scope_revision": ws["scope_revision"],
        "sync_status": ws["sync_status"],
    }


async def update_workspace(name: str, app_ids: list[str], *, workspace_id: str,
                           apps: list[dict[str, Any]] | None = None, owner: str = "") -> dict[str, Any]:
    """更新系统范围（改名 + 重选应用）：apps/app_ids 全量覆盖 scopes。不存在 → NOT_FOUND。"""
    try:
        ws = await omodel_client.update_workspace(workspace_id, name, app_ids, apps=apps, owner=owner)
    except ApiError:
        raise
    except omodel_client.OModelError as e:
        raise _map_omodel_error(e, action="更新") from e
    except Exception as e:  # noqa: BLE001
        raise ApiError(Err.INTERNAL_ERROR, f"更新系统范围失败：{str(e)[:300]}", retryable=True) from e
    if ws is None:
        raise ApiError(Err.NOT_FOUND, "workspace 不存在")
    # 范围内容变更闭环（2026-08-19：改范围后告警仍按旧 scope 接管）：①引用实例的
    # revision 推进到新值——恢复「内容变则版本变」，scope 缓存键翻新、旧快照兜底的
    # revision 校验得以生效；②清本进程 scope 缓存——不再硬等 30s TTL。
    from app import scope_service
    from infra.repositories import agent_teams

    bumped = await agent_teams.bump_scope_revision_by_workspace(
        workspace_id, str(ws.get("scope_revision") or ""), owner or "system")
    scope_service.invalidate_workspace(workspace_id)
    if bumped:
        log.info("[workspace] 范围变更已推进 %d 个引用实例的 scope_revision ws=%s", bumped, workspace_id)
    return ws


async def delete_workspace(workspace_id: str) -> dict[str, Any]:
    """删除（软删）系统范围。幂等：不存在也视作已删除（REST DELETE 语义），避免重复删除/双击报错。"""
    try:
        await omodel_client.delete_workspace(workspace_id)
    except ApiError:
        raise
    except omodel_client.OModelError as e:
        raise _map_omodel_error(e, action="删除") from e
    except Exception as e:  # noqa: BLE001
        raise ApiError(Err.INTERNAL_ERROR, f"删除系统范围失败：{str(e)[:300]}", retryable=True) from e
    return {"workspace_id": workspace_id, "status": "deleted"}


async def status(workspace_id: str) -> dict[str, Any]:
    ws = await omodel_client.get_workspace(workspace_id)
    if ws is None:
        raise ApiError(Err.NOT_FOUND, "workspace 不存在")
    return {
        "workspace_id": workspace_id,
        "sync_status": ws["sync_status"],
        "scope_revision": ws["scope_revision"],
        "app_ids": ws.get("app_ids") or [],  # 范围 chip 展示真实 APPID 列表（mock/real 键一致）
    }


async def statistics(workspace_id: str) -> dict[str, Any]:
    """看护空间统计四数（Agent 初始化「确认能力清单」页展示）。

    上游 wesee/statistics 失败（未配 OMODEL / 超时 / 非 2xx）→ 四个 0（读展示，不阻塞向导）。
    只透出前端要的四键，node_types/relation_types 明细不外传。
    """
    stats = await omodel_client.get_statistics(workspace_id) or {}

    def _n(key: str) -> int:
        try:
            return int(stats.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "node_count": _n("node_count"),
        "relation_count": _n("relation_count"),
        "node_type_count": _n("node_type_count"),
        "relation_type_count": _n("relation_type_count"),
    }


def console_page_base() -> str:
    """omodel 控制台页面前缀（设置页 iframe 用，前端追加 workspace_id）。

    OPENOPS_OMODEL_PAGE_URL 显式覆盖 > 从 OPENOPS_OMODEL_BASE_URL 域根派生
    `{base}/wesee/omodel/index.html?dataSource=api&workspace=`；两者皆空（mock/未配置）
    返回 ""——前端显示「内网环境可用」空态。两种地址都拒绝 userinfo 和动态 `{host}`；
    BASE_URL 还须无 query/fragment，页面只从清洗后的 origin 派生。
    """
    from infra.external.omodel_client import OModelError
    from infra.external.omodel_real import _target_info

    override = os.environ.get("OPENOPS_OMODEL_PAGE_URL", "").strip()
    if override:
        from urllib.parse import urlparse

        try:
            page = urlparse(override)
            _port = page.port
            _target_info(f"{page.scheme}://{page.netloc}")
        except (ValueError, OModelError):
            return ""
        if (page.scheme not in ("http", "https") or not page.hostname
                or page.username is not None or page.password is not None
                or page.fragment or any(ch in override for ch in "{}\\")
                or any(ch.isspace() for ch in override)):
            return ""
        return override
    raw = os.environ.get("OPENOPS_OMODEL_BASE_URL", "").strip().rstrip("/")
    if not raw:
        return ""
    try:
        _target, origin, _host = _target_info(raw)
    except OModelError:
        return ""
    return f"{origin}/wesee/omodel/index.html?dataSource=api&workspace="
