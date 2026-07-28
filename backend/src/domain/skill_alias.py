"""Skill 原始名 → skill_key 别名解析（29.9 命名空间化配套）。

29.9 起新 skill 的 skill_id（本地 skill_key）带命名空间前缀（`user-{工号}-{name}` / `system-{name}`），
而人可见的名是 SKILL.md 原始名（display_name）。斜杠命令与 run_platform_skill 的入参允许用原始名，
本模块把它解析回 canonical key；下载/绑定/LLM 目录/审计等内部链路仍一律使用 skill_key。
"""
from __future__ import annotations

from typing import Any


def resolve_skill_alias(name: str, available: dict[str, dict[str, Any]]) -> tuple[str | None, list[str]]:
    """把用户/模型给出的 skill 名解析为可执行集合里的 canonical skill_key。

    精确 key 命中优先（存量裸名与带前缀新 key 都直达，且在「存量裸名 + 新前缀同 display_name」
    并存时保证确定性）；否则按 display_name 等值找**唯一**命中。

    Returns:
        (解析出的 key | None, display_name 命中的候选 key 列表)——多义时 key 为 None、
        候选列表供调用方报错列举（模型可自纠为完整 skill_key）。
    """
    if name in available:
        return name, [name]
    hits = [k for k, v in available.items() if str(v.get("display_name") or "") == name]
    if len(hits) == 1:
        return hits[0], hits
    return None, hits
