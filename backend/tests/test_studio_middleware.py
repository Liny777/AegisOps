"""StudioTracingMiddleware：chat span 补写 gen_ai.input.messages（需装 agentscope，否则整文件 skip）。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("agentscope")

from agentscope.model import ChatModelBase
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from runtime import studio_tracing as tr
from runtime.studio_middleware import StudioTracingMiddleware, _serialize_messages


class StubChatModel(ChatModelBase):
    """绕过 __init__ 的最小桩：isinstance(ChatModelBase) 是 TracingMiddleware 发 span 的前提。"""


def _stub_model() -> StubChatModel:
    m = object.__new__(StubChatModel)
    m.model = "stub-model"
    return m


@pytest.fixture()
def exporter() -> InMemorySpanExporter:
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):  # 全局只能 set 一次；已有 SDK provider 则复用
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    exp = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    return exp


async def test_on_model_call_stamps_input_messages(exporter):
    mw = StudioTracingMiddleware()
    agent = SimpleNamespace(state=SimpleNamespace(session_id="fsid-mwtest"))
    messages = [{"role": "user", "content": "支付超时，帮我查"}]

    async def next_handler(**kwargs):
        return SimpleNamespace()  # 非 ChatResponse：extractor 容错为 str() 文本输出

    result = await mw.on_model_call(
        agent, {"current_model": _stub_model(), "messages": messages}, next_handler)
    assert result is not None

    spans = [s for s in exporter.get_finished_spans()
             if (s.attributes or {}).get(tr.A_OPERATION_NAME) == tr.OP_CHAT]
    assert spans, "TracingMiddleware 未发 chat span（全局 provider 未生效？）"
    attrs = spans[-1].attributes
    assert "支付超时，帮我查" in attrs[tr.A_INPUT_MESSAGES]  # 子类补写的输入
    assert attrs[tr.A_OUTPUT_MESSAGES]  # super 写的输出仍在（子类没破坏原行为）
    assert attrs[tr.A_CONVERSATION_ID] == "fsid-mwtest"


async def test_on_model_call_skips_span_without_chat_model(exporter):
    """current_model 不是 ChatModelBase → TracingMiddleware 原样直通，不发 span、不炸。"""
    mw = StudioTracingMiddleware()
    agent = SimpleNamespace(state=SimpleNamespace(session_id="fsid-mwtest2"))
    called = {}

    async def next_handler(**kwargs):
        called["kwargs"] = kwargs
        return "raw"

    result = await mw.on_model_call(agent, {"messages": [], "current_model": None}, next_handler)
    assert result == "raw" and "kwargs" in called


def test_serialize_messages_robustness():
    assert _serialize_messages(None) == ""
    assert "你好" in _serialize_messages([{"role": "user", "content": "你好"}])
    long = _serialize_messages([{"c": "x" * 40000}])
    assert len(long) <= 32001 and long.endswith("…")

    class Weird:
        def __iter__(self):
            raise RuntimeError("boom")

    assert isinstance(_serialize_messages(Weird()), str)  # 序列化失败也不抛
