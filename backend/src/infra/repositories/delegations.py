"""sre_agent_delegation 仓储（D 块）：main→sub 派发账本。

预算两层（老 D6 算法迁移）：
- 活跃数 = 该 leader task 下非终态行（max_children 对照）——完成即释放名额；
- 累计数 = 该 leader task 下**全部行含软删**（delegation_max_spawns 对照）——防"删了重派"重置计数。
"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, q_all, q_one

TERMINAL = ("completed", "failed_no_report", "timeout", "cancelled")


async def create(run_id: str, leader_task_id: str, agent_key: str, task_text: str,
                 deadline_s: float, created_by: str) -> str:
    did = str(uuid.uuid4())
    await exec1(
        """
        insert into sre_agent_delegation
          (delegation_id, run_id, leader_task_id, agent_key, task_text, delegation_status,
           deadline, created_by, last_updated_by)
        values (%(d)s, %(r)s, %(l)s, %(k)s, %(t)s, 'running',
                now() + make_interval(secs => %(dl)s), %(u)s, %(u)s)
        """,
        {"d": did, "r": run_id, "l": leader_task_id, "k": agent_key,
         "t": task_text[:2000], "dl": deadline_s, "u": created_by},
    )
    return did


async def mark_terminal(delegation_id: str, status: str, report_text: str | None,
                        had_final_report: bool, updated_by: str) -> int:
    return await exec1(
        """
        update sre_agent_delegation
        set delegation_status=%(s)s, report_text=%(rp)s, had_final_report=%(h)s,
            last_update_date=now(), last_updated_by=%(u)s
        where delegation_id=%(d)s
        """,
        {"d": delegation_id, "s": status, "rp": (report_text or "")[:8000] or None,
         "h": had_final_report, "u": updated_by},
    )


async def count_active_for_leader(leader_task_id: str) -> int:
    row = await q_one(
        "select count(*) as n from sre_agent_delegation "
        "where leader_task_id=%(l)s and delegation_status='running' and deleted_at is null",
        {"l": leader_task_id},
    )
    return int(row["n"]) if row else 0


async def count_total_for_leader(leader_task_id: str) -> int:
    # 故意含软删行：TeamDelete 式重置攻不破累计兜底（老 D6 反死循环口径）
    row = await q_one(
        "select count(*) as n from sre_agent_delegation where leader_task_id=%(l)s",
        {"l": leader_task_id},
    )
    return int(row["n"]) if row else 0


async def list_by_task(leader_task_id: str) -> list[dict[str, Any]]:
    return await q_all(
        "select * from sre_agent_delegation where leader_task_id=%(l)s and deleted_at is null "
        "order by creation_date",
        {"l": leader_task_id},
    )
