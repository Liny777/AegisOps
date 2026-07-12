"""D 块：main→sub 同步批量派发（gather 模式，零 monkey-patch）。

架构拍板（对老 openOps-Dev D6 的取舍）：
- **同步批量 gather**：dispatch 工具内并行跑各子 Agent 的 reply_stream 循环，全部终态后合并汇报
  作为工具返回——放弃老项目 park/wakeup 异步（新项目无 app-server 消息总线，那族复杂度整体消失）；
  主任务取消 → gather 级联取消子 Agent。
- **可搬层照搬**：delegation 账本状态机 + 两层预算（活跃 max_children / 累计 max_spawns 含软删防重置）
  + 超时裁决（wait_for 即 watcher）+ 37 号汇报纪律 prompt。
- **V1.x 安全边界：子 Agent 只读**——需审批（is_approval_required）的工具在子 toolkit 白名单
  构建时剔除，恢复动作由 main 亲自执行走既有 ASK 门；审批桥到子 Agent 列 D 块二期。
- 子 Agent 禁二层派发（child.sub_agents 恒 None → 不注册 dispatch 工具，天然 1 层）。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from infra.repositories import delegations
from runtime.emit import emit
from runtime.task_registry import TaskState

log = logging.getLogger("openops.subagent")

SUB_TIMEOUT_S = float(os.environ.get("OPENOPS_SUBAGENT_TIMEOUT_S", "300"))
_MAX_BATCH = 5  # 单次派发批大小硬上限（max_children 再往下收）


def _child_state(st: TaskState, sub: dict[str, Any], agent_key: str, text: str, seq: int) -> TaskState:
    """子 TaskState：继承 leader 的 scope/沙箱/模型，工具面按角色画像白名单裁剪（per-agent 隔离）。"""
    child = TaskState(task_id=f"{st.task_id}.{agent_key}{seq}", run_id=st.run_id, user_id=st.user_id,
                      instance_id=st.instance_id, input_text=text)
    child.agent_key = agent_key
    child.scope_ctx = st.scope_ctx
    child.sandbox_cfg = st.sandbox_cfg
    child.model_spec = st.model_spec
    child.selected_model = st.selected_model
    allowed_sk = set(sub.get("skills") or [])
    child.available_skills = {k: v for k, v in (st.available_skills or {}).items() if k in allowed_sk}
    anns = st.tool_annotations or {}
    # 只读边界：白名单 ∩ 免审批（需 ASK 的写工具剔除——Tool Gateway 的模板集校验按此 fail-closed）
    child.template_tools = {
        t for t in (sub.get("mcp_tools") or [])
        if not (anns.get(t) or {}).get("is_approval_required")
    }
    child.tool_annotations = {k: v for k, v in anns.items() if k in child.template_tools}
    return child


async def _run_one(st: TaskState, run: dict[str, Any], sub: dict[str, Any],
                   agent_key: str, text: str, seq: int) -> str:
    """跑一个子 Agent 完整 ReAct 循环（reply_stream 内部迭代）；返回其汇报文本。"""
    from agentscope.agent import Agent
    from agentscope.event import (
        ModelCallEndEvent,
        ModelCallStartEvent,
        RequireUserConfirmEvent,
        TextBlockDeltaEvent,
    )
    from agentscope.message import Msg, TextBlock
    from agentscope.state import AgentState

    from infra.seed import SUB_REPORT_DISCIPLINE
    from runtime import agentscope_runtime as rt  # 惰性：打破与 runtime 主模块的循环 import

    child = _child_state(st, sub, agent_key, text, seq)
    toolkit, _pruned = await rt._build_toolkit(child, run)
    agent = Agent(
        name=f"sre-{agent_key}",
        system_prompt=f"{sub['role']}\n{SUB_REPORT_DISCIPLINE}",
        model=await rt._build_model(child),
        toolkit=toolkit,
        state=AgentState(session_id=f"{run['framework_session_id']}:{agent_key}",
                         permission_context=rt._permission_context(child)),
    )
    await emit(child, run, "openops.subagent.started", action=agent_key,
               message=f"子 Agent「{sub.get('label', agent_key)}」开始执行",
               payload={"agent_key": agent_key, "task": text[:300]})
    chunks: list[str] = []
    inputs: Any = Msg(name="user", role="user", content=[TextBlock(type="text", text=text)])
    async for ev in agent.reply_stream(inputs):
        if isinstance(ev, ModelCallStartEvent):
            await emit(child, run, "openops.model.call.started", action="model_call",
                       message=f"[{agent_key}] 模型推理中（{ev.model_name}）",
                       payload={"model": ev.model_name, "agent_key": agent_key})
        elif isinstance(ev, ModelCallEndEvent):
            await emit(child, run, "openops.model.call.succeeded", action="model_call",
                       message=f"[{agent_key}] 模型推理完成",
                       payload={"input_tokens": ev.input_tokens, "output_tokens": ev.output_tokens,
                                "agent_key": agent_key})
        elif isinstance(ev, TextBlockDeltaEvent):
            chunks.append(ev.delta)  # 子 Agent 文本不进主对话流（汇报经工具返回给 main），只累积
        elif isinstance(ev, RequireUserConfirmEvent):
            # 防御：只读边界下不应出现；万一出现（标注热更新竞态）直接拒绝，不桥审批（D 块二期）
            log.warning("[OpenOps][subagent] %s 触发审批请求，按只读边界拒绝", agent_key)
            break
    report = "".join(chunks).strip() or "（子 Agent 未产出文本汇报；执行过程见活动栏）"
    await emit(child, run, "openops.subagent.reported", action=agent_key,
               message=f"子 Agent「{sub.get('label', agent_key)}」已汇报",
               payload={"agent_key": agent_key, "report_chars": len(report)})
    return report


async def dispatch(st: TaskState, run: dict[str, Any], tasks: list[dict[str, Any]]) -> str:
    """dispatch_subagents 工具主体：预算裁决 → 账本登记 → gather 并行 → 合并汇报。"""
    subs = {s["key"]: s for s in (st.sub_agents or [])}
    if not subs:
        return "当前模板未配置 sub agents，无法派发。"
    if not isinstance(tasks, list) or not tasks:
        return "派发清单为空：tasks 需为 [{role, task}] 数组。"
    tasks = tasks[:_MAX_BATCH]
    bad = sorted({str(t.get("role")) for t in tasks if t.get("role") not in subs})
    if bad:
        return f"未知角色 {bad}；可用角色：{sorted(subs)}。"

    cfg = st.dispatch_cfg or {}
    max_children = int(cfg.get("max_children", 3))
    max_spawns = int(cfg.get("delegation_max_spawns", 10))
    active = await delegations.count_active_for_leader(st.task_id)
    total = await delegations.count_total_for_leader(st.task_id)
    if active + len(tasks) > max_children:
        return (f"派发被预算拒绝：活跃子 Agent {active} + 本批 {len(tasks)} 超过 max_children={max_children}。"
                "等在途子 Agent 完成后再派。")
    if total + len(tasks) > max_spawns:
        return (f"派发被累计兜底拒绝：本任务已派发 {total} 次，上限 {max_spawns}。"
                "请基于已有结果收敛结论，不要继续重派。")

    entries: list[tuple[str, str, str, int]] = []
    for i, t in enumerate(tasks):
        role, text = str(t["role"]), str(t.get("task") or "")
        did = await delegations.create(st.run_id, st.task_id, role, text, SUB_TIMEOUT_S, st.user_id)
        entries.append((did, role, text, i))
        await emit(st, run, "openops.subagent.dispatched", action=role,
                   message=f"派发子 Agent「{subs[role].get('label', role)}」",
                   payload={"agent_key": role, "delegation_id": did, "task": text[:300]})

    async def _guarded(did: str, role: str, text: str, seq: int) -> tuple[str, str, str]:
        try:
            report = await asyncio.wait_for(_run_one(st, run, subs[role], role, text, seq),
                                            timeout=SUB_TIMEOUT_S)
            await delegations.mark_terminal(did, "completed", report, True, st.user_id)
            return role, "completed", report
        except asyncio.TimeoutError:
            await delegations.mark_terminal(did, "timeout", None, False, st.user_id)
            await emit(st, run, "openops.subagent.timeout", severity="warning", action=role,
                       message=f"子 Agent {role} 超时（{int(SUB_TIMEOUT_S)}s）",
                       payload={"agent_key": role, "delegation_id": did})
            return role, "timeout", f"（{role} 超时未完成汇报；其已执行的过程见活动栏，可据此部分推进）"
        except asyncio.CancelledError:  # 主任务取消 → 账本收口后级联
            await delegations.mark_terminal(did, "cancelled", None, False, st.user_id)
            raise
        except Exception as e:  # noqa: BLE001 —— 单个子失败不拖垮整批
            detail = f"{type(e).__name__}: {str(e)[:200]}"
            await delegations.mark_terminal(did, "failed_no_report", detail, False, st.user_id)
            await emit(st, run, "openops.subagent.failed", severity="warning", action=role,
                       message=f"子 Agent {role} 异常", reason_code="SUBAGENT_FAILED",
                       payload={"agent_key": role, "delegation_id": did, "error": detail})
            return role, "failed", f"（{role} 执行异常：{detail}）"

    results = await asyncio.gather(*(_guarded(*e) for e in entries))
    parts = [f"【{subs[r].get('label', r)}·{s}】\n{txt}" for r, s, txt in results]
    return "\n\n".join(parts)
