"""Mock 编排器：把一次 Task 按「巡检→定界→RCA→ASK→恢复→结论」脚本化推进。

真实 AgentScope 2.0.3 runtime（B1 块，见 runtime/agentscope_runtime.py）与本模块**同签名并存**，
由环境变量 OPENOPS_RUNTIME 选择；本模块保留为 mock **test-double**（pytest/demo 可回退）。
两条后端经 runtime.emit 共享发射；工具调用统一走 runtime.tool_gateway（B4：标注/Scope/Secret/审计），
是否 ASK 由标注 is_approval_required 决定。
每步：先写 audit_event（事实源）再 publish SSE（体验）。敏感字段禁入事件（SEC-002）。
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from infra.repositories import runs
from runtime import tool_gateway
from runtime.emit import emit as _emit
from runtime.rca_demo import rca as _rca
from runtime.task_registry import TaskState
from runtime.tool_gateway import ToolBlocked

DELAY_MS = int(os.environ.get("OPENOPS_ORCH_DELAY_MS", "900"))


async def _sleep(st: TaskState) -> bool:
    """步间延时；返回 False 表示任务被取消，应停。"""
    await asyncio.sleep(DELAY_MS / 1000)
    return st.status == "running"


async def run_task(st: TaskState, run: dict[str, Any]) -> None:
    """脚本化推进。st.status 由 cancel 流程外部置位。"""
    try:
        if not await _sleep(st):
            return await _finish_cancel(st, run)

        # 巡检：指标查询（Gateway：标注/APPID 校验 + header 注入 + tool.call.* 事件）
        await tool_gateway.invoke(st, run, "query_resource", {"appid": "APP-A"},
                                  started_msg="巡检 · 指标查询",
                                  succeeded_msg="P99 / 错误率 / Redis 连接数已取回")
        st.rca = _rca(1, "定界中")
        await _emit(st, run, "openops.rca.updated", message="RCA 面板更新（定界中）", payload=st.rca)
        if not await _sleep(st):
            return await _finish_cancel(st, run)

        # 定界：拓扑依赖
        await tool_gateway.invoke(st, run, "query_resource", {"appid": "APP-A"},
                                  started_msg="定界 · 拓扑依赖",
                                  succeeded_msg="svc-payment-api → Redis / MySQL 依赖图")
        st.rca = _rca(2, "验证 H1")
        await _emit(st, run, "openops.rca.updated", message="假设排行更新（H1 领先）", payload=st.rca)
        if not await _sleep(st):
            return await _finish_cancel(st, run)

        # B8·补2：容器内只读诊断（证明 run_bash 接进编排循环）；env 门控默认关，不改现有 demo 序列
        if os.getenv("OPENOPS_DEMO_SANDBOX_STEP") == "1":
            from runtime.sandbox_bash import run_container_command
            await run_container_command(st, run, "ls -la")
            if not await _sleep(st):
                return await _finish_cancel(st, run)

        # 恢复动作（B4）：未标注/blocked → 直接 fail-closed（不 ASK，Gateway 拦）；
        # allowed 免审批 → 直接执行；allowed 需审批 → ASK
        ann = (st.tool_annotations or {}).get("recover_execute")
        allowed = ann is not None and ann.get("status") == "allowed"
        if not allowed or not ann.get("is_approval_required"):
            await tool_gateway.invoke(st, run, "recover_execute", {"appid": "APP-A", "action": "restart"},
                                      started_msg="执行恢复动作（标注免审批）",
                                      succeeded_msg="恢复动作已执行（execution 受控追踪）")
            st.rca = _rca(3, "已闭环")
            await _emit(st, run, "openops.rca.updated", message="结论确认：H1 连接泄漏，已恢复", payload=st.rca)
            st.status = "completed"
            return await _emit(st, run, "openops.task.completed", message="任务完成：根因 H1，已执行恢复", action="task")

        # ASK：恢复动作需人工批准（标注 is_approval_required=true）
        appr = await runs.create_approval(
            st.user_id, str(run["agent_team_instance_id"]), st.run_id, st.task_id, "recover_execute",
            {"appid": "APP-A", "action": "restart", "target": "svc-payment-api/svc-a"},
            str(run["audit_trace_id"]), str(run["framework_session_id"]),
        )
        st.approval_id = str(appr["approval_request_id"])
        await _emit(st, run, "openops.approval.required", severity="warning",
                    message="恢复动作待批准：重启 svc-a 释放连接",
                    payload={"approval_request_id": st.approval_id, "tool": "recover_execute",
                             "target": "APP-A · svc-payment-api/svc-a", "impact": "重启期间 svc-a 短暂不可用（约 15s）"})

        # 等待决策（decide/cancel 置 approval_ev；超时按 expire）
        try:
            await asyncio.wait_for(st.approval_ev.wait(), timeout=max(DELAY_MS / 1000 * 40, 300))
        except asyncio.TimeoutError:
            await runs.expire_stale_approvals(st.run_id)
            st.approval_result = "timeout"

        if st.status != "running":
            return await _finish_cancel(st, run)

        if st.approval_result == "approved":
            await tool_gateway.invoke(st, run, "recover_execute", {"appid": "APP-A", "action": "restart"},
                                      started_msg="执行恢复动作（已批准）",
                                      succeeded_msg="恢复动作已执行（execution 受控追踪）")
            st.rca = _rca(3, "已闭环")
            await _emit(st, run, "openops.rca.updated", message="结论确认：H1 连接泄漏，已恢复", payload=st.rca)
            st.status = "completed"
            await _emit(st, run, "openops.task.completed", message="任务完成：根因 H1，已执行恢复", action="task")
        elif st.approval_result == "timeout":
            await _emit(st, run, "openops.approval.timeout", severity="warning",
                        message="批准超时：恢复动作未执行", reason_code="APPROVAL_TIMEOUT")
            st.status = "completed"
            await _emit(st, run, "openops.task.completed", message="任务结束：待人工跟进恢复动作", action="task")
        else:  # rejected
            st.rca = _rca(2, "验证 H1", {"conclusion": "恢复动作被拒绝：保持观察，建议走短期配置优化。"})
            await _emit(st, run, "openops.rca.updated", message="恢复动作被拒绝，保持观察", payload=st.rca)
            st.status = "completed"
            await _emit(st, run, "openops.task.completed", message="任务结束：未执行恢复（用户拒绝）", action="task")
    except ToolBlocked as e:
        # Gateway fail-closed：tool.blocked 已发；任务按失败收口（reason_code 透传）
        st.status = "failed"
        await _emit(st, run, "openops.task.failed", severity="error", action="task",
                    message=f"任务失败：{e.message}", reason_code=e.reason_code)
    except asyncio.CancelledError:
        await _finish_cancel(st, run)
        raise


async def _finish_cancel(st: TaskState, run: dict[str, Any]) -> None:
    if st.status not in ("cancelled",):
        st.status = "cancelled"
    await _emit(st, run, "openops.task.cancelled", severity="warning",
                message="任务已取消（Run 保持 active，可继续新任务）", action="task")
