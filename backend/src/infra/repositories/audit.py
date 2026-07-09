"""audit_event 仓储（企业审计事实源；30 天 expire_at）。"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, jsonb, q_all


async def insert_event(
    *,
    audit_trace_id: str,
    event_type: str,
    user_id: str,
    run_id: str | None = None,
    instance_id: str | None = None,
    task_id: str | None = None,
    action: str = "",
    decision: str | None = None,
    reason_code: str | None = None,
    payload_redacted: dict[str, Any] | None = None,
    external_request_id: str | None = None,
    actor_type: str = "system",
) -> str:
    eid = str(uuid.uuid4())
    await exec1(
        """
        insert into audit_event
          (audit_event_id, audit_trace_id, event_type, occurred_at, actor_type, user_id,
           agent_team_instance_id, agent_run_id, task_id, action, decision, reason_code,
           external_request_id, payload_redacted_json, created_by, last_updated_by, expire_at)
        values (%(e)s, %(tr)s, %(t)s, clock_timestamp(), %(at)s, %(u)s,
                %(i)s, %(r)s, %(tk)s, %(a)s, %(d)s, %(rc)s,
                %(x)s, %(p)s, 'system', 'system', now() + interval '30 days')
        """,
        {"e": eid, "tr": audit_trace_id, "t": event_type, "at": actor_type, "u": user_id,
         "i": instance_id, "r": run_id, "tk": task_id, "a": action, "d": decision, "rc": reason_code,
         "x": external_request_id, "p": jsonb(payload_redacted or {})},
    )
    return eid


async def list_by_run(run_id: str, limit: int = 200) -> list[dict[str, Any]]:
    return await q_all(
        """
        select * from audit_event where agent_run_id=%(r)s
        order by occurred_at asc limit %(l)s
        """,
        {"r": run_id, "l": limit},
    )


async def list_recent(limit: int = 100) -> list[dict[str, Any]]:
    return await q_all(
        "select * from audit_event order by occurred_at desc limit %(l)s", {"l": limit}
    )


async def list_by_trace(audit_trace_id: str) -> list[dict[str, Any]]:
    return await q_all(
        "select * from audit_event where audit_trace_id=%(t)s order by occurred_at asc", {"t": audit_trace_id}
    )
