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
from infra.redact import redact_text
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
    msg = redact_text(msg, max_length=500)
    eid = await audit.insert_event(
        audit_trace_id=trace, event_type="scope.blocked", user_id=user_id,
        run_id=run_id, instance_id=instance_id, task_id=task_id, action="scope_block", reason_code=reason_code,
        payload_redacted={"summary": msg},
    )
    events.publish(run_id, events.envelope(
        run_id, "openops.scope.blocked", task_id=task_id, severity="warning",
        message=msg, reason_code=reason_code, audit_trace_id=trace, event_id=eid,
    ))


def _scope_override() -> list[str] | None:
    """联调测试缝：设 OPENOPS_SCOPE_OVERRIDE_APPIDS（逗号分隔）就用它当 effective_appids、跳过 oModel——
    oModel 服务未就绪时也能拿真 appid 联调真 MCP。空/未设=不覆盖，照常走 oModel。仅测试/联调用。"""
    raw = os.environ.get("OPENOPS_SCOPE_OVERRIDE_APPIDS", "").strip()
    return [a.strip() for a in raw.split(",") if a.strip()] or None


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

    override = _scope_override()
    if override is not None:  # 联调缝：跳过 oModel，用真 appid 当 scope（omodel_request_id 打标记，审计可辨）
        res: dict[str, Any] = {"status": "ok", "effective_appids": override,
                               # 覆盖缝只有裸 appid，无名字/企业来源
                               "scope_apps": [{"appid": a, "name": "", "enterprise_id": ""} for a in override],
                               "scope_revision": instance_rev, "omodel_request_id": "scope-override"}
    else:
        res = await omodel_client.resolve_scope(ws_id, instance_rev, user_id)
    status = res["status"]
    if status == "syncing":
        await _blocked(run_id, task_id, instance_id, audit_trace_id, user_id, "WORKSPACE_NOT_READY", "workspace 未就绪")
        raise ApiError(Err.WORKSPACE_NOT_READY, "workspace 未就绪（fail-closed）", retryable=True)
    if status != "ok":
        # 原因必达（内网实测：光秃 SCOPE_RESOLVE_FAILED 无法定位——常见=实例绑的 workspace 是
        # mock 时代旧 id / 已在 umodel 删除，check-net ③ 可列真实 workspaces 核对）
        why = str(res.get("reason") or "上游未给原因")
        msg = (f"范围解析失败（fail-closed）：workspace={ws_id}，{why}。"
               "该实例绑定的系统范围可能已失效——用初始化向导从真实应用树新建 Agent，或核对 check-net ③")
        await _blocked(run_id, task_id, instance_id, audit_trace_id, user_id, "SCOPE_RESOLVE_FAILED", msg)
        raise ApiError(Err.SCOPE_RESOLVE_FAILED, msg, retryable=True)
    appids = res["effective_appids"]
    if not appids:
        await _blocked(run_id, task_id, instance_id, audit_trace_id, user_id, "EMPTY_SCOPE", "有效范围为空")
        raise ApiError(Err.EMPTY_SCOPE, "有效范围为空，禁止平台工具调用")  # SCOPE-003

    new_rev = res.get("scope_revision", instance_rev)
    if new_rev != instance_rev:  # 范围有变：回写实例 + 审计 + scope.updated
        await agent_teams.update_scope_revision(instance_id, new_rev, user_id)
        message = f"工作范围已更新到新版本（{new_rev}）"
        eid = await audit.insert_event(
            audit_trace_id=audit_trace_id, event_type="scope.updated", user_id=user_id,
            run_id=run_id, instance_id=instance_id, task_id=task_id, action="scope_update",
            payload_redacted={"from": instance_rev, "to": new_rev, "summary": message},
        )
        events.publish(run_id, events.envelope(
            run_id, "openops.scope.updated", task_id=task_id,
            message=message, payload={"scope_revision": new_rev},
            audit_trace_id=audit_trace_id, event_id=eid,
        ))

    snapshot_id = await runs.insert_scope_snapshot(
        user_id, instance_id, run_id, task_id, ws_id, new_rev, appids, res["omodel_request_id"], "task_start",
    )
    ctx = {
        "scope_snapshot_id": snapshot_id, "effective_appids": appids,
        # scope_apps：`[{appid, name, enterprise_id}]`，effective_appids 的纯显示装饰（list_scope_apps 用）。
        # 用 .get 兜底——不返回该键的实现（旧 mock/三方）不得在任务启动处 KeyError。
        "scope_apps": res.get("scope_apps") or [],
        "scope_revision": new_rev,
        "workspace_id": ws_id,  # 出站平台 MCP header 用（omodel 工作空间 id，ws_id 见上）
        "omodel_request_id": res["omodel_request_id"], "cache_hit": False,
    }
    expires = now + _TTL_S
    _cache[key] = (expires, ctx)
    if new_rev != instance_rev:  # 下次以新 revision 为 key 也能命中
        _cache[(ws_id, new_rev, user_id)] = (expires, ctx)
    return ctx


async def resolve_from_last_snapshot(
    user_id: str, instance: dict[str, Any], run_id: str, task_id: str, audit_trace_id: str
) -> dict[str, Any] | None:
    """夜间降级（仅告警派发链调用）：登录态缺失致 resolve 失败时，复用实例最近一次快照。

    插入**新**快照行（compute_reason='alert_snapshot_fallback'，task_id 对得上）保持审计链完整；
    ctx 带 degraded=True 随 task 快照落盘留痕，omodel_request_id='snapshot-fallback' 让
    start_task 的 scope.resolved 审计一眼可辨降级。无历史快照/快照为空 → None（调用方按原错抛）。
    不写 _cache：降级结果不该被交互任务命中复用。
    """
    _ = audit_trace_id  # 审计由 start_task 的 scope.resolved 统一记录（external_request_id 可辨）
    instance_id = str(instance["agent_team_instance_id"])
    last = await runs.latest_scope_snapshot_by_instance(instance_id)
    if last is None:
        return None
    appids = list(last.get("effective_appids_snapshot") or [])
    if not appids:
        return None
    ws_id = instance["workspace_id"]
    rev = str(last.get("scope_revision") or instance["scope_revision"] or "")
    snapshot_id = await runs.insert_scope_snapshot(
        user_id, instance_id, run_id, task_id, ws_id, rev, appids,
        "snapshot-fallback", "alert_snapshot_fallback",
    )
    return {
        "scope_snapshot_id": snapshot_id, "effective_appids": appids,
        "scope_apps": [],  # 降级无名字装饰来源；工具面只吃 effective_appids
        "scope_revision": rev, "workspace_id": ws_id,
        "omodel_request_id": "snapshot-fallback", "cache_hit": False, "degraded": True,
    }
