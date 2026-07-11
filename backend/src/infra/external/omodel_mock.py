"""oModel mock 实现（29.1 / 28.6 契约面）：内存 workspace 库 + resolve 分态。

`OPENOPS_OMODEL=mock`（默认）时启用；仅供 demo / pytest，不联真实 umodel。
resolve 返回 status ∈ {ok, syncing, failed}，effective_appids 只由本 mock 给出（无本地 workspace→APPID 表）。
"""
from __future__ import annotations

import uuid
from typing import Any

# mock workspace 库：ready / syncing / failed 三态演示（对应 SCOPE 用例）
_DEFAULTS: dict[str, dict[str, Any]] = {
    "ws_pay_abc": {
        "workspace_id": "ws_pay_abc", "name": "支付核心域",
        "scope_revision": "rev-20260708-001", "sync_status": "ready",
        "app_ids": ["APP-A", "APP-B", "APP-C"],
    },
    "ws_syncing": {
        "workspace_id": "ws_syncing", "name": "同步中范围",
        "scope_revision": "rev-0", "sync_status": "syncing", "app_ids": [],
    },
    "ws_failed": {
        "workspace_id": "ws_failed", "name": "解析失败范围",
        "scope_revision": "rev-err", "sync_status": "failed", "app_ids": [],
    },
}
_WORKSPACES: dict[str, dict[str, Any]] = {k: dict(v) for k, v in _DEFAULTS.items()}


def _req_id() -> str:
    return "req_" + uuid.uuid4().hex[:10]


async def create_workspace(name: str, app_ids: list[str], *,
                           apps: list[dict[str, Any]] | None = None, owner: str = "") -> dict[str, Any]:  # noqa: ARG001 —— mock 忽略
    _ = (apps, owner)
    ws_id = f"ws_{uuid.uuid4().hex[:8]}"
    ws = {
        "workspace_id": ws_id, "name": name,
        "scope_revision": "rev-" + uuid.uuid4().hex[:8],
        "sync_status": "ready",  # mock：立即 ready
        "app_ids": list(app_ids),
    }
    _WORKSPACES[ws_id] = ws
    return ws


async def get_workspace(workspace_id: str) -> dict[str, Any] | None:
    return _WORKSPACES.get(workspace_id)


async def list_workspaces() -> list[dict[str, Any]]:
    return list(_WORKSPACES.values())


async def resolve_scope(workspace_id: str, scope_revision: str, user_id: str) -> dict[str, Any]:
    """resolve → effective_appids（per-user 过滤由 oModel 侧承担；mock 直接返回全集）。

    返回 status：ok / syncing（未就绪）/ failed（未知或解析失败）。scope_revision 为 oModel 当前版本，
    与入参不同即代表范围有变（Scope Service 据此回写实例 + scope.updated）。
    """
    ws = _WORKSPACES.get(workspace_id)
    if ws is None:
        return {"status": "failed", "effective_appids": [], "scope_revision": scope_revision, "omodel_request_id": _req_id()}
    sync = ws["sync_status"]
    if sync in ("syncing", "creating"):
        return {"status": "syncing", "effective_appids": [], "scope_revision": ws["scope_revision"], "omodel_request_id": _req_id()}
    if sync == "failed":
        return {"status": "failed", "effective_appids": [], "scope_revision": ws["scope_revision"], "omodel_request_id": _req_id()}
    return {
        "status": "ok",
        "effective_appids": list(ws["app_ids"]),
        "scope_revision": ws["scope_revision"],
        "omodel_request_id": _req_id(),
    }


# ---- 测试钩子：模拟 oModel 侧范围/版本/状态变更 ----
def _set_scope(workspace_id: str, *, scope_revision: str | None = None,
               app_ids: list[str] | None = None, sync_status: str | None = None) -> None:
    ws = _WORKSPACES.get(workspace_id)
    if ws is None:
        return
    if scope_revision is not None:
        ws["scope_revision"] = scope_revision
    if app_ids is not None:
        ws["app_ids"] = list(app_ids)
    if sync_status is not None:
        ws["sync_status"] = sync_status


def _reset() -> None:
    """恢复默认 workspace 库（测试隔离）。"""
    _WORKSPACES.clear()
    _WORKSPACES.update({k: dict(v) for k, v in _DEFAULTS.items()})
