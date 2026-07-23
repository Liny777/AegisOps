"""sre_agent_studio_span 仓储（Agent Studio 管理员回溯复盘）。

存 agent/llm/tool span **原文**（每字段已在采集层按 OPENOPS_STUDIO_TEXT_CAP 截断），
仅 /admin/studio/* 端点可读；到期硬删（expire_at，OPENOPS_STUDIO_RETENTION_DAYS 默认 30 天）。
"""
from __future__ import annotations

import os
from typing import Any

from infra.db import exec1, q_all


def _retention_days() -> int:
    try:
        return max(1, int(os.environ.get("OPENOPS_STUDIO_RETENTION_DAYS", "30")))
    except ValueError:
        return 30


async def insert_span(
    *,
    user_id: str = "",
    agent_run_id: str = "",
    task_id: str = "",
    session_id: str = "",
    agent_role: str = "",
    agent_name: str = "",
    kind: str = "",
    model: str = "",
    provider: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_input_tokens: int = 0,
    latency_ms: float = 0.0,
    started_at: float = 0.0,
    ended_at: float = 0.0,
    tool_name: str = "",
    tool_args: str = "",
    tool_result: str = "",
    input_messages: str = "",
    output_messages: str = "",
    finish_reason: str = "",
    span_status: str = "",
    trace_id: str = "",
    span_id: str = "",
    parent_span_id: str = "",
) -> None:
    """started_at/ended_at 为 epoch 秒（OTel span 纳秒 /1e9），入库转 timestamptz。"""
    await exec1(
        """
        insert into sre_agent_studio_span
          (user_id, agent_run_id, task_id, session_id, agent_role, agent_name, kind,
           model, provider, input_tokens, output_tokens, cache_input_tokens, latency_ms,
           started_at, ended_at, tool_name, tool_args, tool_result,
           input_messages, output_messages, finish_reason, span_status,
           trace_id, span_id, parent_span_id, expire_at)
        values (%(u)s, %(r)s, %(tk)s, %(sid)s, %(ro)s, %(an)s, %(k)s,
                %(m)s, %(pv)s, %(it)s, %(ot)s, %(ct)s, %(lm)s,
                to_timestamp(%(sa)s), to_timestamp(%(ea)s), %(tn)s, %(ta)s, %(tr)s,
                %(im)s, %(om)s, %(fr)s, %(ss)s,
                %(ti)s, %(si)s, %(pi)s, now() + make_interval(days => %(rd)s))
        """,
        {"u": user_id, "r": agent_run_id, "tk": task_id, "sid": session_id, "ro": agent_role,
         "an": agent_name, "k": kind, "m": model, "pv": provider, "it": input_tokens,
         "ot": output_tokens, "ct": cache_input_tokens, "lm": latency_ms,
         "sa": started_at, "ea": ended_at, "tn": tool_name, "ta": tool_args, "tr": tool_result,
         "im": input_messages, "om": output_messages, "fr": finish_reason, "ss": span_status,
         "ti": trace_id, "si": span_id, "pi": parent_span_id, "rd": _retention_days()},
    )


async def list_spans_by_run(run_id: str) -> list[dict[str, Any]]:
    return await q_all(
        "select * from sre_agent_studio_span where agent_run_id=%(r)s order by id",
        {"r": run_id},
    )


async def stats_by_run_ids(run_ids: list[str]) -> dict[str, dict[str, Any]]:
    """run 列表页的聚合指标；返回 {run_id: {agents, llm_calls, tool_calls, total_tokens, latency_ms}}。"""
    if not run_ids:
        return {}
    rows = await q_all(
        """
        select agent_run_id,
               count(distinct session_id)                                as agents,
               count(*) filter (where kind = 'llm')                      as llm_calls,
               count(*) filter (where kind = 'tool')                     as tool_calls,
               coalesce(sum(input_tokens + output_tokens), 0)            as total_tokens,
               coalesce(sum(latency_ms) filter (where kind in ('llm','tool')), 0) as latency_ms
        from sre_agent_studio_span
        where agent_run_id = any(%(ids)s)
        group by agent_run_id
        """,
        {"ids": run_ids},
    )
    return {
        str(r["agent_run_id"]): {
            "agents": int(r["agents"]), "llm_calls": int(r["llm_calls"]),
            "tool_calls": int(r["tool_calls"]), "total_tokens": int(r["total_tokens"]),
            "latency_ms": float(r["latency_ms"]),
        }
        for r in rows
    }


async def purge_expired() -> int:
    return await exec1("delete from sre_agent_studio_span where expire_at <= now()")
