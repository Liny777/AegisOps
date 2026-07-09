"""Runtime Adapter（mock）：把 Task 提交给编排器。B7 块换真 AgentScope 2.0.3。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from runtime import orchestrator
from runtime.task_registry import TaskState, put

log = logging.getLogger("openops.runtime")


def _log_failure(st: TaskState) -> None:
    def cb(t: asyncio.Task[None]) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            st.status = "failed"
            log.exception("orchestrator failed task=%s run=%s", st.task_id, st.run_id, exc_info=exc)

    assert st.orchestrator is not None
    st.orchestrator.add_done_callback(cb)


def submit_task(st: TaskState, run: dict[str, Any]) -> None:
    st.orchestrator = asyncio.create_task(orchestrator.run_task(st, run))
    _log_failure(st)
    put(st)
