"""oModel client（mock）：workspace 状态 + scope resolve（29.1 / 28.6 契约面）。

真实实现（B1 块）按 29.1 SPL 接口替换；接口签名保持不变。
"""
from __future__ import annotations

import uuid
from typing import Any

# mock workspace 库：ws_pay_abc 就绪；ws_syncing 演示未就绪分支（INIT-003）
_WORKSPACES: dict[str, dict[str, Any]] = {
    "ws_pay_abc": {
        "workspace_id": "ws_pay_abc",
        "name": "支付核心域",
        "scope_revision": "rev-20260708-001",
        "sync_status": "ready",
        "app_ids": ["APP-A", "APP-B", "APP-C"],
    },
    "ws_syncing": {
        "workspace_id": "ws_syncing",
        "name": "同步中范围",
        "scope_revision": "rev-0",
        "sync_status": "syncing",
        "app_ids": [],
    },
}


async def create_workspace(name: str, app_ids: list[str]) -> dict[str, Any]:
    ws_id = f"ws_{uuid.uuid4().hex[:8]}"
    ws = {
        "workspace_id": ws_id,
        "name": name,
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
    """resolve → effective_appids（per-user 过滤由 oModel 侧承担；mock 直接返回全集）。"""
    ws = _WORKSPACES.get(workspace_id)
    if ws is None:
        return {"status": "failed", "effective_appids": [], "omodel_request_id": "req_" + uuid.uuid4().hex[:10]}
    return {
        "status": "ok",
        "effective_appids": list(ws["app_ids"]),
        "scope_revision": ws["scope_revision"],
        "omodel_request_id": "req_" + uuid.uuid4().hex[:10],
    }
