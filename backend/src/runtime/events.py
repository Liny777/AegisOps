"""进程内事件总线：per-run 序号 + 环形缓冲 + SSE 订阅（30.4 envelope）。

- 事实源仍是 audit_event（orchestrator 先写审计再 publish）；本总线只服务实时体验。
- Last-Event-ID 补发：缓冲内按 seq 补；缓冲丢失（重启）→ 发 resync 事件让前端拉 /state。
"""
from __future__ import annotations

import asyncio
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from infra.redact import redact_text

_BUFFER_SIZE = 500


class RunChannel:
    def __init__(self) -> None:
        self.seq = 0
        self.buffer: deque[dict[str, Any]] = deque(maxlen=_BUFFER_SIZE)
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()


_channels: dict[str, RunChannel] = {}


def _chan(run_id: str) -> RunChannel:
    if run_id not in _channels:
        _channels[run_id] = RunChannel()
    return _channels[run_id]


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


def publish(run_id: str, event: dict[str, Any]) -> dict[str, Any]:
    ch = _chan(run_id)
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
    if last_event_id is not None and last_event_id < ch.seq:
        buffered = [e for e in ch.buffer if e["sequence"] > last_event_id]
        # 缓冲被挤掉（最早 seq > last+1）→ 无法无缝补发
        if buffered and buffered[0]["sequence"] != last_event_id + 1:
            need_resync = True
        replay = buffered
    return q, replay, need_resync


def unsubscribe(run_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
    ch = _channels.get(run_id)
    if ch:
        ch.subscribers.discard(q)


def snapshot(run_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """缓冲内事件快照（/ag-ui 批量端点用）。"""
    ch = _channels.get(run_id)
    return list(ch.buffer)[-limit:] if ch else []


def reset() -> None:  # 测试用
    _channels.clear()
