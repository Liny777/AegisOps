"""Runtime Adapter：把 Task 提交给所选 runtime 后端。

两实现同签名 `run_task(st, run)`，产出一致的 `openops.*` 事件/审计（经 runtime.emit）：
- mock（默认）：runtime.orchestrator，脚本化推进，pytest/demo 的 test-double。
- agentscope：runtime.agentscope_runtime，真 AgentScope 2.0.3（B1，惰性导入）。

由环境变量 `OPENOPS_RUNTIME=mock|agentscope` 选择（默认 mock，保 pytest 不依赖 agentscope）。
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from runtime.task_registry import TaskState, put

log = logging.getLogger("openops.runtime")

RunTaskFn = Callable[[TaskState, dict[str, Any]], Awaitable[None]]


def _resolve_backend() -> RunTaskFn:
    backend = os.environ.get("OPENOPS_RUNTIME", "mock").strip().lower()
    if backend == "agentscope":
        from runtime import agentscope_runtime  # 惰性：仅此分支导入 agentscope

        return agentscope_runtime.run_task
    from runtime import orchestrator

    return orchestrator.run_task


def _log_failure(st: TaskState) -> None:
    def cb(t: asyncio.Task[None]) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            st.status = "failed"
            log.exception("runtime failed task=%s run=%s", st.task_id, st.run_id, exc_info=exc)

    assert st.orchestrator is not None
    st.orchestrator.add_done_callback(cb)


def submit_task(st: TaskState, run: dict[str, Any]) -> None:
    run_task = _resolve_backend()
    st.orchestrator = asyncio.create_task(run_task(st, run))
    _log_failure(st)
    put(st)
