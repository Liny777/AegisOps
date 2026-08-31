"""sre_agent_run / sre_scope_snapshot / sre_approval_request / sre_flow_check_request 仓储。"""
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


async def get_run(run_id: str, include_deleted: bool = False) -> dict[str, Any] | None:
    """include_deleted=True 供审计入口（DEF-5）：软删会话的 run.deleted 事件不能因过滤而永久不可见。"""
    if include_deleted:
        return await q_one("select * from sre_agent_run where agent_run_id=%(r)s", {"r": run_id})
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


async def list_idle_active_runs(ttl_minutes: int) -> list[dict[str, Any]]:
    """无活动超阈值的活跃 run（idle-run 回收候选，doc 28.10/11「TTL 兜底 run 泄漏」）：
    最后活动取该 run 审计事件的 max(occurred_at)，从未产生事件的落回 started_at 兜底；仅 active 且未软删。
    走 ix_audit_event_run_time (agent_run_id, occurred_at DESC) 子查询取最新事件，代价低。"""
    return await q_all(
        """
        select r.agent_run_id, r.user_id, r.agent_team_instance_id, r.audit_trace_id, r.entry_source
        from sre_agent_run r
        where r.run_status='active' and r.deleted_at is null
          and coalesce(
                (select max(a.occurred_at) from sre_audit_event a where a.agent_run_id = r.agent_run_id),
                r.started_at
              ) < now() - make_interval(mins => %(ttl)s)
        """,
        {"ttl": ttl_minutes},
    )


async def close_idle(run_id: str, reason_code: str) -> int:
    """条件关闭（idle 回收专用）：仅当仍 active 才翻 closed，返回受影响行数——
    与用户并发 close/delete 幂等（已非 active 则 0 行、调用方跳过）。"""
    return await exec1(
        """
        update sre_agent_run
        set run_status='closed', status_reason_code=%(rc)s, ended_at=now(), last_update_date=now()
        where agent_run_id=%(r)s and run_status='active' and deleted_at is null
        """,
        {"r": run_id, "rc": reason_code},
    )


async def reopen_run(run_id: str) -> int:
    """撤销 idle 误关（并发起任务竞态）：closed→active 并清 ended_at/status_reason_code。
    仅回滚本次 idle_timeout 关的行（守 status_reason_code），绝不动用户主动关的会话。"""
    return await exec1(
        """
        update sre_agent_run
        set run_status='active', ended_at=null, status_reason_code=null, last_update_date=now()
        where agent_run_id=%(r)s and run_status='closed' and status_reason_code='idle_timeout'
        """,
        {"r": run_id},
    )


async def set_entry_source(run_id: str, source: str) -> int:
    """run 来源打标（alerts dispatcher 建诊断 run 后调用）：user=交互 / alert=告警自动诊断。"""
    return await exec1(
        "update sre_agent_run set entry_source=%(s)s, last_update_date=now() where agent_run_id=%(r)s",
        {"r": run_id, "s": source},
    )


async def reopen_alert_run(run_id: str) -> int:
    """告警 run 的「追问自动复开」：仅系统关闭（idle 回收 / 告警专属 TTL）可复开，
    用户主动 close 的守卫外——放宽 reason 范围但收紧 entry_source，二者都不满足绝不翻状态。"""
    return await exec1(
        """
        update sre_agent_run
        set run_status='active', ended_at=null, status_reason_code=null, last_update_date=now()
        where agent_run_id=%(r)s and run_status='closed' and entry_source='alert'
          and status_reason_code in ('idle_timeout', 'alert_idle')
        """,
        {"r": run_id},
    )


async def latest_scope_snapshot_by_instance(instance_id: str) -> dict[str, Any] | None:
    """实例最近一次范围快照（告警链夜间降级兜底源）；无历史 = 实例从未成功 resolve 过。"""
    return await q_one(
        """
        select * from sre_scope_snapshot
        where agent_team_instance_id=%(i)s
        order by computed_at desc limit 1
        """,
        {"i": instance_id},
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


async def cancel_pending_approvals(task_id: str, by: str) -> int:
    """任务终止级联收口（DEF-1）：该 task 的 pending 审批全部置 cancelled——
    否则子任务终态后遗留 pending 卡片，迟到 decide 会经旧 fallback 污染主任务握手。"""
    return await exec1(
        """
        update sre_approval_request
        set decision='cancelled', decided_by=%(b)s, decided_at=now(), last_updated_by=%(b)s, last_update_date=now()
        where task_id=%(t)s and decision='pending'
        """,
        {"t": task_id, "b": by},
    )


async def force_expire_approval(approval_id: str) -> None:
    """等待方主循环超时先于行内 expire_at（连带 B）：把该 pending 行置为已过期，
    交随后的 expire_stale_approvals 统一翻转（timeout+审计单出口，不在此直改 decision）。"""
    await exec1(
        "update sre_approval_request set expire_at=now() - interval '1 second', "
        "last_update_date=now(), last_updated_by='system' "
        "where approval_request_id=%(a)s and decision='pending'",
        {"a": approval_id},
    )


async def expire_stale_approvals(run_id: str) -> list[dict[str, Any]]:
    """ASK 超时（ASK-004）：pending 且过期 → timeout；返回被翻转行（供调用方补审计/SSE，连带 B）。"""
    return await q_all(
        """
        update sre_approval_request set decision='timeout', last_update_date=now(), last_updated_by='system'
        where agent_run_id=%(r)s and decision='pending' and expire_at is not null and expire_at < now()
        returning approval_request_id, task_id, agent_run_id, agent_team_instance_id, tool_call_name, audit_trace_id, user_id
        """,
        {"r": run_id},
    )


# ---- 四号校验（29.14）：函数族镜像 approval，独立表 sre_flow_check_request ----

async def create_flow_check(
    user_id: str, instance_id: str, run_id: str, task_id: str, tool_call_name: str,
    arguments_redacted: dict[str, Any], config_snapshot: dict[str, Any],
    audit_trace_id: str, framework_session_id: str, expire_minutes: int = 5,
) -> dict[str, Any]:
    """创建四号校验请求行；config_snapshot=创建时按租户解析后的配置（SSE 与 /state 恢复同口径）。"""
    fid = str(uuid.uuid4())
    await exec1(
        """
        insert into sre_flow_check_request
          (flow_check_request_id, user_id, agent_team_instance_id, agent_run_id, framework_session_id,
           task_id, tool_call_name, arguments_redacted_json, flow_check_config_json,
           decision, audit_trace_id, created_by, last_updated_by, expire_at)
        values (%(f)s, %(u)s, %(i)s, %(r)s, %(fs)s,
                %(t)s, %(n)s, %(arg)s, %(cfg)s,
                'pending', %(tr)s, %(u)s, %(u)s, now() + make_interval(mins => %(exp)s))
        """,
        {"f": fid, "u": user_id, "i": instance_id, "r": run_id, "fs": framework_session_id,
         "t": task_id, "n": tool_call_name, "arg": jsonb(arguments_redacted),
         "cfg": jsonb(config_snapshot), "tr": audit_trace_id, "exp": expire_minutes},
    )
    return (await get_flow_check(fid))  # type: ignore[return-value]


async def get_flow_check(flow_check_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_flow_check_request where flow_check_request_id=%(f)s", {"f": flow_check_id}
    )


async def pending_flow_checks(run_id: str) -> list[dict[str, Any]]:
    return await q_all(
        """
        select * from sre_flow_check_request
        where agent_run_id=%(r)s and decision='pending'
        order by creation_date
        """,
        {"r": run_id},
    )


async def decide_flow_check(flow_check_id: str, decision: str, decided_by: str) -> int:
    return await exec1(
        """
        update sre_flow_check_request
        set decision=%(d)s, decided_by=%(b)s, decided_at=now(), last_updated_by=%(b)s, last_update_date=now()
        where flow_check_request_id=%(f)s and decision='pending'
        """,
        {"f": flow_check_id, "d": decision, "b": decided_by},
    )


async def cancel_pending_flow_checks(task_id: str, by: str) -> int:
    """任务终止级联收口（同 cancel_pending_approvals）：防遗留 pending 卡片与迟到 decide。"""
    return await exec1(
        """
        update sre_flow_check_request
        set decision='cancelled', decided_by=%(b)s, decided_at=now(), last_updated_by=%(b)s, last_update_date=now()
        where task_id=%(t)s and decision='pending'
        """,
        {"t": task_id, "b": by},
    )


async def force_expire_flow_check(flow_check_id: str) -> None:
    """等待方主循环超时先于行内 expire_at：置过期交 expire_stale_flow_checks 统一翻转（单出口）。"""
    await exec1(
        "update sre_flow_check_request set expire_at=now() - interval '1 second', "
        "last_update_date=now(), last_updated_by='system' "
        "where flow_check_request_id=%(f)s and decision='pending'",
        {"f": flow_check_id},
    )


async def expire_stale_flow_checks(run_id: str) -> list[dict[str, Any]]:
    """四号校验超时：pending 且过期 → timeout；返回被翻转行（供调用方补审计/SSE）。"""
    return await q_all(
        """
        update sre_flow_check_request set decision='timeout', last_update_date=now(), last_updated_by='system'
        where agent_run_id=%(r)s and decision='pending' and expire_at is not null and expire_at < now()
        returning flow_check_request_id, task_id, agent_run_id, agent_team_instance_id, tool_call_name, audit_trace_id, user_id
        """,
        {"r": run_id},
    )
