"""Scope Service：运行边界 resolve → scope_snapshot（28.6；EMPTY_SCOPE fail-closed）。"""
from __future__ import annotations

from typing import Any

from domain.errors import ApiError, Err
from infra.external import omodel_client
from infra.repositories import runs


async def resolve_for_task(
    user_id: str, instance: dict[str, Any], run_id: str, task_id: str
) -> dict[str, Any]:
    res = await omodel_client.resolve_scope(
        instance["workspace_id"], instance["scope_revision"], user_id
    )
    if res["status"] != "ok":
        raise ApiError(Err.SCOPE_RESOLVE_FAILED, "范围解析失败（fail-closed）", retryable=True)
    appids = res["effective_appids"]
    if not appids:
        raise ApiError(Err.EMPTY_SCOPE, "有效范围为空，禁止平台工具调用")  # SCOPE-003
    snapshot_id = await runs.insert_scope_snapshot(
        user_id, str(instance["agent_team_instance_id"]), run_id, task_id,
        instance["workspace_id"], res.get("scope_revision", instance["scope_revision"]),
        appids, res["omodel_request_id"], "task_start",
    )
    return {"scope_snapshot_id": snapshot_id, "effective_appids": appids, "omodel_request_id": res["omodel_request_id"]}
