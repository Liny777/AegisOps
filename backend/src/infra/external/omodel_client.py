"""oModel client 分发器（B3 可切换 adapter）：按 `OPENOPS_OMODEL=mock`（默认）|`real` 选实现。

callers（scope_service / workspace_service / agent_team_service）签名不变；resolve 契约见 [[28.6]]/[[29.1]]。
"""
from __future__ import annotations

import os
from typing import Any


def _impl() -> Any:
    if os.environ.get("OPENOPS_OMODEL", "mock").strip().lower() == "real":
        from infra.external import omodel_real

        return omodel_real
    from infra.external import omodel_mock

    return omodel_mock


async def resolve_scope(workspace_id: str, scope_revision: str, user_id: str) -> dict[str, Any]:
    return await _impl().resolve_scope(workspace_id, scope_revision, user_id)


async def get_workspace(workspace_id: str) -> dict[str, Any] | None:
    return await _impl().get_workspace(workspace_id)


async def list_workspaces() -> list[dict[str, Any]]:
    return await _impl().list_workspaces()


async def create_workspace(name: str, app_ids: list[str]) -> dict[str, Any]:
    return await _impl().create_workspace(name, app_ids)
