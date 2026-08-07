"""容器内受控 Bash 的审计化编排（B8-3）：四层裁决 → 审计 → 执行。

装配层：把 command_guard 决策、sandbox.command.* 审计、HITL 审批与容器执行串起来。
在真 agentscope agent 循环中，Bash 作为工具由框架 PermissionEngine 的 RequireUserConfirmEvent
桥做 HITL（B1 的 `_handle_ask`）；本函数是运行时无关的受控执行入口，`approver` 注入审批解析
（live 路径接 HITL 桥，测试注入 approve/reject）。命令行入审计、输出脱敏截断，不含任何平台注入项。
"""
from __future__ import annotations

import asyncio
import os
import shlex
from typing import Any, Awaitable, Callable

from infra.redact import redact_args
from infra.repositories import runs
from runtime.emit import emit
from runtime.task_registry import TaskState
from sandbox import command_guard
from sandbox.executor import SkillResult
from sandbox.executor import executor as sandbox_executor

_ASK_TIMEOUT_S = float(os.environ.get("OPENOPS_ASK_TIMEOUT_S", "300"))
# B8·补3（Skill 内容截断修复）：run_container_command 回模型的字符上限——原硬编码 2000 太紧，读
# Skill 手册/references 大文件被切成前 2000 字符，agent「没按 skill 内容执行」。放宽到 16000（> 手册
# cap 12000，一次 cat 即可读完典型 SKILL.md）。读大文件另有官方 Read 工具（绑容器后端，带分页、不受
# 本上限约束）。均 env 可调，风格同其他 OPENOPS_SKILL_* 旋钮。
_BASH_OUTPUT_CHARS = int(os.environ.get("OPENOPS_BASH_OUTPUT_CHARS", "16000"))
_READ_FILE_CHARS = int(os.environ.get("OPENOPS_READ_FILE_CHARS", "40000"))
_LIST_MAXDEPTH = int(os.environ.get("OPENOPS_LIST_MAXDEPTH", "3"))
_LIST_MAX_ENTRIES = int(os.environ.get("OPENOPS_LIST_MAX_ENTRIES", "500"))


async def run_bash(
    st: TaskState, run: dict[str, Any], command: str, *,
    cfg: dict[str, Any], approver: Callable[[], Awaitable[bool]],
) -> SkillResult:
    """决策 → 审计 → 执行一条容器内 Bash 命令。deny/拒绝审批则不执行。"""
    deny_prefixes = command_guard.parse_deny_prefixes(cfg.get("bash_deny_prefixes"))
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

    # allow，或 ask 已批准 → 容器内执行（容器缺失自愈重建）
    res = await sandbox_executor.run_command(getattr(st, "sandbox_uid", "") or st.user_id, command, run_id=st.run_id, cfg=st.sandbox_cfg)
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
    args = redact_args({"command": command})  # 同一份脱敏入参：入审批行 + 进事件 payload（卡片展示）
    appr = await runs.create_approval(
        st.user_id, str(run["agent_team_instance_id"]), st.run_id, st.task_id, "run_container_command",
        args, str(run["audit_trace_id"]), str(run["framework_session_id"]),
    )
    st.approval_id = str(appr["approval_request_id"])
    # payload 带 args（脱敏入参字典，供审批卡逐项展示"批哪个工具的什么入参"）；保留 command 兼容旧前端
    await emit(st, run, "openops.approval.required", severity="warning", action="bash",
               message="容器内命令待批准",
               payload={"approval_request_id": st.approval_id, "tool": "run_container_command",
                        "command": command, "args": args})
    try:
        await asyncio.wait_for(st.approval_ev.wait(), timeout=_ASK_TIMEOUT_S)
    except asyncio.TimeoutError:
        from runtime.emit import expire_stale_approvals_and_audit as _exp
        await _exp(st.run_id, force_approval_id=st.approval_id)  # 循环超时 ⇒ 本行必达 timeout
        st.approval_result = "timeout"
    return st.approval_result == "approved"


async def run_container_command(st: TaskState, run: dict[str, Any], command: str) -> str:
    """agent 循环入口（B8·补2）：Agent 在自己容器内跑一条 shell 命令，四层裁决 + 审计 + HITL。

    只读命令直接执行；非只读经 approval_ev 审批（与恢复 tool 同机制）；deny 拒绝。返回给模型的文本结果。
    容器由 run 开启时会话期常驻就位；缺失（未开 run/已回收）返回错误文本，不崩溃。
    """
    # DEF-7：不再按「容器在册」前置拦截——那恰好短路了唯一需要自愈的场景（重启/idle 回收后
    # executor.run_command 的 _get_or_revive 会现场重建）。只在缺会话上下文时 fail-closed。
    if not st.run_id or st.sandbox_cfg is None:
        return "容器不可用（缺少会话上下文），命令未执行"

    async def approver() -> bool:
        return await _bridge_command_approval(st, run, command)

    res = await run_bash(st, run, command, cfg=st.sandbox_cfg or {}, approver=approver)
    body = f"exit={res.exit_code}\n{res.stdout}\n{res.stderr}".strip()
    if not body:
        return f"status={res.status}"
    if len(body) > _BASH_OUTPUT_CHARS:
        return (body[:_BASH_OUTPUT_CHARS]
                + f"\n…（输出共 {len(body)} 字符，超 {_BASH_OUTPUT_CHARS} 已截断。读大文件请用 Read 工具"
                  f"（绑容器后端、带 offset/limit 分页、不受此上限约束），"
                  f"或 `tail -n +N '文件' | head -n M` 分段读）")
    return body


async def list_container_files(st: TaskState, run: dict[str, Any], path: str = ".", *,
                               pattern: str | None = None) -> str:
    """列出容器内目录下的文件（可选文件名通配过滤），Glob/LS 工具等价物（B8·补3）。

    用可移植 find（避免 GNU-only 的 -printf，容器多为 busybox/Alpine）；path/pattern 经 shlex 引用。
    机器拼装、可证只读，直接经 executor 执行，单独发 openops.sandbox.file.list 审计。
    """
    if not st.run_id or st.sandbox_cfg is None:
        return "容器不可用（缺少会话上下文）"
    q = shlex.quote(path)
    name = f"-name {shlex.quote(pattern)} " if pattern else ""
    cmd = f"find {q} -maxdepth {_LIST_MAXDEPTH} {name}2>/dev/null | head -n {_LIST_MAX_ENTRIES}"
    res = await sandbox_executor.run_command(getattr(st, "sandbox_uid", "") or st.user_id, cmd, run_id=st.run_id, cfg=st.sandbox_cfg)
    await emit(st, run, "openops.sandbox.file.list", action="list_files",
               message=f"列出容器目录 {path}", decision=res.status,
               payload={"path": path, "pattern": pattern, "status": res.status})
    if res.status != "success":
        return f"列目录失败（{res.status}，exit={res.exit_code}）：{(res.stderr or '')[:500] or path}"
    listing = (res.stdout or "").strip()
    if not listing:
        return f"目录 {path} 下未找到文件" + (f"（pattern={pattern}）" if pattern else "")
    n = listing.count("\n") + 1
    note = (f"\n\n（已达 {_LIST_MAX_ENTRIES} 条上限，可能未列全——请收窄 path 或用 pattern 过滤）"
            if n >= _LIST_MAX_ENTRIES else "")
    out = f"{path} 下的文件（共 {n} 条）：\n{listing}{note}"
    if len(out) > _READ_FILE_CHARS:
        out = out[:_READ_FILE_CHARS] + "\n…（已截断）"
    return out
