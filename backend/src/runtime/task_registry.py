"""TaskRuntimeRegistry（进程内运行态；PG 不存 task 表，task 上下文活在 Run 内）。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class TaskState:
    task_id: str
    run_id: str
    user_id: str
    instance_id: str
    input_text: str
    status: str = "running"  # running/cancel_requested/cancelled/completed/failed
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    orchestrator: asyncio.Task[None] | None = None
    rca: dict[str, Any] | None = None  # RCA 快照（/state 恢复用）
    selected_model: str | None = None
    model_spec: dict[str, Any] | None = None  # 平台模型元数据（无 API Key）；agentscope 后端据此建真模型/回退 stub
    scope_ctx: dict[str, Any] | None = None  # ScopeContext（effective_appids/snapshot_id/revision）；Tool Gateway 校验用
    tool_annotations: dict[str, dict[str, Any]] | None = None  # 平台工具标注快照（by tool_name）；Gateway/ASK 判定用
    plan_notified: set[str] = field(default_factory=set)  # 已发过 runtime_plan.updated 的工具（每 task 每工具一次）
    tool_blocked: bool = False  # 本 task 出现过工具拦截（B6-RT-001：阻断后不得采纳模型「已恢复」结论）
    template_tools: set[str] = field(default_factory=set)  # 模板 default_tools（B7·二：RuntimePlan 只装配模板内工具）
    # ASK 决策握手（decide/cancel 置位；orchestrator 等待）
    approval_ev: asyncio.Event = field(default_factory=asyncio.Event)
    approval_result: str | None = None  # approved/rejected/timeout/cancelled
    approval_id: str | None = None


_by_run: dict[str, TaskState] = {}  # run_id → 当前 task（V1 每 Run 同时最多 1 个 running task 上下文）


def put(state: TaskState) -> None:
    _by_run[state.run_id] = state


def get_by_run(run_id: str) -> TaskState | None:
    return _by_run.get(run_id)


def get_by_task(task_id: str) -> TaskState | None:
    for st in _by_run.values():
        if st.task_id == task_id:
            return st
    return None


def running_count(user_id: str) -> int:
    return sum(1 for st in _by_run.values() if st.user_id == user_id and st.status == "running")


def instance_has_running(instance_id: str) -> bool:
    return any(st.instance_id == instance_id and st.status == "running" for st in _by_run.values())


def reset() -> None:  # 测试用
    for st in _by_run.values():
        if st.orchestrator and not st.orchestrator.done():
            st.orchestrator.cancel()
    _by_run.clear()
