"""Skill ZIP multipart 上传的 API 层共用件（用户面 assets 路由 与 管理台 admin 路由共两处）。

放在 api 包而非某个 router 里：50MB 上限与 ZIP 魔数校验一旦抄成两份，早晚漂移成两个不同的限制。
"""
from __future__ import annotations

import json

from domain.errors import ApiError, Err

MAX_SKILL_ZIP = 50 * 1024 * 1024  # 29.3 §2.1：ZIP ≤ 50MB


def parse_tags(raw: str) -> list[str]:
    """tags 兼容 JSON 数组串 `["a","b"]` 或逗号分隔 `a,b`（中英文逗号）。"""
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            return [str(x).strip() for x in json.loads(raw) if str(x).strip()]
        except (ValueError, TypeError):
            pass
    return [t.strip() for t in raw.replace("，", ",").split(",") if t.strip()]


def check_skill_zip(data: bytes) -> None:
    """上传前置校验：超限 → VALIDATION_FAILED；非 ZIP → SKILL_PACKAGE_INVALID。

    魔数校验的价值在定位：非 zip 直接拒，避免下游解包时抛 BadZipFile 让人误以为是包内容问题。"""
    if len(data) > MAX_SKILL_ZIP:
        raise ApiError(Err.VALIDATION_FAILED, "ZIP 超过 50MB 上限（29.3 §2.1）")
    if data[:2] != b"PK":
        raise ApiError(Err.SKILL_PACKAGE_INVALID, "文件不是有效的 ZIP 包")
