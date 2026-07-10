"""LLM Provider client：OpenAI 兼容探测（MODEL-001/002）。

`OPENOPS_LLM_PROBE=mock(默认)|real`：
- mock：启发式——model_name 含 "no-tool" 视为不支持 tool calling → 探测失败分支可测，不打网。
- real：向 `{base_url}/chat/completions` 发一次最小请求（带 dummy tools + tool_choice），
  验**可达 + 接受 tools 参数**（= supports_tool_calling）。连接错/超时/HTTP 错 → ok=False，
  reason 脱敏（绝不含 api_key）。调用方在 probe 之前已过 `egress.check_llm_egress`（C2，SSRF 防护）。
"""
from __future__ import annotations

import os
from typing import Any

# 用于探测 tool calling 能力的最小 dummy 工具（OpenAI 兼容格式）
_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "openops_probe_noop",
        "description": "probe tool-calling capability; do not call",
        "parameters": {"type": "object", "properties": {}},
    },
}


async def probe(base_url: str, model_name: str, api_key: str | None) -> dict[str, Any]:
    if os.getenv("OPENOPS_LLM_PROBE", "mock").lower() == "real":
        return await _probe_real(base_url, model_name, api_key)
    supports_tool = "no-tool" not in model_name.lower()
    return {
        "ok": supports_tool,
        "supports_tool_calling": supports_tool,
        "supports_streaming": True,
        "reason": None if supports_tool else "model does not support tool calling",
    }


async def _probe_real(base_url: str, model_name: str, api_key: str | None) -> dict[str, Any]:
    import httpx

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "tools": [_PROBE_TOOL],
        "tool_choice": "auto",
    }
    timeout = float(os.getenv("OPENOPS_LLM_PROBE_TIMEOUT_S", "15"))
    try:
        async with httpx.AsyncClient(timeout=timeout) as cli:
            r = await cli.post(url, json=payload, headers=headers)
    except Exception as exc:  # 连接错/超时/DNS：脱敏（不回显 url/key，只给类型）
        return {"ok": False, "supports_tool_calling": False, "supports_streaming": False,
                "reason": f"模型端点不可达（{type(exc).__name__}）"}
    if r.status_code == 200:
        return {"ok": True, "supports_tool_calling": True, "supports_streaming": True, "reason": None}
    # 4xx：区分「不支持 tools 参数」与「鉴权/请求错」，不回显响应体（可能含敏感）
    reason = _real_error_reason(r.status_code, _peek_body(r))
    return {"ok": False, "supports_tool_calling": False, "supports_streaming": False, "reason": reason}


def _peek_body(r: Any) -> str:
    try:
        return (r.text or "")[:200].lower()
    except Exception:
        return ""


def _real_error_reason(status: int, body_lower: str) -> str:
    if status in (401, 403):
        return "鉴权失败（API Key 无效或无权限）"
    if status == 404:
        return "端点或模型不存在（404）"
    if "tool" in body_lower or "function" in body_lower:
        return "模型不支持 tool calling"
    return f"模型探测失败（HTTP {status}）"
