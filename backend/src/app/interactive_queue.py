"""交互任务排队（内存 FIFO）：并发名额满时不再 429，改为排队 + 位次提示。

**为什么是内存队列，而告警侧是 DB 队列**——这是刻意的不对称，别"统一"掉：
交互任务是**用户正在等**，进程重启后那个人多半已经离开，恢复一个没人等的任务只会
制造幽灵任务（占名额、发 SSE 给不存在的听众）。所以重启即丢弃，前端靠排队超时兜底。
告警任务无人等待、延迟几分钟无所谓，才需要 `sre_alert_incident` 那种持久化队列。

生命周期：
    start_task 名额满 → enqueue()（返回位次，前端显示"前面还有 N 个"）
    任一任务协程结束 → runtime_adapter 的 done_callback → drain() → 队首重新走 start_task
    用户等不及 → cancel()（run_state_service.cancel_task 的第一分支）
    超时未轮到 → 每条自带的定时器 → 推 task.failed(queue_timeout)

装配（scope resolve / 容器 ensure / 模板编译）**不在入队时做**：排队期间零资源占用，
且排队期间管理员改配置能生效——drain 时重新走一遍 start_task 前置检查（run 可能已被
关闭/删除，此时按正常错误路径失败，而不是启动一个僵尸任务）。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from runtime import events

log = logging.getLogger("openops.queue")

DEFAULT_MAX_SIZE = 20
DEFAULT_TIMEOUT_S = 300


@dataclass
class _Entry:
    user: dict[str, Any]
    run_id: str
    task_id: str
    req: Any
    trace: str                       # run 的 audit_trace_id（推 SSE 用）
    timer: asyncio.Task[None] | None = field(default=None, repr=False)


_queue: list[_Entry] = []
_draining = False
_drain_requested = False   # drain 运行期间又有名额释放 → 收工前再跑一轮（防丢唤醒）


# ============================ 对外 API ============================


def enabled(cfg: dict[str, Any]) -> bool:
    """灰度开关：关闭时 start_task 退回旧的 429 行为（应急回滚用）。"""
    return bool(cfg.get("interactive_queue_enabled", True))


def enqueue(user: dict[str, Any], run_id: str, task_id: str, req: Any, trace: str,
            *, max_size: int, timeout_s: int) -> int | None:
    """入队并返回 1-based 位次；队列已满返回 None（调用方抛 429）。"""
    if len(_queue) >= max_size:
        return None
    entry = _Entry(user=user, run_id=run_id, task_id=task_id, req=req, trace=trace)
    _queue.append(entry)
    entry.timer = asyncio.create_task(_expire_after(entry, timeout_s))
    position = len(_queue)
    events.publish(run_id, events.envelope(
        run_id, "openops.task.queued", task_id=task_id,
        message=f"正在排队，前面还有 {position - 1} 个",
        payload={"queue_position": position}, audit_trace_id=trace))
    log.info("[queue] 入队 task=%s run=%s 位次=%d 深度=%d", task_id, run_id, position, len(_queue))
    return position


def cancel(task_id: str) -> bool:
    """取消排队中的任务（用户点「取消排队」）；不在队列里返回 False 让调用方走原路径。"""
    for i, e in enumerate(_queue):
        if e.task_id == task_id:
            _drop(i)
            log.info("[queue] 排队中取消 task=%s 剩余深度=%d", task_id, len(_queue))
            _broadcast_positions()
            return True
    return False


def position(task_id: str) -> int | None:
    for i, e in enumerate(_queue):
        if e.task_id == task_id:
            return i + 1
    return None


def depth() -> dict[str, Any]:
    """管理台观测：对话侧排队水位（补「对话拥堵无观测入口」的缺口）。"""
    by_user: dict[str, int] = {}
    for e in _queue:
        uid = str(e.user.get("user_id", ""))
        by_user[uid] = by_user.get(uid, 0) + 1
    return {"total": len(_queue), "by_user": by_user}


def drain() -> None:
    """有名额了 → 唤醒队列。fire-and-forget：调用方（done_callback）不等待。

    放几个由**真实名额**决定，不是"一次一个"——见 _drain_once。已有 drain 在跑时只置
    脏标记，由它收工前再跑一轮：否则两个任务同时结束会丢掉一次唤醒（第二次 drain 撞上
    `_draining=True` 直接返回，那个空出来的名额就没人认领了）。
    """
    global _drain_requested
    if not _queue:
        return
    _drain_requested = True
    if _draining:
        return
    asyncio.create_task(_drain_once())


def reset() -> None:
    """测试隔离：清空队列并取消所有定时器。"""
    global _draining, _drain_requested
    for e in _queue:
        if e.timer and not e.timer.done():
            e.timer.cancel()
    _queue.clear()
    _draining = False
    _drain_requested = False


def shutdown() -> None:
    """关停收口：丢弃全部排队任务。

    **必须在取消 runtime 任务之前调用**——否则每个任务的 done_callback 都会触发 drain()，
    对着正在关闭的连接池启动新任务，刷出一片 `pool is already closed`（同 B5-BE-001 的
    PoolClosed 噪声）。丢弃是设计内行为：排队任务没有 TaskState、没有落库，重启后那个
    用户多半已经离开（见模块头）。前端侧靠排队超时兜底。
    """
    if _queue:
        log.info("[queue] 关停丢弃 %d 个排队任务：%s",
                 len(_queue), ", ".join(e.task_id for e in _queue))
    reset()


# ============================ 内部 ============================


def _drop(index: int) -> _Entry:
    entry = _queue.pop(index)
    if entry.timer and not entry.timer.done():
        entry.timer.cancel()
    return entry


def _broadcast_positions() -> None:
    """位次变化后推给仍在排队的每个 run（复用其自身 SSE 通道，前端零新增订阅）。"""
    for i, e in enumerate(_queue):
        events.publish(e.run_id, events.envelope(
            e.run_id, "openops.task.queue_position", task_id=e.task_id,
            message=f"正在排队，前面还有 {i} 个",
            payload={"queue_position": i + 1}, audit_trace_id=e.trace))


async def _drain_once() -> None:
    """按**真实名额**逐个放行队首，直到队首没名额或队列空。

    关键是每次出队前都问一次 `has_interactive_slot`——drain() 只说明"有任务结束了"，
    不说明队列里所有人都能开跑。少了这道复核，第一次 drain 就会把整队放空，并发闸失效。
    启动失败不消耗名额，循环继续放下一个。
    """
    global _draining, _drain_requested
    if _draining:
        return
    _draining = True
    from app import run_state_service  # 局部导入避免与 run_state_service 成环

    try:
        while True:
            _drain_requested = False
            while _queue:
                head = _queue[0]
                uid = str(head.user.get("user_id", ""))
                try:
                    if not await run_state_service.has_interactive_slot(uid):
                        break                     # 队首无名额 → 等下一次 drain
                except Exception as exc:          # noqa: BLE001 —— 读配置失败：本轮先不放，别误放空
                    log.warning("[queue] 名额复核失败，本轮暂停出队：%s", exc)
                    break
                entry = _drop(0)
                _broadcast_positions()            # 其余人位次各减一
                try:
                    # 重走 start_task：前置检查（run 是否已关闭/删除）全部重新校验；
                    # _queued_task_id 让它跳过名额检查（已由上面复核）并复用原 task_id。
                    await run_state_service.start_task(
                        entry.user, entry.run_id, entry.req, _queued_task_id=entry.task_id)
                except Exception as exc:  # noqa: BLE001 —— run 已关闭/范围失败等：告知前端，不吞
                    log.warning("[queue] 出队启动失败 task=%s: %s", entry.task_id, exc)
                    events.publish(entry.run_id, events.envelope(
                        entry.run_id, "openops.task.failed", task_id=entry.task_id,
                        message=f"排队结束但启动失败：{getattr(exc, 'message', str(exc))}",
                        reason_code=getattr(exc, "code", "QUEUE_START_FAILED"), severity="warning",
                        audit_trace_id=entry.trace))
            if not _drain_requested:              # 本轮期间没有新唤醒 → 收工
                break
    finally:
        _draining = False


async def _expire_after(entry: _Entry, timeout_s: int) -> None:
    try:
        await asyncio.sleep(timeout_s)
    except asyncio.CancelledError:
        return
    for i, e in enumerate(_queue):
        if e.task_id == entry.task_id:
            _queue.pop(i)  # 不用 _drop：自己就是那个 timer，不能 cancel 自己
            log.info("[queue] 排队超时 task=%s（等待 %ds）", entry.task_id, timeout_s)
            events.publish(entry.run_id, events.envelope(
                entry.run_id, "openops.task.failed", task_id=entry.task_id,
                message=f"排队超时（等待超过 {timeout_s // 60} 分钟），请稍后重试",
                reason_code="QUEUE_TIMEOUT", severity="warning", audit_trace_id=entry.trace))
            _broadcast_positions()
            return
