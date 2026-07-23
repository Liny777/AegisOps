"""Agent Studio 身份上下文（零依赖，可无条件 import）。

run_task / _run_one 在 agent 开跑前把 (user_id, run_id, task_id, role) 写进 contextvar；
`studio_tracing.OpenOpsSpanProcessor.on_start` 从这里读并盖成 span 属性。默认 None =
非捕获上下文（后台 reconciler/sweeper 等起的 span 不落库）。

子 Agent 经 `asyncio.create_task` 运行，context 为 task 私有快照——`_run_one` 内 set 的
role=agent_key 不会泄漏回主 Agent。
"""
from __future__ import annotations

import os
from contextvars import ContextVar, Token

studio_user_id: ContextVar[str | None] = ContextVar("studio_user_id", default=None)
studio_run_id: ContextVar[str | None] = ContextVar("studio_run_id", default=None)
studio_task_id: ContextVar[str | None] = ContextVar("studio_task_id", default=None)
studio_agent_role: ContextVar[str | None] = ContextVar("studio_agent_role", default=None)

_StudioTokens = tuple[Token, Token, Token, Token]


def set_studio_task_context(user_id: str, run_id: str, task_id: str, role: str) -> _StudioTokens:
    return (
        studio_user_id.set(user_id),
        studio_run_id.set(run_id),
        studio_task_id.set(task_id),
        studio_agent_role.set(role),
    )


def reset_studio_task_context(tokens: _StudioTokens) -> None:
    t_uid, t_rid, t_tid, t_role = tokens
    studio_user_id.reset(t_uid)
    studio_run_id.reset(t_rid)
    studio_task_id.reset(t_tid)
    studio_agent_role.reset(t_role)


def studio_enabled() -> bool:
    """Agent Studio 捕获开关（默认开；仅 OPENOPS_RUNTIME=agentscope 时实际产生数据）。"""
    return os.getenv("OPENOPS_AGENT_STUDIO_ENABLED", "true").strip().lower() in ("1", "true", "yes")
