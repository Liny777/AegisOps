"""工具入参 key 级脱敏（缺陷批连带 D；16/30.4 号明令：用户对话中的密码/token 不得进审计与工具卡）。

`arguments_redacted_json` 列此前存的是原始入参、无任何脱敏动作——本模块让列名名副其实。
key 匹配敏感词 → 值整体打码；字符串值内的 sk-…/Bearer … 样式同样打码。
"""
from __future__ import annotations

import re
from typing import Any

_KEY_RE = re.compile(r"(pass(word|wd)?|token|secret|api[_-]?key|apikey|authorization|cookie|credential|private[_-]?key)", re.I)
_VAL_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|Bearer\s+[A-Za-z0-9._\-]{6,})")


def redact_args(obj: Any) -> Any:
    """递归脱敏（不改原对象）。dict 的敏感 key → "***"；字符串值抹 key/token 样式片段。"""
    if isinstance(obj, dict):
        return {k: ("***" if isinstance(k, str) and _KEY_RE.search(k) else redact_args(v))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_args(v) for v in obj]
    if isinstance(obj, str):
        return _VAL_RE.sub("[REDACTED]", obj)
    return obj
