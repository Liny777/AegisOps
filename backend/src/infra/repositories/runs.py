"""sre_agent_run / sre_scope_snapshot / sre_approval_request 仓储。"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, jsonb, q_all, q_one


async def create_run(
    user_id: str, instance_id: str, config_version_id: str, framework_session_id: str, audit_trace_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    rid = run_id or str(uuid.uuid4())  # 调用方可预定 id（B8：run 开启前先按此 id 做沙箱容量准入）
    await exec1(
        """
        insert into sre_agent_run
          (agent_run_id, user_id, agent_team_instance_id, config_version_id, framework_session_id,
           audit_trace_id, run_status, started_at, created_by, last_updated_by)
        values (%(r)s, %(u)s, %(i)s, %(cv)s, %(f)s, %(tr)s, 'active', now(), %(u)s, %(u)s)
        """,
        {"r": rid, "u": user_id, "i": instance_id, "cv": config_version_id, "f": framework_session_id, "tr": audit_trace_id},
    )
    return (await get_run(rid))  # type: ignore[return-value]


async def get_run(run_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_agent_run where agent_run_id=%(r)s and deleted_at is null", {"r": run_id}
    )


async def list_runs_by_user(user_id: str) -> list[dict[str, Any]]:
    return await q_all(
        "select * from sre_agent_run where user_id=%(u)s and deleted_at is null order by started_at desc", {"u": user_id}
    )


async def soft_delete_run(run_id: str, updated_by: str) -> int:
    """软删（会话删除入口）：打 deleted_at 标记；get_run/list_runs_by_user 均已过滤。"""
    return await exec1(
        """
        update sre_agent_run
        set deleted_at=now(), last_update_date=now(), last_updated_by=%(u)s
        where agent_run_id=%(r)s and deleted_at is null
        """,
        {"r": run_id, "u": updated_by},
    )


async def set_run_title(run_id: str, title: str, updated_by: str) -> int:
    return await exec1(
        """
        update sre_agent_run
        set run_title=%(t)s, last_update_date=now(), last_updated_by=%(u)s
        where agent_run_id=%(r)s
        """,
        {"r": run_id, "t": title, "u": updated_by},
    )


async def set_run_status(run_id: str, status: str, reason_code: str | None = None) -> int:
    return await exec1(
        """
        update sre_agent_run
        set run_status=%(s)s, status_reason_code=%(rc)s,
            ended_at = case when %(s)s='closed' then now() else ended_at end,
            last_update_date=now()
        where agent_run_id=%(r)s
        """,
        {"r": run_id, "s": status, "rc": reason_code},
    )


async def insert_scope_snapshot(
    user_id: str, instance_id: str, run_id: str, task_id: str, workspace_id: str,
    scope_revision: str, appids: list[str], omodel_request_id: str, compute_reason: str,
) -> str:
    sid = str(uuid.uuid4())
    await exec1(
        """
        insert into sre_scope_snapshot
          (scope_snapshot_id, user_id, agent_team_instance_id, agent_run_id, task_id, workspace_id,
           scope_revision, effective_appids_snapshot, omodel_request_id, compute_reason, computed_at, created_by, last_updated_by)
        values (%(s)s, %(u)s, %(i)s, %(r)s, %(t)s, %(w)s, %(sr)s, %(a)s, %(orq)s, %(cr)s, now(), %(u)s, %(u)s)
        """,
        {"s": sid, "u": user_id, "i": instance_id, "r": run_id, "t": task_id, "w": workspace_id,
         "sr": scope_revision, "a": jsonb(appids), "orq": omodel_request_id, "cr": compute_reason},
    )
    return sid


async def create_approval(
    user_id: str, instance_id: str, run_id: str, task_id: str, tool_call_name: str,
    arguments_redacted: dict[str, Any], audit_trace_id: str, framework_session_id: str,
    expire_minutes: int = 5,
) -> dict[str, Any]:
    aid = str(uuid.uuid4())
    await exec1(
        """
        insert into sre_approval_request
          (approval_request_id, user_id, agent_team_instance_id, agent_run_id, framework_session_id,
           reply_id, tool_call_id, task_id, tool_call_name,
           arguments_redacted_json, suggested_rules_redacted_json, decision, audit_trace_id,
           created_by, last_updated_by, expire_at)
        values (%(a)s, %(u)s, %(i)s, %(r)s, %(f)s,
                %(rp)s, %(tc)s, %(t)s, %(n)s,
                %(arg)s, %(sg)s, 'pending', %(tr)s,
                %(u)s, %(u)s, now() + make_interval(mins => %(exp)s))
        """,
        {"a": aid, "u": user_id, "i": instance_id, "r": run_id, "f": framework_session_id,
         "rp": "reply_" + uuid.uuid4().hex[:10], "tc": "call_" + uuid.uuid4().hex[:10],
         "t": task_id, "n": tool_call_name,
         "arg": jsonb(arguments_redacted), "sg": jsonb({}), "tr": audit_trace_id, "exp": expire_minutes},
    )
    return (await get_approval(aid))  # type: ignore[return-value]


async def get_approval(approval_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_approval_request where approval_request_id=%(a)s", {"a": approval_id}
    )


async def pending_approvals(run_id: str) -> list[dict[str, Any]]:
    return await q_all(
        """
        select * from sre_approval_request
        where agent_run_id=%(r)s and decision='pending'
        order by creation_date
        """,
        {"r": run_id},
    )


async def decide_approval(approval_id: str, decision: str, decided_by: str) -> int:
    return await exec1(
        """
        update sre_approval_request
        set decision=%(d)s, decided_by=%(b)s, decided_at=now(), last_updated_by=%(b)s, last_update_date=now()
        where approval_request_id=%(a)s and decision='pending'
        """,
        {"a": approval_id, "d": decision, "b": decided_by},
    )


async def expire_stale_approvals(run_id: str) -> int:
    """ASK 超时（ASK-004）：pending 且过期 → timeout。"""
    return await exec1(
        """
        update sre_approval_request set decision='timeout', last_update_date=now(), last_updated_by='system'
        where agent_run_id=%(r)s and decision='pending' and expire_at is not null and expire_at < now()
        """,
        {"r": run_id},
    )
