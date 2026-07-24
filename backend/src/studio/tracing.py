"""Agent Studio 的 span 捕获（OpenTelemetry SpanProcessor；移植自老项目 studio_tracing）。

数据通路
========
agentscope ``TracingMiddleware``（经 `studio_middleware.StudioTracingMiddleware` 挂到
Agent 上）对每次 agent 运行发三类 span：
  - ``invoke_agent {name}``  (operation = invoke_agent)  → agent 层输入输出（交接内容）
  - ``chat {model}``         (operation = chat)          → LLM 调用（token/模型；输入由
    StudioTracingMiddleware 补写 ``gen_ai.input.messages``，2.x 默认只写输出）
  - ``execute_tool {tool}``  (operation = execute_tool)  → 工具/MCP 调用入参与结果

``OpenOpsSpanProcessor`` 挂在全局 SDK TracerProvider 上：
  - ``on_start``：从 contextvar（``studio_context``，run_task/_run_one 开跑处写）读
    user_id / run_id / task_id / role，**盖成 span 属性**（``openops.*``）。on_start 跑在
    起 span 的事件循环线程上，contextvar 可见。
  - ``on_end``：把 span 抽成一行纯 dict，``call_soon_threadsafe`` 投进有界 asyncio 队列
    （满则丢——可观测性数据，丢优于拖垮 run），循环内 drain task 逐行
    ``studio_spans.insert_span(...)`` 落库。**on_end 绝不碰连接池、绝不阻塞。**

身份从 **span 属性**（on_start 盖的）读回，而非 on_end 再读 contextvar——即便某个工具
span 在线程池里结束（contextvar 不可见），也仍拿得到正确归属。

本模块顶层 import ``opentelemetry.sdk``（随 agentscope 可选组安装），只允许被
main.lifespan 惰性 import——mock runtime / 未装 agentscope 时进程内零成本。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider

from studio.context import (
    studio_agent_role,
    studio_run_id,
    studio_task_id,
    studio_user_id,
)

logger = logging.getLogger("openops.studio")

# ── agentscope 用的 span 属性键 / operation 取值（单一真源：其 SpanAttributes；
#    import 失败则回退到 OTel GenAI semconv 字面量，保证捕获不因内部路径变动而崩）──
try:  # pragma: no cover - import path 因 agentscope 版本而异
    from agentscope.middleware._tracing._attributes import (  # type: ignore
        OperationNameValues as _AgOps,
        SpanAttributes as _AgAttr,
    )

    A_OPERATION_NAME = _AgAttr.GEN_AI_OPERATION_NAME
    A_CONVERSATION_ID = _AgAttr.GEN_AI_CONVERSATION_ID
    A_AGENT_NAME = _AgAttr.GEN_AI_AGENT_NAME
    A_REQUEST_MODEL = _AgAttr.GEN_AI_REQUEST_MODEL
    A_PROVIDER_NAME = _AgAttr.GEN_AI_PROVIDER_NAME
    A_USAGE_INPUT_TOKENS = _AgAttr.GEN_AI_USAGE_INPUT_TOKENS
    A_USAGE_OUTPUT_TOKENS = _AgAttr.GEN_AI_USAGE_OUTPUT_TOKENS
    A_CACHE_INPUT_TOKENS = _AgAttr.AGENTSCOPE_CACHE_INPUT_TOKENS
    A_FINISH_REASONS = _AgAttr.GEN_AI_RESPONSE_FINISH_REASONS
    A_TOOL_NAME = _AgAttr.GEN_AI_TOOL_NAME
    A_TOOL_CALL_ARGUMENTS = _AgAttr.GEN_AI_TOOL_CALL_ARGUMENTS
    A_TOOL_CALL_RESULT = _AgAttr.GEN_AI_TOOL_CALL_RESULT
    A_INPUT_MESSAGES = _AgAttr.GEN_AI_INPUT_MESSAGES
    A_OUTPUT_MESSAGES = _AgAttr.GEN_AI_OUTPUT_MESSAGES
    OP_CHAT = _AgOps.CHAT
    OP_INVOKE_AGENT = _AgOps.INVOKE_AGENT
    OP_EXECUTE_TOOL = _AgOps.EXECUTE_TOOL
except Exception:  # noqa: BLE001
    A_OPERATION_NAME = "gen_ai.operation.name"
    A_CONVERSATION_ID = "gen_ai.conversation_id"
    A_AGENT_NAME = "gen_ai.agent.name"
    A_REQUEST_MODEL = "gen_ai.request.model"
    A_PROVIDER_NAME = "gen_ai.provider.name"
    A_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
    A_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
    A_CACHE_INPUT_TOKENS = "agentscope.usage.cache_input_tokens"
    A_FINISH_REASONS = "gen_ai.response.finish_reasons"
    A_TOOL_NAME = "gen_ai.tool.name"
    A_TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
    A_TOOL_CALL_RESULT = "gen_ai.tool.call.result"
    A_INPUT_MESSAGES = "gen_ai.input.messages"
    A_OUTPUT_MESSAGES = "gen_ai.output.messages"
    OP_CHAT = "chat"
    OP_INVOKE_AGENT = "invoke_agent"
    OP_EXECUTE_TOOL = "execute_tool"

# operation → 我们的 kind；只捕获这三类。
_OP_TO_KIND = {OP_INVOKE_AGENT: "agent", OP_CHAT: "llm", OP_EXECUTE_TOOL: "tool"}
_CAPTURED_OPS = frozenset(_OP_TO_KIND)

# on_start 盖的自有属性（不与 gen_ai.* 撞名）。
_ATTR_USER_ID = "openops.user_id"
_ATTR_RUN_ID = "openops.run_id"
_ATTR_TASK_ID = "openops.task_id"
_ATTR_AGENT_ROLE = "openops.agent_role"

_DEFAULT_TEXT_CAP = 8000
_PURGE_INTERVAL_S = 3600.0


def _text_cap() -> int:
    try:
        return max(256, int(os.environ.get("OPENOPS_STUDIO_TEXT_CAP", str(_DEFAULT_TEXT_CAP))))
    except ValueError:
        return _DEFAULT_TEXT_CAP


def _as_int(v: Any) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _cap(v: Any, cap: int) -> str:
    if v is None:
        return ""
    s = v if isinstance(v, str) else str(v)
    return s if len(s) <= cap else (s[:cap] + "…")


def _span_to_row(span: ReadableSpan, text_cap: int) -> dict[str, Any] | None:
    """ReadableSpan → sre_agent_studio_span 一行（键 = studio_spans.insert_span 的 kwargs）。

    无 openops.run_id（非捕获上下文/竞态）→ 返回 None（不落无主行）。
    """
    attr = span.attributes or {}
    op = attr.get(A_OPERATION_NAME)
    if op not in _CAPTURED_OPS:
        return None
    run_id = attr.get(_ATTR_RUN_ID) or ""
    if not run_id:
        return None

    ctx = span.get_span_context()
    trace_id = format(ctx.trace_id, "032x") if ctx else ""
    span_id = format(ctx.span_id, "016x") if ctx else ""
    parent_span_id = format(span.parent.span_id, "016x") if span.parent else ""

    start_ns = span.start_time or 0
    end_ns = span.end_time or start_ns
    latency_ms = max(0.0, (end_ns - start_ns) / 1e6)

    span_status = ""
    try:
        span_status = span.status.status_code.name  # OK / ERROR / UNSET
    except Exception:  # noqa: BLE001
        pass

    return {
        "user_id": str(attr.get(_ATTR_USER_ID) or ""),
        "agent_run_id": str(run_id),
        "task_id": str(attr.get(_ATTR_TASK_ID) or ""),
        "session_id": str(attr.get(A_CONVERSATION_ID) or ""),
        "agent_role": str(attr.get(_ATTR_AGENT_ROLE) or ""),
        "agent_name": str(attr.get(A_AGENT_NAME) or ""),
        "kind": _OP_TO_KIND[op],
        "model": str(attr.get(A_REQUEST_MODEL) or ""),
        "provider": str(attr.get(A_PROVIDER_NAME) or ""),
        "input_tokens": _as_int(attr.get(A_USAGE_INPUT_TOKENS)),
        "output_tokens": _as_int(attr.get(A_USAGE_OUTPUT_TOKENS)),
        "cache_input_tokens": _as_int(attr.get(A_CACHE_INPUT_TOKENS)),
        "latency_ms": latency_ms,
        "started_at": start_ns / 1e9,
        "ended_at": end_ns / 1e9,
        "tool_name": str(attr.get(A_TOOL_NAME) or ""),
        "tool_args": _cap(attr.get(A_TOOL_CALL_ARGUMENTS), text_cap),
        "tool_result": _cap(attr.get(A_TOOL_CALL_RESULT), text_cap),
        # chat span 的 input 由 StudioTracingMiddleware 补写；invoke_agent 的 input 是
        # agent 层入参（主=用户问题，子=派发指令）→ 交接内容的 span 侧来源。
        "input_messages": _cap(attr.get(A_INPUT_MESSAGES), text_cap),
        "output_messages": _cap(attr.get(A_OUTPUT_MESSAGES), text_cap),
        "finish_reason": str(attr.get(A_FINISH_REASONS) or ""),
        "span_status": span_status,
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
    }


def _safe_put(queue: "asyncio.Queue[dict[str, Any]]", row: dict[str, Any]) -> None:
    # 在循环线程上执行（call_soon_threadsafe 调度）：队列满则丢弃，绝不抛进 loop 异常处理器。
    try:
        queue.put_nowait(row)
    except asyncio.QueueFull:
        pass


class OpenOpsSpanProcessor(SpanProcessor):
    """把 agentscope 的 agent/llm/tool span 落进按 run 归组的 sre_agent_studio_span。"""

    def __init__(self, text_cap: int = _DEFAULT_TEXT_CAP, maxsize: int = 10000) -> None:
        self._text_cap = max(256, int(text_cap or _DEFAULT_TEXT_CAP))
        self.queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue(maxsize=maxsize)
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """drain task 启动时（在事件循环上）登记 loop，供 on_end 跨线程投递。"""
        self._loop = loop

    # SpanProcessor 接口 ────────────────────────────────────────
    def on_start(self, span: Any, parent_context: Any = None) -> None:
        try:
            uid = studio_user_id.get()
            if uid is None:
                return  # 非捕获上下文（后台 reconciler/sweeper 等）
            span.set_attribute(_ATTR_USER_ID, uid)
            rid = studio_run_id.get()
            if rid:
                span.set_attribute(_ATTR_RUN_ID, rid)
            tid = studio_task_id.get()
            if tid:
                span.set_attribute(_ATTR_TASK_ID, tid)
            role = studio_agent_role.get()
            if role:
                span.set_attribute(_ATTR_AGENT_ROLE, role)
        except Exception:  # noqa: BLE001 捕获绝不影响 run
            pass

    def on_end(self, span: ReadableSpan) -> None:
        loop = self._loop
        if loop is None:
            return  # drain 未起，丢弃（服务跑起来前不会有 agent run）
        try:
            row = _span_to_row(span, self._text_cap)
            if row is None:
                return
            loop.call_soon_threadsafe(_safe_put, self.queue, row)
        except Exception:  # noqa: BLE001
            pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:  # noqa: ARG002
        return True


async def run_studio_span_drain_loop(processor: OpenOpsSpanProcessor) -> None:
    """循环内常驻 task：把队列里的 span 行落库。永不因单行失败而退出；每小时顺带清过期行。"""
    from studio import repository as studio_spans

    processor.attach_loop(asyncio.get_running_loop())
    logger.info("[agent-studio] span drain loop started")
    last_purge = time.monotonic()
    while True:
        row = await processor.queue.get()
        try:
            await studio_spans.insert_span(**row)
        except Exception as e:  # noqa: BLE001 单行落库失败不拖垮捕获；日志绝不带 span 内容
            logger.debug("[agent-studio] insert_span failed: %s", e)
        if time.monotonic() - last_purge >= _PURGE_INTERVAL_S:
            last_purge = time.monotonic()
            try:
                await studio_spans.purge_expired()
            except Exception as e:  # noqa: BLE001
                logger.debug("[agent-studio] purge_expired failed: %s", e)


def install_and_start() -> asyncio.Task:
    """lifespan 一行接入：确保全局 SDK TracerProvider → 挂 processor → 起 drain task。

    agentscope 的 `_check_tracing_enabled` 只认全局 SDK TracerProvider——这里 set 之后，
    Agent 上挂的 TracingMiddleware 才真正发 span；不 set 则其自身 no-op（开关语义）。
    """
    existing = trace.get_tracer_provider()
    if isinstance(existing, TracerProvider):
        provider = existing
    else:
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    proc = OpenOpsSpanProcessor(text_cap=_text_cap())
    provider.add_span_processor(proc)
    return asyncio.create_task(
        run_studio_span_drain_loop(proc), name="openops-studio-span-drain"
    )
