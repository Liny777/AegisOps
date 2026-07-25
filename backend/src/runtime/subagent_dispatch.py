"""D/E 块：main→sub 同步批量派发（gather 模式，零 monkey-patch）。

架构拍板（对老 openOps-Dev D6 的取舍）：
- **同步批量 gather**：dispatch 工具内并行跑各子 Agent 的 reply_stream 循环，全部终态后合并汇报
  作为工具返回——放弃老项目 park/wakeup 异步（新项目无 app-server 消息总线）；主任务取消级联子。
- **可搬层照搬**：delegation 账本状态机 + 两层预算（活跃 max_children / 累计 max_spawns 含软删防重置）
  + 37 号汇报纪律 prompt。
- **E1 审批桥**：子 Agent 可绑需审批的写工具（用户业务形态：恢复工具绑恢复 Agent）——子循环与主循环
  同构（RequireUserConfirm → _handle_ask → UserConfirmResult 恢复），审批事件带子 task_id，
  decide 按 task_id 精确路由回子 approval_ev（task_registry._subtasks）。
- **超时与审批共存**：监控循环替代 wait_for 硬包——审批等待期（approval_id 已设且未决）不计入
  执行超时预算（老 D6「对等审批顺延 deadline」的同步版）。
- 子 Agent 禁二层派发（child.sub_agents 恒 None → 不注册 dispatch 工具）。
- **E4 治理**：子 Agent 按画像接 ReActConfig(max_iters)/ContextConfig(tool_result_limit)
  （agentscope 2.0.3 默认 20/50000；不变式 tool_result_limit < 模型窗口，经验 ≤1/3）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

import studio  # Agent Studio 垂直切片：只经 facade（src/studio/__init__.py），不碰其内部结构
from infra.redact import redact_text
from infra.repositories import delegations, runs as runs_repo
from runtime import task_registry
from runtime.emit import emit
from runtime.task_registry import TaskState

log = logging.getLogger("openops.subagent")

SUB_TIMEOUT_S = float(os.environ.get("OPENOPS_SUBAGENT_TIMEOUT_S", "300"))
_MAX_BATCH = 5  # 单次派发批大小硬上限（max_children 再往下收）
_POLL_S = 0.5  # 监控循环步长


def _sub_task_id(leader_task_id: str, agent_key: str, did: str) -> str:
    """子 task_id（DEF-2 修）：`{leader}.{key}-{delegation_id前8}` 全局唯一——旧 `{key}{seq}` 用批内
    下标，跨批同角色必碰撞（registry 覆盖写+第一批 finally 摘掉第二批+审批错路由）。"""
    return f"{leader_task_id}.{agent_key}-{did[:8]}"


# ── 子 Agent session_id：跨模块字符串契约（**改动前先读这段**）────────────────────
# 这个字符串是子 Agent 的 AgentState.session_id，会被 agentscope 当作
# `gen_ai.conversation_id` 发进 OTel span，最终落到 Agent Studio 的
# `sre_agent_studio_span.session_id` 列。Studio 靠**反解它**把 span 归组到对应的
# 派发账本行（主↔子交接内容）。
#
# ⚠ 这是 core 与 studio 切片之间唯一的非类型化契约，且**解析失败是静默的**
#   （studio 侧解析不出就返回 None，表现为「交接内容空着、其余一切正常」，
#    无日志无告警）。所以：
#   1) 生产与消费都必须走下面这对函数，不要再手写 f-string / split；
#   2) 改格式必然让 tests/studio/test_session_id_contract.py 变红——那是**设计好的**信号，
#      看到它红就说明 studio 侧也要同步改，别只改这里。
_SESSION_SEP = ":"


def sub_session_id(framework_session_id: str, agent_key: str, did: str) -> str:
    """子 Agent 的 AgentState.session_id（唯一生产方）。"""
    return f"{framework_session_id}{_SESSION_SEP}{agent_key}-{did[:8]}"


def parse_sub_session_id(session_id: str) -> tuple[str, str] | None:
    """反解 `sub_session_id` → (agent_key, delegation_id 前 8 位)；不是子 session 则 None。

    供 Agent Studio 把 span 归组到派发账本用。解析失败返回 None（不抛）——
    主 Agent 的 session_id 就是裸 framework_session_id，走这里必然返回 None，属正常。
    """
    try:
        _, suffix = session_id.split(_SESSION_SEP, 1)
        agent_key, did8 = suffix.rsplit("-", 1)
    except ValueError:
        return None
    if not agent_key or not did8:
        return None
    return agent_key, did8


def _child_state(st: TaskState, sub: dict[str, Any], agent_key: str, text: str, did: str, *,
                 dispatch_batch_id: str | None = None,
                 dispatch_batch_no: int | None = None) -> TaskState:
    """子 TaskState：继承 leader 的 scope/沙箱/模型，工具面按角色画像白名单裁剪（per-agent 隔离）。

    E1：白名单不再剔除需审批工具——恢复类可绑到恢复 Agent，标注的 ask 规则经
    _permission_context 自动生效，审批卡带子 task_id 弹到前端。
    """
    child = TaskState(task_id=_sub_task_id(st.task_id, agent_key, did), run_id=st.run_id, user_id=st.user_id,
                      instance_id=st.instance_id, input_text=text)
    child.agent_key = agent_key
    child.agent_label = str(sub.get("label") or agent_key)
    child.leader_task_id = st.task_id
    child.delegation_id = did
    child.dispatch_batch_id = dispatch_batch_id
    child.dispatch_batch_no = dispatch_batch_no
    child.activity_tool_labels = dict(st.activity_tool_labels or {})
    child.scope_ctx = st.scope_ctx
    child.sandbox_cfg = st.sandbox_cfg
    child.model_spec = st.model_spec
    child.selected_model = st.selected_model
    allowed_sk = set(sub.get("skills") or [])
    # 切片源=skills_pool 未过滤全集：main.skills 白名单只收窄 main 自己，不得掐子角色技能池
    # （老 D6 主从技能面独立；skills_pool 缺省回退 available_skills 兼容旧快照恢复路径）
    _pool = st.skills_pool if st.skills_pool is not None else (st.available_skills or {})
    child.available_skills = {k: v for k, v in _pool.items() if k in allowed_sk}
    # skill_hint 透传：/<skill> 的确定性引导原本只到 main，主 Agent 一转派就丢失。
    # 这里只搬运，命中与否由 _build_sub_system_prompt 按 child.available_skills 复核
    # （子技能面是 leader 子集，透传未装配的 hint 会引导子 Agent 调必被 fail-closed 的 skill）。
    child.skill_hint = st.skill_hint
    anns = st.tool_annotations or {}
    # mcp_servers **刻意不继承**（子恒 None）：用户自定义 MCP 只豁免 main 的模板白名单，子 Agent 工具面
    # 仍严格按画像 mcp_tools 裁剪（B7 角色隔离——否则每个子角色都能看到用户全部 MCP 工具）。
    # 与 skill 一致：filter_main_skills 的用户豁免也只对 main，此处按 allowed_sk 切片时无用户豁免。
    # 附带收益：子 Agent 不对用户 endpoint 发起工具发现（_build_toolkit 每次都会全量 sweep）。
    child.template_tools = set(sub.get("mcp_tools") or [])
    child.tool_annotations = {k: v for k, v in anns.items() if k in child.template_tools}
    return child


async def _emit_skill_gaps(child: TaskState, run: dict[str, Any], sub: dict[str, Any]) -> None:
    """画像里配了 Skill、切片后却拿不到 → 发 skill.skipped（info）让管理员在活动栏可见。

    对齐 MCP 侧的 tool.skipped，但**不设 tool_blocked**：「未装配」不是「被拦截」，不该压制结论。
    动机：模板对 sub_agents[].skills 只校验「是字符串数组」（不校验 key 存在），前端 ChipPicker
    渲染「目录 ∪ 已选」——失效 key 在编辑器里仍显示为已勾选，运行时静默切空、无任何信号。
    """
    wanted = [k for k in (sub.get("skills") or []) if isinstance(k, str)]
    if not wanted:
        return  # 画像本就没配 Skill：这是有意的角色隔离，不是异常
    missing = [k for k in wanted if k not in (child.available_skills or {})]
    if not missing:
        return
    await emit(child, run, "openops.skill.skipped", severity="info", action="skill_not_assembled",
               message=(f"子 Agent「{sub.get('label', child.agent_key)}」画像配置的 "
                        f"{len(missing)} 个 Skill 未装配到本实例：{'、'.join(missing)}。"
                        f"请在模板编辑器确认 skill_key 仍存在于当前 Skill 目录（已下架/改名的 key "
                        f"在编辑器里仍会显示为已勾选）。"),
               reason_code="SKILL_NOT_ASSEMBLED",
               # 缺失清单走 payload["keys"]：sanitize_activity_payload 对任意事件都保留 keys 列表
               # （见 infra/redact.py），换个键名就会被裁掉、审计里只剩 summary 文本
               payload={"keys": missing, "assembled": sorted(child.available_skills or {}),
                        "phase": "subagent_spawn"})


async def _run_one(st: TaskState, run: dict[str, Any], sub: dict[str, Any],
                   agent_key: str, text: str, did: str, dispatch_batch_id: str | None = None,
                   dispatch_batch_no: int | None = None) -> str:
    """跑一个子 Agent 完整循环（含审批握手，与主循环同构）；返回其汇报文本。"""
    from agentscope.agent import Agent
    from agentscope.event import (
        ConfirmResult,
        ModelCallEndEvent,
        ModelCallStartEvent,
        RequireUserConfirmEvent,
        TextBlockDeltaEvent,
        UserConfirmResultEvent,
    )
    from agentscope.message import Msg, TextBlock
    from agentscope.state import AgentState

    try:  # E4：治理 config（2.0.3 公开导出优先，私有路径兜底）
        from agentscope.agent import ContextConfig, ReActConfig
    except ImportError:  # pragma: no cover
        from agentscope.agent._config import ContextConfig, ReActConfig

    from runtime import agentscope_runtime as rt  # 惰性：打破与 runtime 主模块的循环 import

    child = _child_state(st, sub, agent_key, text, did,
                         dispatch_batch_id=dispatch_batch_id, dispatch_batch_no=dispatch_batch_no)
    task_registry.register_subtask(child)  # E1：decide_approval 按子 task_id 路由到 child.approval_ev
    # Agent Studio：子 span 归属（_run_one 在 create_task 私有 context 快照里跑，set 不泄漏回主）
    _studio_tok = studio.set_task_context(child.user_id, child.run_id, child.task_id, agent_key)
    try:
        await _emit_skill_gaps(child, run, sub)
        toolkit, _pruned = await rt._build_toolkit(child, run)
        agent = Agent(
            name=f"sre-{agent_key}",
            system_prompt=rt._build_sub_system_prompt(child, sub),
            model=await rt._build_model(child),
            toolkit=toolkit,
            state=AgentState(session_id=sub_session_id(str(run["framework_session_id"]), agent_key, did),
                             permission_context=rt._permission_context(child)),
            react_config=ReActConfig(max_iters=int(sub.get("max_iters", 20))),
            context_config=ContextConfig(tool_result_limit=int(sub.get("tool_result_limit", 24000))),
            middlewares=studio.agent_middlewares(),  # Agent Studio span 捕获（关闭/降级时 []）
        )
        await emit(child, run, "openops.subagent.started", action=agent_key,
                   message=f"子 Agent「{sub.get('label', agent_key)}」开始执行",
                   payload={"task_summary": redact_text(text, max_length=300)})
        chunks: list[str] = []
        inputs: Any = Msg(name="user", role="user", content=[TextBlock(type="text", text=text)])
        while True:
            require_ev = None
            async for ev in agent.reply_stream(inputs):
                if isinstance(ev, ModelCallStartEvent):
                    await emit(child, run, "openops.model.call.started", action="model_call",
                               message=f"[{agent_key}] 模型推理中（{ev.model_name}）",
                               payload={"model": ev.model_name})
                elif isinstance(ev, ModelCallEndEvent):
                    await emit(child, run, "openops.model.call.succeeded", action="model_call",
                               message=f"[{agent_key}] 模型推理完成",
                               payload={"input_tokens": ev.input_tokens, "output_tokens": ev.output_tokens})
                elif isinstance(ev, TextBlockDeltaEvent):
                    chunks.append(ev.delta)  # 子文本不进主对话流（汇报经工具返回给 main），只累积
                elif isinstance(ev, RequireUserConfirmEvent):
                    require_ev = ev
                    break  # E1：去做审批握手（审批卡带子 task_id 弹前端），再恢复
            if child.status != "running":
                raise asyncio.CancelledError()  # 外部取消（主任务取消级联/用户 cancel 子 task）
            if require_ev is None:
                break  # reply 正常结束
            decision = await rt._handle_ask(child, run, require_ev)
            if child.status != "running":
                raise asyncio.CancelledError()
            inputs = UserConfirmResultEvent(
                reply_id=require_ev.reply_id,
                confirm_results=[ConfirmResult(confirmed=decision == "approved", tool_call=tc)
                                 for tc in require_ev.tool_calls],
            )
        report = "".join(chunks).strip() or "（子 Agent 未产出文本汇报；执行过程见活动栏）"
        if child.tool_blocked:
            # B6-RT-001 对子 Agent 补齐：拦截标记原本只在主循环消费（run_task 的 st.tool_blocked
            # 分支），子 Agent 的 Skill/工具被拒后照样能汇报「已完成」、主 Agent 采信 → 假闭环。
            # 不改写模型原文，只前置声明，让主 Agent 判断时看得到。
            report = ("【注意】本子任务执行过程中有工具或 Skill 被运行时拦截（未装配/标注变更/未获批），"
                      "以下结论不完整，不得据此判定已闭环。\n" + report)
        await emit(child, run, "openops.subagent.reported", action=agent_key,
                   message=f"子 Agent「{sub.get('label', agent_key)}」已汇报",
                   payload={"report_chars": len(report),
                            "report_summary": redact_text(report, max_length=300)})
        return report
    finally:
        studio.reset_task_context(_studio_tok)
        task_registry.unregister_subtask(child.task_id)
        try:  # DEF-1 级联收口：子任务任何终止路径都把自己的 pending 审批置 cancelled——
            # 否则遗留 pending 卡片的迟到 decide 无人认领（旧 fallback 时代会污染主任务握手）
            await runs_repo.cancel_pending_approvals(child.task_id, st.user_id)
        except Exception:  # noqa: BLE001 —— 收口失败不改变子任务结果
            log.warning("[OpenOps][subagent] 子任务 %s pending 审批收口失败", child.task_id)


async def _run_with_budget(st: TaskState, run: dict[str, Any], sub: dict[str, Any],
                           agent_key: str, text: str, did: str,
                           dispatch_batch_id: str | None = None,
                           dispatch_batch_no: int | None = None) -> str:
    """监控式超时（E1）：审批等待期不计入执行预算——wait_for 硬包会把等人批准的时间算成超时。"""
    child_probe: dict[str, TaskState] = {}

    async def _wrapped() -> str:
        # _run_one 内部创建 child；此处经 registry 探测其审批等待态（task_id 可预知）
        return await _run_one(st, run, sub, agent_key, text, did,
                              dispatch_batch_id, dispatch_batch_no)

    child_task_id = _sub_task_id(st.task_id, agent_key, did)
    runner = asyncio.create_task(_wrapped())
    spent = 0.0
    try:
        while True:
            done, _ = await asyncio.wait({runner}, timeout=_POLL_S)
            if done:
                return runner.result()
            child = child_probe.get("c") or task_registry.get_by_task(child_task_id)
            if child is not None:
                child_probe["c"] = child
            waiting_approval = bool(child and child.approval_id and child.approval_result is None)
            if not waiting_approval:
                spent += _POLL_S
            if spent >= SUB_TIMEOUT_S:
                runner.cancel()
                try:
                    await runner
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                raise asyncio.TimeoutError()
    except asyncio.CancelledError:
        runner.cancel()
        raise


async def dispatch(st: TaskState, run: dict[str, Any], tasks: list[dict[str, Any]]) -> str:
    """dispatch_subagents 工具主体：预算裁决 → 账本登记 → gather 并行 → 合并汇报。"""
    subs = {s["key"]: s for s in (st.sub_agents or [])}
    if not subs:
        return "当前模板未配置 sub agents，无法派发。"
    if not isinstance(tasks, list) or not tasks:
        return "派发清单为空：tasks 需为 [{role, task}] 数组。"
    if len(tasks) > _MAX_BATCH:  # DEF-8：超限直接拒绝（旧行为静默截断，第 6 个任务无声丢弃）
        return f"单批最多 {_MAX_BATCH} 个子任务（本次提交 {len(tasks)} 个）；请拆成多批派发。"
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

    dispatch_batch_id = str(uuid.uuid4())
    dispatch_batch_no = await delegations.next_batch_no(st.task_id)
    entries: list[tuple[str, str, str]] = []
    for t in tasks:
        role, text = str(t["role"]), str(t.get("task") or "")
        did = await delegations.create(
            st.run_id, st.task_id, role, text, SUB_TIMEOUT_S, st.user_id,
            dispatch_batch_id=dispatch_batch_id, dispatch_batch_no=dispatch_batch_no,
        )
        entries.append((did, role, text))
        label = str(subs[role].get("label") or role)
        await emit(st, run, "openops.subagent.dispatched", action=role,
                   message=f"派发子 Agent「{label}」",
                   payload={"agent_key": role, "agent_label": label,
                            "leader_task_id": st.task_id,
                            "child_task_id": _sub_task_id(st.task_id, role, did),
                            "delegation_id": did,
                            "dispatch_batch_id": dispatch_batch_id,
                            "dispatch_batch_no": dispatch_batch_no,
                            "task_summary": redact_text(text, max_length=300)})

    def _terminal_payload(did: str, role: str) -> dict[str, Any]:
        return {"agent_key": role,
                "agent_label": str(subs[role].get("label") or role),
                "leader_task_id": st.task_id,
                "child_task_id": _sub_task_id(st.task_id, role, did),
                "delegation_id": did,
                "dispatch_batch_id": dispatch_batch_id,
                "dispatch_batch_no": dispatch_batch_no}

    async def _guarded(did: str, role: str, text: str) -> tuple[str, str, str]:
        try:
            report = await _run_with_budget(st, run, subs[role], role, text, did,
                                            dispatch_batch_id, dispatch_batch_no)
            await delegations.mark_terminal(did, "completed", report, True, st.user_id)
            return role, "completed", report
        except asyncio.TimeoutError:
            await delegations.mark_terminal(did, "timeout", None, False, st.user_id)
            await emit(st, run, "openops.subagent.timeout", severity="warning", action=role,
                       message=f"子 Agent {role} 超时（{int(SUB_TIMEOUT_S)}s 执行预算，审批等待不计入）",
                       payload=_terminal_payload(did, role))
            return role, "timeout", f"（{role} 超时未完成汇报；其已执行的过程见活动栏，可据此部分推进）"
        except asyncio.CancelledError:  # 主任务取消 → 账本收口后级联
            await delegations.mark_terminal(did, "cancelled", None, False, st.user_id)
            await emit(st, run, "openops.subagent.cancelled", severity="warning", action=role,
                       message=f"子 Agent {role} 已取消", payload=_terminal_payload(did, role))
            raise
        except Exception as e:  # noqa: BLE001 —— 单个子失败不拖垮整批
            detail = f"{type(e).__name__}: {redact_text(e, max_length=200)}"
            await delegations.mark_terminal(did, "failed_no_report", detail, False, st.user_id)
            await emit(st, run, "openops.subagent.failed", severity="warning", action=role,
                       message=f"子 Agent {role} 异常", reason_code="SUBAGENT_FAILED",
                       payload={**_terminal_payload(did, role), "error": detail})
            return role, "failed", f"（{role} 执行异常：{detail}）"

    results = await asyncio.gather(*(_guarded(*e) for e in entries))
    parts = [f"【{subs[r].get('label', r)}·{s}】\n{txt}" for r, s, txt in results]
    return "\n\n".join(parts)
