"""共享事件发射：先写 audit_event（事实源）再 publish SSE（体验）。

mock orchestrator 与真 AgentScope runtime 共用本函数，保证两条后端产出的 `openops.*`
事件与审计投影完全一致（30.4 envelope）。敏感字段禁入事件（SEC-002）由调用方保证。
P 块：任务状态转移事件在此单点顺带落影子快照（sre_task_state）——两条 runtime 全部
状态变化都过 emit，无需在各终态散点插桩；快照失败不阻断任务（旧库未迁移时降级）。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from infra.redact import redact_text, sanitize_activity_payload
from infra.repositories import audit, delegations, runs, task_states
from runtime import events, task_registry
from runtime.task_registry import TaskState

log = logging.getLogger("openops.emit")

# 事件 → 快照状态（事件即真相，不依赖调用方是否已改 st.status）
_SNAPSHOT_STATUS = {
    "openops.task.started": "running",
    "openops.task.completed": "completed",
    "openops.task.failed": "failed",
    "openops.task.cancelled": "cancelled",
}
# 不改状态但需刷新快照内容的事件（RCA 面板 / 审批引用 / 四号校验引用）
_SNAPSHOT_REFRESH = {"openops.rca.updated", "openops.approval.required",
                     "openops.approval.approved", "openops.approval.rejected",
                     "openops.flow_check.required", "openops.flow_check.approved",
                     "openops.flow_check.rejected"}


def activity_context(st: TaskState) -> dict[str, Any]:
    """TaskState → 可安全写入事件的多 Agent 关联字段（None 不占 payload）。"""
    values: dict[str, Any] = {
        "agent_key": st.agent_key,
        "agent_label": st.agent_label,
        "leader_task_id": st.leader_task_id,
        "child_task_id": st.task_id if st.leader_task_id else None,
        "delegation_id": st.delegation_id,
        "dispatch_batch_id": st.dispatch_batch_id,
        "dispatch_batch_no": st.dispatch_batch_no,
    }
    return {k: v for k, v in values.items() if v is not None}


async def activity_context_of_task(task_id: str) -> dict[str, Any]:
    """审批决策/超时等不持有 TaskState 的入口也尽量补齐子 Agent 上下文。"""
    st = task_registry.get_by_task(task_id)
    if st is not None:
        return activity_context(st)
    ledger = await delegations.find_by_child_task_id(task_id)
    if ledger is not None:
        return {
            "agent_key": str(ledger["agent_key"]),
            "agent_label": str(ledger["agent_key"]),
            "leader_task_id": str(ledger["leader_task_id"]),
            "child_task_id": task_id,
            "delegation_id": str(ledger["delegation_id"]),
            **({"dispatch_batch_id": str(ledger["dispatch_batch_id"])}
               if ledger.get("dispatch_batch_id") else {}),
            **({"dispatch_batch_no": int(ledger["dispatch_batch_no"])}
               if ledger.get("dispatch_batch_no") is not None else {}),
        }
    return {"agent_key": agent_key_of_task(task_id)}


def activity_tool_label(st: TaskState, tool_name: str) -> str:
    """完整工具名 → MCP 内层工具名 → 原工具名。"""
    labels = st.activity_tool_labels or {}
    if labels.get(tool_name):
        return labels[tool_name]
    inner = tool_name
    for sep in ("__", "/", ":", "."):
        if sep in inner:
            inner = inner.rsplit(sep, 1)[-1]
    return labels.get(inner) or tool_name


async def emit(st: TaskState, run: dict[str, Any], event_type: str, **kw: Any) -> None:
    trace = str(run["audit_trace_id"])
    # E3：agent_key 单点注入（所有事件全覆盖——子 Agent 的 model/skill/bash/tool 事件前端才能分组；
    # 内网实测：只有两处手动带 key 时，子 Agent 大部分过程事件都落在主时间线里"看不出是谁"）。
    # 注入只作默认：调用点显式带的 agent_key 优先（dispatch 用主 st 发 dispatched/timeout/failed
    # 但语义归属子角色组——被 "main" 盖掉就又看不出是谁了）。
    message = redact_text(kw.get("message", ""), max_length=500)
    raw_payload = {**activity_context(st), **(kw.get("payload") or {})}
    tool_name = raw_payload.get("tool") or raw_payload.get("skill")
    if isinstance(tool_name, str) and tool_name:
        raw_payload.setdefault("display_label", activity_tool_label(st, tool_name))
    if message:
        raw_payload.setdefault("summary", message[:300])
    payload = sanitize_activity_payload(
        event_type,
        raw_payload,
        message=message,
        external_request_id=kw.get("external_request_id"),
    )
    eid = await audit.insert_event(
        audit_trace_id=trace, event_type=event_type, user_id=st.user_id,
        run_id=st.run_id, instance_id=str(run["agent_team_instance_id"]), task_id=st.task_id,
        action=kw.get("action", ""), decision=kw.get("decision"), reason_code=kw.get("reason_code"),
        payload_redacted=payload, external_request_id=kw.get("external_request_id"),
    )
    events.publish(st.run_id, events.envelope(
        st.run_id, event_type, task_id=st.task_id, message=message,
        reason_code=kw.get("reason_code"), severity=kw.get("severity", "info"),
        payload=payload, audit_trace_id=trace, event_id=eid,
        external_request_id=kw.get("external_request_id"),
    ))
    # 只给主任务落 sre_task_state：子 Agent（leader_task_id 非空）的 approval.required 一旦落快照，
    # 会插入一条永不置终态的 running 子行——污染 count_running（并发限额误判）并顶替 get_state 投影。
    # 子 Agent 生命周期由 delegations 账本记录，不进主任务快照表。
    if (event_type in _SNAPSHOT_STATUS or event_type in _SNAPSHOT_REFRESH) and st.leader_task_id is None:
        try:
            await task_states.upsert_snapshot(st, _SNAPSHOT_STATUS.get(event_type, st.status), trace)
        except Exception:  # noqa: BLE001 —— 旧库未跑 persistence 迁移等，快照降级不阻断
            log.warning("[OpenOps][snapshot] 任务快照写入失败（旧库重跑 sql/openops_v1_core.sql 补表）")


def agent_key_of_task(task_id: str) -> str:
    """事件归属解析（DEF-6 公共化）：优先存活 TaskState；已注销从 task_id 形状推——
    主任务无 "."；子任务 `{leader}.{key}-{hash8}`（DEF-2 格式，旧 `{key}{seq}` 兼容）。"""
    st = task_registry.get_by_task(task_id)
    if st is not None:
        return st.agent_key
    if "." not in task_id:
        return "main"
    tail = task_id.split(".", 1)[1]
    tail = re.sub(r"-[0-9a-f]{8}$", "", tail)
    return re.sub(r"\d+$", "", tail) or tail


async def expire_stale_approvals_and_audit(run_id: str, *, force_approval_id: str | None = None) -> int:
    """ASK 超时收口（连带 B）：pending→timeout 的翻转行补 approval.timeout 审计+SSE——
    此前 repo 静默翻转，超时终态在审计链上无痕（31 号 ASK-004）。

    force_approval_id：等待方主循环超时（OPENOPS_ASK_TIMEOUT_S）可能早于行内 expire_at
    （固定 5 分钟）——等待已放弃但行仍 pending，卡片永悬。传入本次等待的 approval_id
    先行置过期，保证「循环超时 ⇒ 该行必达 decision=timeout」不依赖两个时钟对齐。"""
    if force_approval_id:
        await runs.force_expire_approval(force_approval_id)
    rows = await runs.expire_stale_approvals(run_id)
    for r in rows:
        task_id = str(r["task_id"])
        ctx = await activity_context_of_task(task_id)
        message = "审批超时未处理，按拒绝收口"
        payload = sanitize_activity_payload(
            "openops.approval.timeout",
            {"approval_request_id": str(r["approval_request_id"]),
             "tool": r["tool_call_name"], **ctx, "summary": message},
            message=message,
        )
        eid: str | None = None
        try:
            eid = await audit.insert_event(
                audit_trace_id=str(r["audit_trace_id"]), event_type="approval.timeout",
                user_id=str(r["user_id"]), run_id=str(r["agent_run_id"]),
                instance_id=str(r["agent_team_instance_id"]), task_id=task_id,
                action="expire", decision="timeout", actor_type="system",
                payload_redacted=payload,
            )
        except Exception:  # noqa: BLE001 —— 审计失败不阻断超时收口
            log.warning("[OpenOps][ask] approval.timeout 审计写入失败 task=%s", r["task_id"])
        events.publish(str(r["agent_run_id"]), events.envelope(
            str(r["agent_run_id"]), "openops.approval.timeout", task_id=task_id,
            severity="warning", message=message, payload=payload,
            audit_trace_id=str(r["audit_trace_id"]), event_id=eid,
        ))
    return len(rows)


async def expire_stale_flow_checks_and_audit(run_id: str, *, force_flow_check_id: str | None = None) -> int:
    """四号校验超时收口（29.14；形制同 expire_stale_approvals_and_audit）：
    pending→timeout 翻转行补 flow_check.timeout 审计+SSE。force_flow_check_id 语义同审批版——
    等待方主循环超时（OPENOPS_FLOW_CHECK_TIMEOUT_S）可能早于行内 expire_at，先行置过期保证必达 timeout。"""
    if force_flow_check_id:
        await runs.force_expire_flow_check(force_flow_check_id)
    rows = await runs.expire_stale_flow_checks(run_id)
    for r in rows:
        task_id = str(r["task_id"])
        ctx = await activity_context_of_task(task_id)
        message = "四号校验超时未处理，按拒绝收口"
        payload = sanitize_activity_payload(
            "openops.flow_check.timeout",
            {"flow_check_request_id": str(r["flow_check_request_id"]),
             "tool": r["tool_call_name"], **ctx, "summary": message},
            message=message,
        )
        eid: str | None = None
        try:
            eid = await audit.insert_event(
                audit_trace_id=str(r["audit_trace_id"]), event_type="flow_check.timeout",
                user_id=str(r["user_id"]), run_id=str(r["agent_run_id"]),
                instance_id=str(r["agent_team_instance_id"]), task_id=task_id,
                action="expire", decision="timeout", actor_type="system",
                payload_redacted=payload,
            )
        except Exception:  # noqa: BLE001 —— 审计失败不阻断超时收口
            log.warning("[OpenOps][flow-check] flow_check.timeout 审计写入失败 task=%s", r["task_id"])
        events.publish(str(r["agent_run_id"]), events.envelope(
            str(r["agent_run_id"]), "openops.flow_check.timeout", task_id=task_id,
            severity="warning", message=message, payload=payload,
            audit_trace_id=str(r["audit_trace_id"]), event_id=eid,
        ))
    return len(rows)
