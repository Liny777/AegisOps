"""假设 checkpoint：诊断推进到假设（step>=3）后暂停，弹卡等用户补充假设或继续排查。

主 Agent 首次提交 step>=3 的 update_diagnosis_board 后，平台自动开一个 checkpoint：
emit `openops.diagnosis.checkpoint.opened` → 前端聊天流弹卡（添加假设 / 继续排查 / Ns 后
自动继续）→ 服务端阻塞等 decide 或超时（默认 10s）→ 决策文本拼进工具返回值回注模型。

设计要点（与审批桥 E1 的关系）：
- 复用同一套「asyncio.Event 三件套 + emit 事件管线」骨架（st.checkpoint_ev/result/id），
  但不共 `sre_approval_request` 表——审批行与工具调用强耦合（tool_call_name/reply_id），
  而 checkpoint 是 10s 级 UX 闸门，pending 态只活在内存：run 存活期间 get_state 从
  TaskState 投影恢复；进程重启则 run 已死，无需持久化。
- 只对主任务生效（st.leader_task_id 为 None）——子 Agent 连步骤都推不动（rca_board 降权），
  更不该替用户拍板；主 Agent 无执行超时预算（SUB_TIMEOUT_S 只管子任务），阻塞等待安全。
- 只弹一次（st.checkpoint_done）：用户补充假设后模型会重交 step=3，若再弹会自我死锁。
- hold 动作：10s 打不完一条假设——用户点「添加假设」时前端先发 hold 冻结倒计时，
  服务端把窗口延长到 CHECKPOINT_HOLD_S（默认 180s）并 emit checkpoint.extended。
- 超时/取消语义：超时=默认继续排查（emit closed, timed_out=true）；run 取消时
  CancelledError 原样上抛（绝不吞取消），清挂起态但不发 closed（task.cancelled 收尾）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from runtime.emit import emit
from runtime.task_registry import TaskState

log = logging.getLogger("openops.diagnosis_checkpoint")

# 首窗时长（秒）；0 = 关闭整个特性（测试 conftest 默认 0，防无关用例被动等 10s）
CHECKPOINT_TIMEOUT_S = float(os.environ.get("OPENOPS_DIAG_CHECKPOINT_TIMEOUT_S", "10"))
# hold（用户点「添加假设」开始输入）后的延长窗
CHECKPOINT_HOLD_S = float(os.environ.get("OPENOPS_DIAG_CHECKPOINT_HOLD_S", "180"))


def _deadline_iso(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _suffix_of(action: str, text: str, timed_out: bool) -> str:
    """决策 → 拼进 update_diagnosis_board 返回值的回注文本（模型下一轮必然读到）。"""
    if action == "add_hypothesis" and text:
        return (f"（用户补充了一条候选假设：「{text}」。请把它并入候选假设集合、重新评估置信度与排序，"
                f"重新调用 update_diagnosis_board(step=3) 提交更新后的假设排行，然后再进入验证（Step 4））")
    if timed_out:
        return "（已等待用户确认，超时未操作，默认继续排查：请按手册进入验证（Step 4））"
    return "（用户已确认继续排查：请按手册进入验证（Step 4））"


async def maybe_pause_for_user(st: TaskState, run: dict[str, Any], *,
                               step: int, step_completed: bool) -> str:
    """触发条件满足则弹卡并阻塞等决策；返回回注文本（未触发返回空串）。

    触发条件（全部满足）：特性开启（超时>0）、本次提交 step>=3 且非一步收尾
    （step=5+completed 的跳步收尾没有「补假设」的余地，且违反手册另有治理）、
    调用方是主任务、本 task 尚未弹过。
    """
    if CHECKPOINT_TIMEOUT_S <= 0:
        return ""
    if step < 3 or (step >= 5 and step_completed):
        return ""
    if st.leader_task_id:  # 子任务不弹（连步骤都推不动，见 rca_board 降权）
        return ""
    if st.checkpoint_done:
        return ""
    st.checkpoint_done = True
    return await _run_checkpoint(st, run, step=step)


async def _run_checkpoint(st: TaskState, run: dict[str, Any], *, step: int) -> str:
    checkpoint_id = str(uuid.uuid4())
    st.checkpoint_id = checkpoint_id
    st.checkpoint_result = None
    st.checkpoint_ev.clear()  # 等待前清位（同审批桥：防陈旧置位立即放行）
    st.checkpoint_deadline = _deadline_iso(CHECKPOINT_TIMEOUT_S)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + CHECKPOINT_TIMEOUT_S
    await emit(st, run, "openops.diagnosis.checkpoint.opened", action="checkpoint",
               message=f"假设已生成，等待用户确认：可补充新假设，{CHECKPOINT_TIMEOUT_S:.0f}秒内未操作将自动继续排查",
               payload={"checkpoint_id": checkpoint_id, "step": step,
                        "timeout_seconds": CHECKPOINT_TIMEOUT_S,
                        "deadline_at": st.checkpoint_deadline})

    action, text, timed_out = "continue", "", False
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                timed_out = st.checkpoint_result is None  # 决策与超时同刻竞争：有结果就认结果
                break
            try:
                await asyncio.wait_for(st.checkpoint_ev.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                timed_out = st.checkpoint_result is None
                break
            result = st.checkpoint_result or {}
            if result.get("action") == "hold":
                # 用户开始输入：延长窗口并复位握手，等真正的决策
                deadline = loop.time() + CHECKPOINT_HOLD_S
                st.checkpoint_deadline = _deadline_iso(CHECKPOINT_HOLD_S)
                st.checkpoint_ev.clear()
                st.checkpoint_result = None
                await emit(st, run, "openops.diagnosis.checkpoint.extended", action="checkpoint",
                           message=f"用户正在输入补充假设，等待窗口已延长至 {CHECKPOINT_HOLD_S:.0f} 秒",
                           payload={"checkpoint_id": checkpoint_id,
                                    "timeout_seconds": CHECKPOINT_HOLD_S,
                                    "deadline_at": st.checkpoint_deadline})
                continue
            break
    except asyncio.CancelledError:
        # run 取消：清挂起态后原样上抛（task.cancelled 收尾对话，不发 closed）
        st.checkpoint_id = None
        st.checkpoint_deadline = None
        raise

    result = st.checkpoint_result or {}
    if not timed_out:
        action = str(result.get("action") or "continue")
        text = str(result.get("text") or "")
    st.checkpoint_id = None
    st.checkpoint_deadline = None
    st.checkpoint_result = None

    message = ("等待超时，自动继续排查" if timed_out
               else "用户补充了假设，将并入候选重排" if action == "add_hypothesis"
               else "用户已确认继续排查")
    await emit(st, run, "openops.diagnosis.checkpoint.closed", action="checkpoint",
               decision=action, message=message,
               payload={"checkpoint_id": checkpoint_id, "action": action,
                        "timed_out": timed_out, "hypothesis_chars": len(text)})
    return _suffix_of(action, text, timed_out)
