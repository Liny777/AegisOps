"""容器内受控 Bash 的审计化编排（B8-3）：四层裁决 → 审计 → 执行。

装配层：把 command_guard 决策、sandbox.command.* 审计、HITL 审批与容器执行串起来。
在真 agentscope agent 循环中，Bash 作为工具由框架 PermissionEngine 的 RequireUserConfirmEvent
桥做 HITL（B1 的 `_handle_ask`）；本函数是运行时无关的受控执行入口，`approver` 注入审批解析
（live 路径接 HITL 桥，测试注入 approve/reject）。命令行入审计、输出脱敏截断，不含任何平台注入项。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from runtime.emit import emit
from runtime.task_registry import TaskState
from sandbox import command_guard
from sandbox.executor import SkillResult
from sandbox.executor import executor as sandbox_executor


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
