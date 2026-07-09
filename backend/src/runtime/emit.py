"""共享事件发射：先写 audit_event（事实源）再 publish SSE（体验）。

mock orchestrator 与真 AgentScope runtime 共用本函数，保证两条后端产出的 `openops.*`
事件与审计投影完全一致（30.4 envelope）。敏感字段禁入事件（SEC-002）由调用方保证。
"""
from __future__ import annotations

from typing import Any

from infra.repositories import audit
from runtime import events
from runtime.task_registry import TaskState


async def emit(st: TaskState, run: dict[str, Any], event_type: str, **kw: Any) -> None:
    trace = str(run["audit_trace_id"])
    await audit.insert_event(
        audit_trace_id=trace, event_type=event_type, user_id=st.user_id,
        run_id=st.run_id, instance_id=str(run["agent_team_instance_id"]), task_id=st.task_id,
        action=kw.get("action", ""), decision=kw.get("decision"), reason_code=kw.get("reason_code"),
        payload_redacted=kw.get("payload"), external_request_id=kw.get("external_request_id"),
    )
    events.publish(st.run_id, events.envelope(
        st.run_id, event_type, task_id=st.task_id, message=kw.get("message", ""),
        reason_code=kw.get("reason_code"), severity=kw.get("severity", "info"),
        payload=kw.get("payload"), audit_trace_id=trace,
    ))
