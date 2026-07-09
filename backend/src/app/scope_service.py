"""Scope Service（28.6）：运行边界 resolve → ScopeContext + scope_snapshot。

- `effective_appids` 只来自 oModel resolve（无本地 workspace→APPID 表）。
- 30 秒短 TTL 缓存（key=workspace+revision+user）：命中复用 ScopeContext 与 scope_snapshot_id，不重解不重写快照；
  缓存只做性能优化，不作失败兜底。
- fail-closed：syncing→WORKSPACE_NOT_READY、failed→SCOPE_RESOLVE_FAILED、empty→EMPTY_SCOPE，并推 scope.blocked。
- oModel 返回新 scope_revision：回写 agent_team_instance、写审计、推 scope.updated；scope_snapshot 仅审计回放用。
"""
from __future__ import annotations

import os
import time
from typing import Any

from domain.errors import ApiError, Err
from infra.external import omodel_client
from infra.repositories import agent_teams, audit, runs
from runtime import events

_TTL_S = float(os.environ.get("OPENOPS_SCOPE_TTL_S", "30"))
# (workspace_id, scope_revision, user_id) -> (expires_at_monotonic, ScopeContext)
_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}


def _reset_cache() -> None:  # 测试隔离
    _cache.clear()


async def _blocked(run_id: str, task_id: str, instance_id: str, trace: str,
                   user_id: str, reason_code: str, msg: str) -> None:
    """fail-closed：写审计 scope.blocked + 推 openops.scope.blocked。"""
    await audit.insert_event(
        audit_trace_id=trace, event_type="scope.blocked", user_id=user_id,
        run_id=run_id, instance_id=instance_id, task_id=task_id, action="scope_block", reason_code=reason_code,
    )
    events.publish(run_id, events.envelope(
        run_id, "openops.scope.blocked", task_id=task_id, severity="warning",
        message=msg, reason_code=reason_code, audit_trace_id=trace,
    ))


async def resolve_for_task(
    user_id: str, instance: dict[str, Any], run_id: str, task_id: str, audit_trace_id: str
) -> dict[str, Any]:
    ws_id = instance["workspace_id"]
    instance_rev = instance["scope_revision"]
    instance_id = str(instance["agent_team_instance_id"])
    key = (ws_id, instance_rev, user_id)

    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and cached[0] > now:
        ctx = dict(cached[1])
        ctx["cache_hit"] = True
        return ctx  # 复用 ScopeContext + scope_snapshot_id（28.6：不重解、不重写快照）

    res = await omodel_client.resolve_scope(ws_id, instance_rev, user_id)
    status = res["status"]
    if status == "syncing":
        await _blocked(run_id, task_id, instance_id, audit_trace_id, user_id, "WORKSPACE_NOT_READY", "workspace 未就绪")
        raise ApiError(Err.WORKSPACE_NOT_READY, "workspace 未就绪（fail-closed）", retryable=True)
    if status != "ok":
        await _blocked(run_id, task_id, instance_id, audit_trace_id, user_id, "SCOPE_RESOLVE_FAILED", "范围解析失败")
        raise ApiError(Err.SCOPE_RESOLVE_FAILED, "范围解析失败（fail-closed）", retryable=True)
    appids = res["effective_appids"]
    if not appids:
        await _blocked(run_id, task_id, instance_id, audit_trace_id, user_id, "EMPTY_SCOPE", "有效范围为空")
        raise ApiError(Err.EMPTY_SCOPE, "有效范围为空，禁止平台工具调用")  # SCOPE-003

    new_rev = res.get("scope_revision", instance_rev)
    if new_rev != instance_rev:  # 范围有变：回写实例 + 审计 + scope.updated
        await agent_teams.update_scope_revision(instance_id, new_rev, user_id)
        await audit.insert_event(
            audit_trace_id=audit_trace_id, event_type="scope.updated", user_id=user_id,
            run_id=run_id, instance_id=instance_id, task_id=task_id, action="scope_update",
            payload_redacted={"from": instance_rev, "to": new_rev},
        )
        events.publish(run_id, events.envelope(
            run_id, "openops.scope.updated", task_id=task_id,
            message=f"工作范围已更新到新版本（{new_rev}）", payload={"scope_revision": new_rev},
            audit_trace_id=audit_trace_id,
        ))

    snapshot_id = await runs.insert_scope_snapshot(
        user_id, instance_id, run_id, task_id, ws_id, new_rev, appids, res["omodel_request_id"], "task_start",
    )
    ctx = {
        "scope_snapshot_id": snapshot_id, "effective_appids": appids, "scope_revision": new_rev,
        "omodel_request_id": res["omodel_request_id"], "cache_hit": False,
    }
    expires = now + _TTL_S
    _cache[key] = (expires, ctx)
    if new_rev != instance_rev:  # 下次以新 revision 为 key 也能命中
        _cache[(ws_id, new_rev, user_id)] = (expires, ctx)
    return ctx
