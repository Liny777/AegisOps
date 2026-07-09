"""Mock 编排器：把一次 Task 按「巡检→定界→RCA→ASK→恢复→结论」脚本化推进。

真实 AgentScope 2.0.3 runtime（B7 块）替换本模块；事件语义/审计投影保持不变。
每步：先写 audit_event（事实源）再 publish SSE（体验）。敏感字段禁入事件（SEC-002）。
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from infra.external import http_mcp_client
from infra.repositories import audit, runs
from runtime import events
from runtime.task_registry import TaskState

DELAY_MS = int(os.environ.get("OPENOPS_ORCH_DELAY_MS", "900"))


def _rca(revision: int, phase: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    base: dict[str, Any] = {
        "revision": revision,
        "title": "支付延迟突增",
        "phaseLabel": phase,
        "tiles": [
            {"label": "症状", "value": "下单 P99 180ms→1.4s"},
            {"label": "时间窗", "value": "10:02 起 · 持续 9min"},
            {"label": "影响面", "value": "APP-A 支付下单 · 0.6% 错误"},
            {"label": "当前阶段", "value": phase},
        ],
        "steps": [
            {"num": 1, "label": "范围", "state": "done"},
            {"num": 2, "label": "证据", "state": "done" if revision >= 2 else "active"},
            {"num": 3, "label": "假设", "state": "done" if revision >= 2 else "waiting"},
            {"num": 4, "label": "验证", "state": "active" if revision >= 2 else "waiting"},
            {"num": 5, "label": "结论", "state": "done" if revision >= 3 else "waiting"},
        ],
        "currentQ": "Redis 连接饱和是慢查询导致，还是连接泄漏导致？",
        "why": "两者恢复动作不同：慢查询→限流/优化，连接泄漏→重启释放。",
        "facts": [
            {"text": "10:02 P99 由 180ms 升至 1.4s，错误率 0.6%"},
            {"text": "svc-payment-api → Redis active 连接打满（1000/1000）"},
        ],
        "unknowns": [{"text": "连接是否泄漏还是被慢查询长期占用"}],
        "sources": [
            {"name": "Prometheus", "status": "done", "tone": "good"},
            {"name": "Loki", "status": "running" if revision < 2 else "done", "tone": "warning" if revision < 2 else "good"},
            {"name": "oModel 拓扑", "status": "done", "tone": "good"},
        ],
        "hypotheses": [
            {"text": "H1 Redis 连接泄漏（svc-a 未释放）", "tag": "支持", "tagTone": "good", "conf": 0.72 if revision < 3 else 0.9},
            {"text": "H2 下游慢查询占用连接", "tag": "部分支持", "tagTone": "warning", "conf": 0.41},
        ],
        "actions": [
            {"tier": "立即", "text": "重启 svc-payment-api 实例 svc-a 释放连接", "confirm": True, "impact": "3 实例",
             "status": "待确认" if revision < 3 else "已执行", "statusTone": "warning" if revision < 3 else "good"},
            {"tier": "短期", "text": "对 Redis 连接加 max-idle 与超时回收", "impact": "配置", "status": "建议", "statusTone": "neutral"},
        ],
        "conclusion": "根因倾向 H1（连接泄漏）：重启 svc-a 后确认连接回落即可定论。"
        if revision < 3 else "已确认 H1：重启 svc-a 后连接数回落、P99 恢复 210ms，事件闭环。",
    }
    base.update(extra or {})
    return base


async def _emit(st: TaskState, run: dict[str, Any], event_type: str, **kw: Any) -> None:
    trace = str(run["audit_trace_id"])
    await audit.insert_event(
        audit_trace_id=trace, event_type=event_type, user_id=st.user_id,
        run_id=st.run_id, instance_id=str(run["agent_team_instance_id"]), task_id=st.task_id,
        action=kw.get("action", ""), decision=kw.get("decision"), reason_code=kw.get("reason_code"),
        payload_redacted=kw.get("payload"), external_request_id=kw.get("external_request_id"),
    )
    events.publish(st.run_id, events.envelope(
        st.run_id, event_type, task_id=st.task_id, message=kw.get("message", ""),
        reason_code=kw.get("reason_code"), severity=kw.get("severity", "info"),
        payload=kw.get("payload"), audit_trace_id=trace,
    ))


async def _sleep(st: TaskState) -> bool:
    """步间延时；返回 False 表示任务被取消，应停。"""
    await asyncio.sleep(DELAY_MS / 1000)
    return st.status == "running"


async def run_task(st: TaskState, run: dict[str, Any]) -> None:
    """脚本化推进。st.status 由 cancel 流程外部置位。"""
    try:
        if not await _sleep(st):
            return await _finish_cancel(st, run)

        # 巡检：指标查询
        await _emit(st, run, "openops.tool.call.started", message="巡检 · 指标查询", action="query_resource",
                    payload={"tool": "query_resource", "appid": "APP-A"})
        r1 = await http_mcp_client.call_tool("query_resource", {"appid": "APP-A"})
        await _emit(st, run, "openops.tool.call.succeeded", message="P99 / 错误率 / Redis 连接数已取回",
                    action="query_resource", external_request_id=r1["request_id"],
                    payload={"summary": r1.get("result_summary", "")})
        st.rca = _rca(1, "定界中")
        await _emit(st, run, "openops.rca.updated", message="RCA 面板更新（定界中）", payload=st.rca)
        if not await _sleep(st):
            return await _finish_cancel(st, run)

        # 定界：拓扑依赖
        await _emit(st, run, "openops.tool.call.started", message="定界 · 拓扑依赖", action="query_resource",
                    payload={"tool": "query_resource", "appid": "APP-A", "view": "topo"})
        r2 = await http_mcp_client.call_tool("query_resource", {"appid": "APP-A"})
        await _emit(st, run, "openops.tool.call.succeeded", message="svc-payment-api → Redis / MySQL 依赖图",
                    action="query_resource", external_request_id=r2["request_id"])
        st.rca = _rca(2, "验证 H1")
        await _emit(st, run, "openops.rca.updated", message="假设排行更新（H1 领先）", payload=st.rca)
        if not await _sleep(st):
            return await _finish_cancel(st, run)

        # ASK：恢复动作需人工批准（管理员标注 is_approval_required=true）
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
            r3 = await http_mcp_client.call_tool("recover_execute", {"appid": "APP-A", "action": "restart"})
            await _emit(st, run, "openops.tool.call.succeeded", message="恢复动作已执行（execution 受控追踪）",
                        action="recover_execute", external_request_id=r3["request_id"],
                        payload={"execution_id": r3.get("execution_id", "")})
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
    except asyncio.CancelledError:
        await _finish_cancel(st, run)
        raise


async def _finish_cancel(st: TaskState, run: dict[str, Any]) -> None:
    if st.status not in ("cancelled",):
        st.status = "cancelled"
    await _emit(st, run, "openops.task.cancelled", severity="warning",
                message="任务已取消（Run 保持 active，可继续新任务）", action="task")
