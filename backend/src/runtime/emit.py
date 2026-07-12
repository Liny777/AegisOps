"""共享事件发射：先写 audit_event（事实源）再 publish SSE（体验）。

mock orchestrator 与真 AgentScope runtime 共用本函数，保证两条后端产出的 `openops.*`
事件与审计投影完全一致（30.4 envelope）。敏感字段禁入事件（SEC-002）由调用方保证。
P 块：任务状态转移事件在此单点顺带落影子快照（sre_task_state）——两条 runtime 全部
状态变化都过 emit，无需在各终态散点插桩；快照失败不阻断任务（旧库未迁移时降级）。
"""
from __future__ import annotations

import logging
from typing import Any

from infra.repositories import audit, task_states
from runtime import events
from runtime.task_registry import TaskState

log = logging.getLogger("openops.emit")

# 事件 → 快照状态（事件即真相，不依赖调用方是否已改 st.status）
_SNAPSHOT_STATUS = {
    "openops.task.started": "running",
    "openops.task.completed": "completed",
    "openops.task.failed": "failed",
    "openops.task.cancelled": "cancelled",
}
# 不改状态但需刷新快照内容的事件（RCA 面板 / 审批引用）
_SNAPSHOT_REFRESH = {"openops.rca.updated", "openops.approval.required",
                     "openops.approval.approved", "openops.approval.rejected"}


async def emit(st: TaskState, run: dict[str, Any], event_type: str, **kw: Any) -> None:
    trace = str(run["audit_trace_id"])
    # E3：agent_key 单点注入（所有事件全覆盖——子 Agent 的 model/skill/bash/tool 事件前端才能分组；
    # 内网实测：只有两处手动带 key 时，子 Agent 大部分过程事件都落在主时间线里"看不出是谁"）。
    # 注入只作默认：调用点显式带的 agent_key 优先（dispatch 用主 st 发 dispatched/timeout/failed
    # 但语义归属子角色组——被 "main" 盖掉就又看不出是谁了）。
    payload = {"agent_key": st.agent_key, **(kw.get("payload") or {})}
    await audit.insert_event(
        audit_trace_id=trace, event_type=event_type, user_id=st.user_id,
        run_id=st.run_id, instance_id=str(run["agent_team_instance_id"]), task_id=st.task_id,
        action=kw.get("action", ""), decision=kw.get("decision"), reason_code=kw.get("reason_code"),
        payload_redacted=payload, external_request_id=kw.get("external_request_id"),
    )
    events.publish(st.run_id, events.envelope(
        st.run_id, event_type, task_id=st.task_id, message=kw.get("message", ""),
        reason_code=kw.get("reason_code"), severity=kw.get("severity", "info"),
        payload=payload, audit_trace_id=trace,
    ))
    if event_type in _SNAPSHOT_STATUS or event_type in _SNAPSHOT_REFRESH:
        try:
            await task_states.upsert_snapshot(st, _SNAPSHOT_STATUS.get(event_type, st.status), trace)
        except Exception:  # noqa: BLE001 —— 旧库未跑 persistence 迁移等，快照降级不阻断
            log.warning("[OpenOps][snapshot] 任务快照写入失败（旧库重跑 sql/openops_v1_core.sql 补表）")
