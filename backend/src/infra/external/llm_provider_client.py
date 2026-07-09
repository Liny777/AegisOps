"""LLM Provider client（mock）：OpenAI 兼容探测（MODEL-001/002）。

约定：model_name 含 "no-tool" 视为不支持 tool calling → 探测失败分支可测。
"""
from __future__ import annotations

from typing import Any


async def probe(base_url: str, model_name: str, api_key: str | None) -> dict[str, Any]:
    supports_tool = "no-tool" not in model_name.lower()
    return {
        "ok": supports_tool,
        "supports_tool_calling": supports_tool,
        "supports_streaming": True,
        "reason": None if supports_tool else "model does not support tool calling",
    }
