"""进程内事件总线：per-run 序号 + 环形缓冲 + SSE 订阅（30.4 envelope）。

- 事实源仍是 audit_event（orchestrator 先写审计再 publish）；本总线只服务实时体验。
- Last-Event-ID 补发：缓冲内按 seq 补；缓冲丢失（重启）→ 发 resync 事件让前端拉 /state。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from infra.redact import redact_text

_BUFFER_SIZE = 500
_CHANNEL_IDLE_TTL_SECONDS = 30 * 60
_MAX_CHANNELS = 1024


class RunChannel:
    def __init__(self) -> None:
        self.seq = 0
        self.buffer: deque[dict[str, Any]] = deque(maxlen=_BUFFER_SIZE)
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.last_touched = time.monotonic()


_channels: dict[str, RunChannel] = {}


def _prune_channels(*, now: float | None = None, reserve: int = 0) -> None:
    """回收无订阅的旧 channel；活跃 SSE/AG-UI 订阅永不被容量策略驱逐。"""
    current = time.monotonic() if now is None else now
    expired = [
        run_id
        for run_id, channel in _channels.items()
        if not channel.subscribers
        and current - channel.last_touched >= _CHANNEL_IDLE_TTL_SECONDS
    ]
    for run_id in expired:
        _channels.pop(run_id, None)

    target = max(0, _MAX_CHANNELS - reserve)
    overflow = len(_channels) - target
    if overflow <= 0:
        return
    idle = sorted(
        (
            (channel.last_touched, run_id)
            for run_id, channel in _channels.items()
            if not channel.subscribers
        ),
    )
    for _last_touched, run_id in idle[:overflow]:
        _channels.pop(run_id, None)


def _chan(run_id: str) -> RunChannel:
    channel = _channels.get(run_id)
    if channel is None:
        # 先为新 channel 腾位置；若现有 channel 全部有订阅，则允许临时超过上限，
        # 也不能为了硬上限破坏正在运行的 SSE/AG-UI 流。
        _prune_channels(reserve=1)
        channel = RunChannel()
        _channels[run_id] = channel
    channel.last_touched = time.monotonic()
    return channel


def envelope(
    run_id: str,
    event_type: str,
    *,
    task_id: str | None = None,
    message: str = "",
    reason_code: str | None = None,
    severity: str = "info",
    payload: dict[str, Any] | None = None,
    audit_trace_id: str | None = None,
    event_id: str | None = None,
    external_request_id: str | None = None,
) -> dict[str, Any]:
    """30.4 统一 envelope（sequence 由 publish 填）。禁入敏感字段由调用方保证。"""
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "agent_run_id": run_id,
        "task_id": task_id,
        "sequence": None,
        "severity": severity,
        "user_visible": True,
        "message": redact_text(message, max_length=500),
        "reason_code": reason_code,
        "audit_trace_id": audit_trace_id,
        "external_request_id": external_request_id,
        "payload_redacted_json": payload or {},
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }


# 高频瞬态流（增量非事实）：只广播给在线订阅者，不占 sequence、不进环形缓冲——
# 千级 delta 不得把 tool.call/rca.updated 挤出缓冲导致 Last-Event-ID 恒 resync。
_TRANSIENT_EVENT_TYPES = frozenset({
    "openops.assistant.delta",
    "openops.assistant.thinking.delta",
})


def publish(run_id: str, event: dict[str, Any]) -> dict[str, Any]:
    ch = _chan(run_id)
    if event.get("event_type") in _TRANSIENT_EVENT_TYPES:
        for q in list(ch.subscribers):
            q.put_nowait(event)
        return event
    ch.seq += 1
    event["sequence"] = ch.seq
    ch.buffer.append(event)
    for q in list(ch.subscribers):
        q.put_nowait(event)
    return event


def subscribe(run_id: str, last_event_id: int | None) -> tuple[asyncio.Queue[dict[str, Any]], list[dict[str, Any]], bool]:
    """返回 (队列, 补发列表, need_resync)。"""
    ch = _chan(run_id)
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    ch.subscribers.add(q)
    replay: list[dict[str, Any]] = []
    need_resync = False
    if last_event_id is not None:
        if last_event_id > ch.seq:
            # channel 因进程重启或空闲回收而重建，序号已无法与客户端游标衔接。
            need_resync = True
        elif last_event_id < ch.seq:
            buffered = [e for e in ch.buffer if e["sequence"] > last_event_id]
            # 缓冲被挤掉（最早 seq > last+1）→ 无法无缝补发
            if not buffered or buffered[0]["sequence"] != last_event_id + 1:
                need_resync = True
            replay = buffered
    return q, replay, need_resync


def unsubscribe(run_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
    ch = _channels.get(run_id)
    if ch:
        ch.subscribers.discard(q)
        ch.last_touched = time.monotonic()
    _prune_channels()


def snapshot(run_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """缓冲内事件快照（/ag-ui 批量端点用）。"""
    ch = _channels.get(run_id)
    if ch:
        ch.last_touched = time.monotonic()
    return list(ch.buffer)[-limit:] if ch else []


def reset() -> None:  # 测试用
    _channels.clear()
