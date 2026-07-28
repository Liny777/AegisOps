"""resolve_skill_alias（29.9 命名空间化别名解析）三态单测：精确 key / 唯一 display_name / 多义与未命中。"""
from __future__ import annotations

from domain.skill_alias import resolve_skill_alias

_AVAILABLE = {
    "inspection": {"display_name": "巡检 inspection"},       # 存量裸名（display 含空格，只能按 key 命中）
    "user-0026demo01-logscan": {"display_name": "logscan"},  # 29.9 新前缀个人 skill
    "system-report": {"display_name": "report"},             # 29.9 新前缀系统 skill
    "user-a-dup": {"display_name": "dup"},
    "user-b-dup": {"display_name": "dup"},                   # display_name 多义
}


def test_exact_key_wins():
    assert resolve_skill_alias("inspection", _AVAILABLE) == ("inspection", ["inspection"])
    assert resolve_skill_alias("user-0026demo01-logscan", _AVAILABLE)[0] == "user-0026demo01-logscan"


def test_unique_display_name_resolves():
    assert resolve_skill_alias("logscan", _AVAILABLE)[0] == "user-0026demo01-logscan"
    assert resolve_skill_alias("report", _AVAILABLE)[0] == "system-report"


def test_ambiguous_or_missing_returns_none():
    key, hits = resolve_skill_alias("dup", _AVAILABLE)
    assert key is None and set(hits) == {"user-a-dup", "user-b-dup"}
    assert resolve_skill_alias("nope", _AVAILABLE) == (None, [])
    assert resolve_skill_alias("x", {}) == (None, [])


def test_exact_key_beats_display_name_collision():
    """存量裸名 key 与别家 display_name 撞名：精确 key 优先（确定性、向后兼容）。"""
    avail = {"foo": {"display_name": "旧foo"}, "user-1-bar": {"display_name": "foo"}}
    assert resolve_skill_alias("foo", avail) == ("foo", ["foo"])
