"""Agent Studio 切片的数据访问层。

两类查询：
- **切片自有表** `sre_agent_studio_span`：存 agent/llm/tool span **原文**（每字段已在采集层
  按 OPENOPS_STUDIO_TEXT_CAP 截断），仅 studio 的 admin/replay 端点可读；到期硬删
  （expire_at，OPENOPS_STUDIO_RETENTION_DAYS 默认 30 天）。写入方只有本切片。
- **core 表只读**：`list_runs_by_user_paged` 读 `sre_agent_run`。Studio 的产品定义就是
  「把 core 的运行记录重新组织给管理员看」，只读 core 表不可避免；但**绝不写 core 表**。
"""
from __future__ import annotations

import os
from typing import Any

from infra.db import exec1, q_all, q_one


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


async def list_runs_by_user_paged(
    user_id: str, *, limit: int, offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """某用户的 run 分页列表（**含软删**）—— Agent Studio 管理员回溯专用。

    ⚠ 与 core 的 `infra.repositories.runs.list_runs_by_user`（滤 `deleted_at is null`）
    刻意不同：管理员复盘**不受用户删会话影响**，所以这里不滤软删，`deleted_at` 原样
    输出给上层打标记。当初这个函数放在 core 的 runs.py 里，紧挨着那个滤软删的同名兄弟——
    迟早被「一致性清理」补上 `and deleted_at is null`，届时 studio 静默失效而 core 测试全绿。
    故随切片一起搬到这里：语义归谁，函数就归谁。

    读的是 core 表 `sre_agent_run`（只读；分页 + total 双查询，样板同 users.list_users_with_whitelist）。
    """
    rows = await q_all(
        """
        select * from sre_agent_run where user_id=%(u)s
        order by started_at desc limit %(l)s offset %(o)s
        """,
        {"u": user_id, "l": limit, "o": offset},
    )
    cnt = await q_one("select count(*) as n from sre_agent_run where user_id=%(u)s", {"u": user_id})
    return rows, int(cnt["n"]) if cnt else 0
