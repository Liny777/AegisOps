"""Event Stream：SSE 生成器（Last-Event-ID 补发 / 心跳 / resync 信号）。"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from runtime import events

HEARTBEAT_SECONDS = 15


def _sse(event: dict[str, Any]) -> str:
    return f"id: {event['sequence']}\nevent: openops\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


async def stream(run_id: str, last_event_id: int | None) -> AsyncIterator[str]:
    q, replay, need_resync = events.subscribe(run_id, last_event_id)
    try:
        # Flush response headers immediately through reverse proxies. Without an
        # initial chunk, an idle Run stays "connecting" until the 15s heartbeat
        # even though the subscription is already live.
        yield ": connected\n\n"
        if need_resync:
            # 缓冲不足以无缝补发（如后端重启）：提示前端调 /state 重建
            yield "event: resync\ndata: {}\n\n"
        for e in replay:
            yield _sse(e)
        while True:
            try:
                e = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_SECONDS)
                yield _sse(e)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    finally:
        events.unsubscribe(run_id, q)
