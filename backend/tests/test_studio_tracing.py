"""studio_tracing 单测（不依赖 DB/agentscope）：_span_to_row 抽取、on_start 盖属性、队列丢弃。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("opentelemetry.sdk")  # otel 随 agentscope 可选组安装；未装则整文件 skip

from runtime import studio_tracing as tr
from runtime.studio_context import reset_studio_task_context, set_studio_task_context


def _fake_span(attributes: dict, *, start_ns: int = 1_000_000_000, end_ns: int = 3_500_000_000):
    ctx = SimpleNamespace(trace_id=0xABC, span_id=0xDEF)
    status = SimpleNamespace(status_code=SimpleNamespace(name="OK"))
    return SimpleNamespace(
        attributes=attributes, get_span_context=lambda: ctx,
        parent=SimpleNamespace(span_id=0x123), start_time=start_ns, end_time=end_ns,
        status=status,
    )


def test_span_to_row_llm_extraction_and_latency():
    row = tr._span_to_row(_fake_span({
        tr.A_OPERATION_NAME: tr.OP_CHAT,
        tr.A_CONVERSATION_ID: "fsid-1",
        tr.A_REQUEST_MODEL: "glm-5.1",
        tr.A_USAGE_INPUT_TOKENS: 100,
        tr.A_USAGE_OUTPUT_TOKENS: 30,
        tr.A_INPUT_MESSAGES: "[in]",
        tr.A_OUTPUT_MESSAGES: "[out]",
        "openops.user_id": "u1",
        "openops.run_id": "r1",
        "openops.task_id": "t1",
        "openops.agent_role": "main",
    }), text_cap=8000)
    assert row is not None
    assert row["kind"] == "llm" and row["agent_run_id"] == "r1" and row["user_id"] == "u1"
    assert row["session_id"] == "fsid-1" and row["agent_role"] == "main" and row["task_id"] == "t1"
    assert row["model"] == "glm-5.1" and row["input_tokens"] == 100 and row["output_tokens"] == 30
    assert row["input_messages"] == "[in]" and row["output_messages"] == "[out]"
    assert row["latency_ms"] == 2500.0  # (3.5s - 1.0s)
    assert row["started_at"] == 1.0 and row["ended_at"] == 3.5
    assert row["trace_id"] == format(0xABC, "032x") and row["parent_span_id"] == format(0x123, "016x")
    assert row["span_status"] == "OK"


def test_span_to_row_drops_unowned_and_uncaptured():
    # 无 openops.run_id（非捕获上下文）→ 丢
    assert tr._span_to_row(_fake_span({tr.A_OPERATION_NAME: tr.OP_CHAT}), 8000) is None
    # 非三类 operation → 丢
    assert tr._span_to_row(_fake_span({
        tr.A_OPERATION_NAME: "embedding", "openops.run_id": "r1"}), 8000) is None


def test_span_to_row_caps_text_fields():
    row = tr._span_to_row(_fake_span({
        tr.A_OPERATION_NAME: tr.OP_EXECUTE_TOOL,
        tr.A_TOOL_NAME: "query_logs",
        tr.A_TOOL_CALL_ARGUMENTS: "x" * 9000,
        tr.A_TOOL_CALL_RESULT: "y" * 100,
        "openops.run_id": "r1",
    }), text_cap=1000)
    assert row["kind"] == "tool" and row["tool_name"] == "query_logs"
    assert len(row["tool_args"]) == 1001 and row["tool_args"].endswith("…")
    assert row["tool_result"] == "y" * 100


class _AttrSpan:
    def __init__(self):
        self.attrs: dict = {}

    def set_attribute(self, k, v):
        self.attrs[k] = v


def test_on_start_stamps_contextvars():
    proc = tr.OpenOpsSpanProcessor()
    span = _AttrSpan()
    proc.on_start(span)  # 未 set contextvar → 不盖任何属性
    assert span.attrs == {}

    toks = set_studio_task_context("u1", "r1", "t1", "inspect")
    try:
        span2 = _AttrSpan()
        proc.on_start(span2)
        assert span2.attrs == {
            "openops.user_id": "u1", "openops.run_id": "r1",
            "openops.task_id": "t1", "openops.agent_role": "inspect",
        }
    finally:
        reset_studio_task_context(toks)
    span3 = _AttrSpan()
    proc.on_start(span3)  # reset 后回到非捕获
    assert span3.attrs == {}


def test_safe_put_drops_when_full():
    q: asyncio.Queue = asyncio.Queue(maxsize=1)
    tr._safe_put(q, {"a": 1})
    tr._safe_put(q, {"b": 2})  # 满：静默丢，不抛
    assert q.qsize() == 1


def test_on_end_before_drain_is_noop():
    proc = tr.OpenOpsSpanProcessor()
    proc.on_end(_fake_span({tr.A_OPERATION_NAME: tr.OP_CHAT, "openops.run_id": "r1"}))
    assert proc.queue.qsize() == 0  # loop 未 attach → 丢弃且不抛
