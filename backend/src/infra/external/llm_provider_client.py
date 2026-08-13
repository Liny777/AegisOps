"""LLM Provider client：OpenAI 兼容探测（MODEL-001/002）。

`OPENOPS_LLM_PROBE=real(默认)|mock`——**只有显式写 mock 才假探测**，其余值（含拼错、空）一律走 real：
默认必须 fail-closed，漏配环境变量应当是「真探测」而非「静默假通过」。曾默认 mock，导致线上
「测试连接」对任何 Key 都返回绿勾（mock 分支无视 base_url/api_key），用户以为验过了其实没有。
- real：向 `{base_url}/chat/completions` 发一次最小请求（带 dummy tools + tool_choice），
  验**可达 + 接受 tools 参数**（= supports_tool_calling）。连接错/超时/HTTP 错 → ok=False，
  reason 脱敏（绝不含 api_key）。调用方在 probe 之前已过 `egress.check_llm_egress`（C2，SSRF 防护）。
- mock：启发式——model_name 含 "no-tool" 视为不支持 tool calling → 探测失败分支可测，不打网。
  仅供离线开发/测试（conftest 显式 setdefault）与确无出网的部署。

返回的 `probe_mode`（"real"|"mock"）会一路透传到前端：mock 时 UI 显示「未真实探测」警告而非绿勾，
免得假探测再次被当成真的。
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


async def probe(
    base_url: str, model_name: str, api_key: str | None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    # 「非 mock 即 real」：拼错的值往安全方向倒（真探测），绝不静默退回假通过
    if os.getenv("OPENOPS_LLM_PROBE", "real").lower() != "mock":
        return await _probe_real(base_url, model_name, api_key, extra_headers)
    supports_tool = "no-tool" not in model_name.lower()
    return {
        "ok": supports_tool,
        "supports_tool_calling": supports_tool,
        "supports_streaming": True,
        "reason": None if supports_tool else "model does not support tool calling",
        "probe_mode": "mock",
    }


async def _probe_real(
    base_url: str, model_name: str, api_key: str | None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    import httpx

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    # 用户自定义 Header 与真实调用（agentscope_runtime default_headers）同源；schema 层已禁
    # Authorization/Content-Type 等保留头，故后设的 Authorization 不会被覆盖。值不进日志/reason。
    headers.update(extra_headers or {})
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
                "reason": f"模型端点不可达（{type(exc).__name__}）", "probe_mode": "real"}
    if r.status_code == 200:
        return {"ok": True, "supports_tool_calling": True, "supports_streaming": True,
                "reason": None, "probe_mode": "real"}
    # 4xx：区分「不支持 tools 参数」与「鉴权/请求错」，不回显响应体（可能含敏感）
    reason = _real_error_reason(r.status_code, _peek_body(r))
    return {"ok": False, "supports_tool_calling": False, "supports_streaming": False,
            "reason": reason, "probe_mode": "real"}


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
