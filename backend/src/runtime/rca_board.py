"""诊断面板（openops.rca.updated）的服务端状态机（A3）。

模型经 update_diagnosis_board 自报进度，本模块负责「不信任模型」的那一半：
- owner=主任务：子 Agent 的更新合并进 leader 的 `st.rca` → get_state 恢复与快照零改动生效；
- 子任务只并内容：步骤推进、step_completed、conclusion、step_summary 只认 owner 主任务的提交——
  面板是 run 级单例，被派发的子 Agent 自认为「我这块查完了」就报 step=5 会把整块面板一步打成
  「诊断完成」（内网现象：走到第2步证据就直接出根因报告）；
- revision 服务端权威自增（契约拒收模型提交的 revision）；且以 owner.rca 现值为基——
  demo 剧本先跑过时内容整体丢弃、revision 仍接续，保证前端「同 task revision 单调」守卫不掉更新；
- 步骤单调钳制（宽松）：只前进不回退，回退不报错（省 ReAct 迭代预算），返回文本注明；允许跳步；
- 字段级 merge：骨架默认 ∪ prev ∪ 本次增量（提交的列表整体替换、未提交保留）；
- steps/phaseLabel/status 每次统一重派生（step=5+completed → concluded）。

合并读改写全程无 await（asyncio 单线程原子）；事件用**调用方** st 发（活动栏归组到子 Agent
角色），payload 用 owner.rca；子 Agent 路径显式补主任务快照（emit 的主任务守卫不动）。
"""
from __future__ import annotations

import logging
from typing import Any

from infra.rca_contract import (
    RcaBoardContractError,
    board_result_summary,
    derive_phase_label,
    derive_steps,
    normalize_board_arguments,
)
from infra.repositories import task_states
from runtime import task_registry
from runtime.emit import emit
from runtime.task_registry import TaskState

log = logging.getLogger("openops.rca_board")

# 面板骨架：前端 RcaCardData 必填字段全部就位、内容为空（模型漏提交也不缺键）
_SKELETON: dict[str, Any] = {
    "title": "", "currentQ": "", "why": "", "conclusion": "",
    "tiles": [], "facts": [], "unknowns": [], "sources": [], "hypotheses": [], "actions": [],
}
_MERGE_KEYS = tuple(_SKELETON)


def board_owner(st: TaskState) -> TaskState:
    """面板归属主任务：子 Agent 按 leader_task_id 找 owner；leader 已注销时退回自身（防御，不阻断）。"""
    if st.leader_task_id:
        owner = task_registry.get_by_task(st.leader_task_id)
        if owner is not None:
            return owner
        log.warning("[OpenOps][rca-board] leader %s 不在 registry，面板落到子 task %s",
                    st.leader_task_id, st.task_id)
    return st


def reopen_with_conclusion(panel: dict[str, Any], conclusion: str) -> dict[str, Any]:
    """安全事实覆盖结论并把面板拉回「进行中」（恢复未执行/被拦截 ≠ 诊断闭环）。

    只改 status 会留下「五步全 done + in_progress」的矛盾形状——steps/phaseLabel 必须与
    status 一起重派生（已收尾的面板回到「第5步·结论」进行中）；revision 自增，避免与末次
    面板更新同号被前端幂等去重丢弃。步骤小结显式回传 derive_steps，防 reopen 后 steps 丢 summary。
    """
    step, completed = _progress_of(panel.get("steps"))
    if completed:
        step, completed = 5, False
    summaries = panel.get("step_summaries") if isinstance(panel.get("step_summaries"), dict) else None
    if not summaries:
        # demo 面板（rca_demo 产）没有 step_summaries 载体、小结只内嵌在 steps[].summary——
        # 从现有 steps 反推，防 reopen 一次审批拒绝就把五步小结整体抹掉
        summaries = {str(item["num"]): item["summary"] for item in panel.get("steps") or []
                     if isinstance(item, dict) and item.get("num") and item.get("summary")}
    return {**panel, "conclusion": conclusion, "status": "in_progress",
            "steps": derive_steps(step, completed, summaries),
            "phaseLabel": derive_phase_label(step, completed),
            "revision": int(panel.get("revision") or 0) + 1}


def _progress_of(steps: Any) -> tuple[int, bool]:
    """由 derive_steps 生成的 steps 反推 (step, completed)——不额外存内部键，快照/事件零污染。"""
    if not isinstance(steps, list):
        return 1, False
    for item in steps:
        if isinstance(item, dict) and item.get("state") == "active":
            return int(item.get("num") or 1), False
    done = [int(item.get("num") or 0) for item in steps
            if isinstance(item, dict) and item.get("state") == "done"]
    return (max(done), True) if done else (1, False)


async def apply_board_update(st: TaskState, run: dict[str, Any], raw: dict[str, Any]) -> str:
    """校验 → 合并 → 派生 → emit rca.updated；返回给模型的短结果文本。

    契约错误（RcaBoardContractError）原样抛出，由工具层翻译成中文纠正文本回给模型自纠。
    """
    args = normalize_board_arguments(raw)
    owner = board_owner(st)

    # 内容底座只认模型面板；demo 剧本一经模型接管即整体丢弃、从骨架起步（真流程不得掺假数据）
    prev = owner.rca if (owner.rca_source == "model" and isinstance(owner.rca, dict)) else None

    # 子任务降权：被派发的 Agent 只能提交内容，步骤推进 / step_completed / conclusion / step_summary
    # 一律由 owner 主任务提交。身份判定用 leader_task_id 而非 `st is not owner`——leader 已注销时
    # board_owner 退回自身（防御分支），那条路径下调用方仍是子任务，同样不得伪造闭环。
    # step_summary 一并降权：它是「主 Agent 对该步的总结」，放行会让报 step=5 的子 Agent 把小结提前
    # 种在第5步上，等面板真推进到 5 时突然冒出来（正是「未开始的步已有产出」那类矛盾）。
    demoted: list[str] = []
    if st.leader_task_id:
        child_step, child_completed = _progress_of(prev.get("steps")) if prev else (1, False)
        if (args["step"], args["step_completed"]) > (child_step, child_completed):
            demoted.append("步骤推进")
        if args.pop("conclusion", None):
            demoted.append("conclusion")
        if args.pop("step_summary", None):
            demoted.append("step_summary")
        args["step"], args["step_completed"] = child_step, child_completed

    merged: dict[str, Any] = dict(_SKELETON)
    if prev:
        merged.update({k: prev[k] for k in _MERGE_KEYS if k in prev})
    for key in _MERGE_KEYS:
        if key in args:
            merged[key] = args[key]

    # 步骤小结逐键累积（键=提交步号 str）：不进 _MERGE_KEYS——列表整体替换语义不适用，历史步
    # 小结要一直保留；demo 丢弃分支天然清零（prev=None 即从空表起步）。钳制回退时小结仍按
    # 提交的步号落位（模型补交历史步小结合理，不能被钳到当前步上）
    summaries: dict[str, str] = dict(prev.get("step_summaries") or {}) if prev else {}
    if "step_summary" in args:
        summaries[str(args["step"])] = args["step_summary"]
    merged["step_summaries"] = summaries

    # 步骤单调钳制（宽松）：(step, completed) 只前进不回退；允许跳步（证据充分快进合理）
    step, completed = args["step"], args["step_completed"]
    clamped = False
    if prev is not None:
        prev_step, prev_completed = _progress_of(prev.get("steps"))
        if (step, completed) < (prev_step, prev_completed):
            step, completed = prev_step, prev_completed
            clamped = True

    concluded = step >= 5 and completed
    if concluded and not merged["conclusion"]:
        # step=5 收尾必须带诊断结论——缺失时给模型明确的补交指引（不落任何状态变更）
        raise RcaBoardContractError(
            "step=5 且 step_completed=true 表示诊断结束，必须提供 conclusion"
            "（影响边界、最可能根因方向、建议下一步）")

    # revision 基数取 owner 与调用方两处现值的 max（含 demo）：demo 剧本可能把面板写在
    # 调用方子 Agent 自己的 st 上（rev 1/2 已随子 task_id 发出过事件），只看 owner 会撞号
    # 回退，前端同段单调守卫会把模型面板的前几次更新静默丢弃
    prev_revision = max(
        int(owner.rca.get("revision") or 0) if isinstance(owner.rca, dict) else 0,
        int(st.rca.get("revision") or 0) if isinstance(st.rca, dict) else 0,
    )
    merged["revision"] = prev_revision + 1
    # 面板分段身份=owner 主任务：事件 envelope 的 task_id 是调用方（活动栏归组用），
    # 子 Agent 与主任务兜底交替发事件时前端不能拿 envelope task_id 当 revision 分段键，
    # 否则跨归属切换被误判「新任务」重置守卫、旧 revision 重新落地闪回
    merged["board_task_id"] = owner.task_id
    merged["steps"] = derive_steps(step, completed, summaries)
    merged["phaseLabel"] = derive_phase_label(step, completed)
    merged["status"] = "concluded" if concluded else "in_progress"

    owner.rca = merged
    owner.rca_source = "model"

    message = (f"诊断完成：{merged['title'] or '诊断结论已提交'}" if concluded
               else f"诊断进度：{merged['phaseLabel']}" + (f" · {merged['title']}" if merged["title"] else ""))
    # 事件用调用方 st 发（活动栏归组到子 Agent 角色），payload 用 owner.rca（run 级单例面板）
    await emit(st, run, "openops.rca.updated", message=message, payload=owner.rca)
    # 子 Agent 路径：emit 只给主任务落 sre_task_state（leader 守卫），这里显式补 owner 快照
    if st is not owner and owner.leader_task_id is None:
        try:
            await task_states.upsert_snapshot(owner, owner.status, str(run["audit_trace_id"]))
        except Exception:  # noqa: BLE001 —— 旧库未迁移等，快照降级不阻断（同 emit 口径）
            log.warning("[OpenOps][rca-board] 主任务快照写入失败 task=%s", owner.task_id)

    text = board_result_summary(merged)
    if clamped:
        text += f"（注意：步骤只能前进不能回退，本次提交的 step={args['step']} 未生效，面板保持{merged['phaseLabel']}）"
    if demoted:
        text += (f"（注意：子任务只能提交内容，本次的 {'、'.join(demoted)} 未生效——"
                 f"步骤推进与诊断收尾由主任务负责，面板保持{merged['phaseLabel']}）")
    return text
