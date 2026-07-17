"""sre_task_state 仓储（P 块）：任务运行态影子快照。

内存 task_registry = 热路径事实；本表 = 跨进程影子（重启孤儿收敛 / get_state 回退展示）。
只落可序列化字段（asyncio.Task/Event 活对象不落）；同一 task 的写入由 emit 串行驱动，无并发冲突。
"""
from __future__ import annotations

from typing import Any

from infra.db import exec1, jsonb, q_all, q_one

TERMINAL = ("completed", "failed", "cancelled", "interrupted")


def _params(st: Any, status: str, audit_trace_id: str) -> dict[str, Any]:
    return {
        "t": st.task_id, "r": st.run_id, "u": st.user_id, "i": st.instance_id,
        "s": status, "x": st.input_text, "rc": jsonb(st.rca) if st.rca else None,
        "m": st.selected_model, "sc": jsonb(st.scope_ctx) if st.scope_ctx else None,
        "a": st.approval_id, "sa": st.started_at, "tr": audit_trace_id,
    }


async def upsert_snapshot(st: Any, status: str, audit_trace_id: str) -> None:
    row = await q_one("select 1 as x from sre_task_state where task_id=%(t)s", {"t": st.task_id})
    p = _params(st, status, audit_trace_id)
    if row is None:
        await exec1(
            """
            insert into sre_task_state
              (task_id, run_id, user_id, instance_id, task_status, input_text, rca_json,
               selected_model, scope_ctx_json, approval_id, started_at, audit_trace_id,
               created_by, last_updated_by)
            values (%(t)s, %(r)s, %(u)s, %(i)s, %(s)s, %(x)s, %(rc)s, %(m)s, %(sc)s, %(a)s,
                    %(sa)s, %(tr)s, %(u)s, %(u)s)
            """,
            p,
        )
    else:
        await exec1(
            """
            update sre_task_state
            set task_status=%(s)s, rca_json=%(rc)s, selected_model=%(m)s, approval_id=%(a)s,
                last_update_date=now(), last_updated_by=%(u)s
            where task_id=%(t)s
            """,
            p,
        )


async def get_latest_by_run(run_id: str) -> dict[str, Any] | None:
    # get_state 重启回退只投影主任务：子行（task_id 含 '.'）晚于主行创建，否则会顶替成"活跃任务"。
    return await q_one(
        "select * from sre_task_state where run_id=%(r)s and deleted_at is null "
        "and strpos(task_id, '.') = 0 "
        "order by creation_date desc limit 1",
        {"r": run_id},
    )


async def get_by_task(task_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_task_state where task_id=%(t)s and deleted_at is null", {"t": task_id}
    )


async def count_running(user_id: str) -> int:
    # 并发限额只数主任务：子 Agent task_id 形如 {leader}.{key}-{hash8}（含 '.'），主任务 tsk_<hex> 无 '.'。
    # 这条谓词也让进程内此前已泄漏的子行下次 start_task 立即不再计入（无需等重启 converge）。
    row = await q_one(
        "select count(*) as n from sre_task_state where user_id=%(u)s and task_status='running' "
        "and deleted_at is null and strpos(task_id, '.') = 0",
        {"u": user_id},
    )
    return int(row["n"]) if row else 0


async def mark_status(task_id: str, status: str, updated_by: str) -> int:
    return await exec1(
        "update sre_task_state set task_status=%(s)s, last_update_date=now(), last_updated_by=%(u)s "
        "where task_id=%(t)s",
        {"t": task_id, "s": status, "u": updated_by},
    )


async def list_running() -> list[dict[str, Any]]:
    return await q_all("select * from sre_task_state where task_status='running' and deleted_at is null")
