"""Agent Studio 的 TracingMiddleware 子类（顶层 import agentscope，只允许惰性 import）。

agentscope 2.x 的 ``TracingMiddleware.on_model_call`` 只写请求参数与输出
（``gen_ai.output.messages``），**不写本次调用的输入 messages**——而管理员复盘需要
「大模型的输入输出」都可见。这里包一层 next_handler：wrapper 执行时已处在 super 的
``start_as_current_span`` 作用域内，current span 即 chat span，把 ``messages`` 序列化
写到 ``gen_ai.input.messages`` 后再调原 handler。失败只吞掉，绝不影响 run。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from agentscope.middleware import TracingMiddleware
from opentelemetry import trace as _otel_trace

from runtime.studio_context import studio_enabled
from runtime.studio_tracing import A_INPUT_MESSAGES

logger = logging.getLogger("openops.studio")

# 序列化预截：落库前 studio_tracing._cap 还会按 OPENOPS_STUDIO_TEXT_CAP（默认 8000）截一次，
# 这里只防超长历史把 span 属性撑到离谱。
_SERIALIZE_CAP = 32000


def _serialize_messages(msgs: Any) -> str:
    try:
        if msgs is None:
            return ""
        items = [m.to_dict() if hasattr(m, "to_dict") else m for m in msgs]
        s = json.dumps(items, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        try:
            s = str(msgs)
        except Exception:  # noqa: BLE001
            return ""
    return s if len(s) <= _SERIALIZE_CAP else (s[:_SERIALIZE_CAP] + "…")


class StudioTracingMiddleware(TracingMiddleware):
    """TracingMiddleware + chat span 补写输入 messages。"""

    async def on_model_call(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        async def wrapped(**kwargs: Any) -> Any:
            try:
                span = _otel_trace.get_current_span()
                if span is not None and span.is_recording():
                    span.set_attribute(
                        A_INPUT_MESSAGES, _serialize_messages(kwargs.get("messages"))
                    )
            except Exception:  # noqa: BLE001 捕获绝不影响 run
                pass
            return await next_handler(**kwargs)

        return await super().on_model_call(agent, input_kwargs, wrapped)


_construct_failed = False


def studio_middlewares() -> list[Any]:
    """Agent(middlewares=...) 用；开关关闭或构造失败返回 []（宁缺捕获不断 run）。

    每次返回**新实例**，不做单例——同一 middleware 对象被多个 Agent 共享时会破坏
    agentscope 的 per-agent 行为（实测破坏 AgentState 会话连续性，test_p3）。
    """
    global _construct_failed
    if _construct_failed or not studio_enabled():
        return []
    try:
        return [StudioTracingMiddleware()]
    except Exception as e:  # noqa: BLE001 —— agentscope 版本漂移等：降级为不捕获
        logger.warning("[agent-studio] TracingMiddleware 构造失败，span 捕获降级关闭：%s", e)
        _construct_failed = True
        return []
