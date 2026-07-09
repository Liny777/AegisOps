"""容器内受控 Bash 的审计化编排（B8-3）：四层裁决 → 审计 → 执行。

装配层：把 command_guard 决策、sandbox.command.* 审计、HITL 审批与容器执行串起来。
在真 agentscope agent 循环中，Bash 作为工具由框架 PermissionEngine 的 RequireUserConfirmEvent
桥做 HITL（B1 的 `_handle_ask`）；本函数是运行时无关的受控执行入口，`approver` 注入审批解析
（live 路径接 HITL 桥，测试注入 approve/reject）。命令行入审计、输出脱敏截断，不含任何平台注入项。
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable

from infra.repositories import runs
from runtime.emit import emit
from runtime.task_registry import TaskState
from sandbox import command_guard
from sandbox.executor import SkillResult
from sandbox.executor import executor as sandbox_executor

_ASK_TIMEOUT_S = float(os.environ.get("OPENOPS_ASK_TIMEOUT_S", "300"))


async def run_bash(
    st: TaskState, run: dict[str, Any], command: str, *,
    cfg: dict[str, Any], approver: Callable[[], Awaitable[bool]],
) -> SkillResult:
    """决策 → 审计 → 执行一条容器内 Bash 命令。deny/拒绝审批则不执行。"""
    deny_prefixes = list(cfg.get("bash_deny_prefixes", []) or [])
    d = await command_guard.decide_async(command, deny_prefixes=deny_prefixes)

    if d.action == "deny":
        st.tool_blocked = True  # 与 B6-RT-001 一致：容器内动作被拦截，不得宣称已执行
        await emit(st, run, "openops.sandbox.command.denied", severity="warning", action="bash",
                   message=f"命令被拦截（第{d.layer}层：{d.reason}）", reason_code="TOOL_BLOCKED",
                   payload={"command": command, "layer": d.layer})
        return SkillResult(status="denied", exit_code=-1, stdout="", stderr=d.reason)

    if d.action == "ask":
        await emit(st, run, "openops.sandbox.command.asked", action="bash",
                   message=f"命令需人工确认（第{d.layer}层：{d.reason}）", payload={"command": command})
        approved = await approver()
        if not approved:
            st.tool_blocked = True
            await emit(st, run, "openops.sandbox.command.denied", severity="warning", action="bash",
                       message="命令被用户拒绝，未执行", reason_code="APPROVAL_REJECTED",
                       payload={"command": command})
            return SkillResult(status="denied", exit_code=-1, stdout="", stderr="用户拒绝执行")

    # allow，或 ask 已批准 → 容器内执行
    res = await sandbox_executor.run_command(st.user_id, command)
    await emit(st, run, "openops.sandbox.command.executed", action="bash",
               message="命令执行完成", decision="success" if res.status == "success" else res.status,
               payload={"command": command, "exit_code": res.exit_code,
                        "stdout": res.stdout[:2000], "stderr": res.stderr[:2000]})
    return res


async def _bridge_command_approval(st: TaskState, run: dict[str, Any], command: str) -> bool:
    """非只读命令的 HITL 桥：建 approval_request + 发 approval.required + 等 approval_ev（decide/cancel 置位）。

    复用与恢复类 tool 同一审批机制（approval_ev / decide_approval）；返回是否批准。
    """
    st.approval_ev.clear()  # 多次 ASK 复用同一 event，等待前清位
    st.approval_result = None
    appr = await runs.create_approval(
        st.user_id, str(run["agent_team_instance_id"]), st.run_id, st.task_id, "run_container_command",
        {"command": command}, str(run["audit_trace_id"]), str(run["framework_session_id"]),
    )
    st.approval_id = str(appr["approval_request_id"])
    await emit(st, run, "openops.approval.required", severity="warning", action="bash",
               message="容器内命令待批准",
               payload={"approval_request_id": st.approval_id, "tool": "run_container_command", "command": command})
    try:
        await asyncio.wait_for(st.approval_ev.wait(), timeout=_ASK_TIMEOUT_S)
    except asyncio.TimeoutError:
        await runs.expire_stale_approvals(st.run_id)
        st.approval_result = "timeout"
    return st.approval_result == "approved"


async def run_container_command(st: TaskState, run: dict[str, Any], command: str) -> str:
    """agent 循环入口（B8·补2）：Agent 在自己容器内跑一条 shell 命令，四层裁决 + 审计 + HITL。

    只读命令直接执行；非只读经 approval_ev 审批（与恢复 tool 同机制）；deny 拒绝。返回给模型的文本结果。
    容器由 run 开启时会话期常驻就位；缺失（未开 run/已回收）返回错误文本，不崩溃。
    """
    if sandbox_executor.get(st.user_id) is None:
        return "容器不可用（会话未就绪），命令未执行"

    async def approver() -> bool:
        return await _bridge_command_approval(st, run, command)

    res = await run_bash(st, run, command, cfg=st.sandbox_cfg or {}, approver=approver)
    body = f"exit={res.exit_code}\n{res.stdout}\n{res.stderr}".strip()
    return body[:2000] if body else f"status={res.status}"
