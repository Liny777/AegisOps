"""sre_agent_session_state 仓储（P 块）：AgentState 按 (framework_session_id, agent_key) 持久化。

同 run 跨 task 的记忆连续性事实源；agent_key 为 D 块 sub agent 预留（现恒 'main'）。
state_json = agentscope AgentState 的 pydantic 全量 JSON（老项目 storage_pg.update_session_state 同模式）。
"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, jsonb, q_one


async def get_state_json(framework_session_id: str, agent_key: str = "main") -> dict[str, Any] | None:
    row = await q_one(
        "select state_json from sre_agent_session_state "
        "where framework_session_id=%(f)s and agent_key=%(k)s and deleted_at is null",
        {"f": framework_session_id, "k": agent_key},
    )
    return row["state_json"] if row else None


async def upsert_state_json(framework_session_id: str, state: dict[str, Any],
                            agent_key: str = "main", updated_by: str = "system") -> None:
    n = await exec1(
        "update sre_agent_session_state set state_json=%(s)s, last_update_date=now(), last_updated_by=%(u)s "
        "where framework_session_id=%(f)s and agent_key=%(k)s and deleted_at is null",
        {"f": framework_session_id, "k": agent_key, "s": jsonb(state), "u": updated_by},
    )
    if n == 0:
        await exec1(
            """
            insert into sre_agent_session_state
              (session_state_id, framework_session_id, agent_key, state_json, created_by, last_updated_by)
            values (%(i)s, %(f)s, %(k)s, %(s)s, %(u)s, %(u)s)
            """,
            {"i": str(uuid.uuid4()), "f": framework_session_id, "k": agent_key,
             "s": jsonb(state), "u": updated_by},
        )
