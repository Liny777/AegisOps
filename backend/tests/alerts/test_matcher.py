"""matcher 纯函数：匹配矩阵（维度间 AND、维度内 OR、空维度不限）+ 归一化/指纹/聚合键。"""
from __future__ import annotations

from alerts import matcher

ALERT = {
    "category": "MySQL",
    "severity": "fatal",
    "strategy_name": "MySQL 主从延迟监控",
    "app_id": "APP-A",
    "labels": {"service": "pay-core", "env": "prod"},
    "title": "MySQL 主库延迟>5s",
    "description": "主从延迟 6.8s 持续 3 分钟",
}


def _m(**match) -> bool:
    return matcher.match(ALERT, match)


def test_empty_rule_matches_everything():
    assert _m() is True


def test_category_dimension_multi_value():
    assert _m(categories=["MySQL"]) is True
    assert _m(categories=["Redis", "MySQL"]) is True   # 多选任一命中即过
    assert _m(categories=["Redis", "Nginx"]) is False
    assert _m(categories=[]) is True  # 空=不限（服务层强制必填，matcher 层保持宽松）


def test_category_legacy_single_value_still_matches():
    """存量规则 match_json 仍是 category 单值：读侧兼容，不迁移也能撮合。"""
    assert _m(category="MySQL") is True
    assert _m(category="Redis") is False
    assert _m(category="") is True


def test_strategy_dimension():
    assert _m(strategies=["MySQL 主从延迟监控", "MySQL 慢查询监控"]) is True
    assert _m(strategies=["MySQL 慢查询监控"]) is False
    assert _m(strategies=[]) is True  # 空=该类型全部策略
    assert matcher.match({**ALERT, "strategy_name": ""},
                         {"strategies": ["MySQL 主从延迟监控"]}) is False  # 对端缺字段不误命中


def test_severity_dimension():
    assert _m(severities=["fatal", "critical"]) is True
    assert _m(severities=["warning"]) is False


def test_appid_dimension():
    assert _m(appids=["APP-A"]) is True
    assert _m(appids=["APP-B"]) is False
    assert matcher.match({**ALERT, "app_id": None}, {"appids": ["APP-A"]}) is False


def test_label_selectors_all_must_equal():
    assert _m(label_selectors={"service": "pay-core"}) is True
    assert _m(label_selectors={"service": "pay-core", "env": "prod"}) is True
    assert _m(label_selectors={"service": "pay-core", "env": "dev"}) is False
    assert _m(label_selectors={"missing": "x"}) is False


def test_keywords_any_over_title_and_description():
    assert _m(keywords=["延迟"]) is True
    assert _m(keywords=["不存在词", "主从"]) is True  # description 也算
    assert _m(keywords=["不存在词"]) is False
    assert _m(keywords=["MYSQL"]) is True  # 不区分大小写


def test_dimensions_are_anded():
    assert _m(category="MySQL", severities=["fatal"],
              strategies=["MySQL 主从延迟监控"], keywords=["延迟"]) is True
    assert _m(category="MySQL", severities=["warning"], keywords=["延迟"]) is False
    assert _m(category="MySQL", severities=["fatal"],
              strategies=["MySQL 慢查询监控"]) is False


def test_normalize_severity_maps_unknown_to_warning():
    assert matcher.normalize_severity("fatal") == ("fatal", False)
    assert matcher.normalize_severity("CRITICAL") == ("critical", False)
    assert matcher.normalize_severity("P0") == ("warning", True)
    assert matcher.normalize_severity("") == ("warning", True)


def test_fingerprint_fallback_deterministic_and_label_order_free():
    a = matcher.fingerprint_fallback("prom", "t", {"b": "2", "a": "1"})
    b = matcher.fingerprint_fallback("prom", "t", {"a": "1", "b": "2"})
    assert a == b and a.startswith("fp_")
    assert matcher.fingerprint_fallback("prom", "t2", {"a": "1"}) != a


def test_group_key_roundtrip():
    gk = matcher.group_key("inst-1", "APP-A", "MySQL 主库延迟>5s")
    assert gk == "inst-1|APP-A|MySQL 主库延迟>5s"
    assert matcher.app_id_from_group_key(gk) == "APP-A"
    assert matcher.app_id_from_group_key(matcher.group_key("inst-1", None, "x")) == ""


def test_severity_rank_order():
    ranks = [matcher.severity_rank(s) for s in ("fatal", "critical", "warning", "info")]
    assert ranks == sorted(ranks)
    assert matcher.severity_rank("unknown") > matcher.severity_rank("info")


# ---------------- category 倒排索引（2026-08-14 吞吐） ----------------

RULES = [
    {"alert_rule_id": "r0", "agent_team_instance_id": "i0",
     "match_json": {"categories": ["MySQL", "Redis"]}},
    {"alert_rule_id": "r1", "agent_team_instance_id": "i1",
     "match_json": {"category": "MySQL"}},                   # legacy 单值
    {"alert_rule_id": "r2", "agent_team_instance_id": "i2",
     "match_json": {"severities": ["fatal"]}},               # 空类别 → wildcard
    {"alert_rule_id": "r3", "agent_team_instance_id": "i3",
     "match_json": {"categories": ["Nginx"]}},
]


def test_index_buckets_and_wildcard():
    idx = matcher.build_rule_index(RULES)
    assert set(idx["by_category"]) == {"MySQL", "Redis", "Nginx"}
    assert idx["by_category"]["MySQL"] == [0, 1]   # legacy 单值也进桶
    assert idx["wildcard"] == [2]                  # 不限类型的规则每类都候选


def test_candidates_union_keeps_original_order():
    idx = matcher.build_rule_index(RULES)
    got = [r["alert_rule_id"] for r in matcher.candidate_rules(idx, "MySQL")]
    assert got == ["r0", "r1", "r2"]               # 类型桶 ∪ wildcard，按原始下标序
    assert [r["alert_rule_id"] for r in matcher.candidate_rules(idx, "PostgreSQL")] == ["r2"]
    assert [r["alert_rule_id"] for r in matcher.candidate_rules(idx, "")] == ["r2"]


def test_index_path_equals_linear_scan():
    """差分性质：索引候选→精判 的命中集 ≡ 全量线性 match 的命中集（六维语义零变的守卫）。"""
    alerts = [
        dict(ALERT),
        {**ALERT, "category": "Nginx", "severity": "warning"},
        {**ALERT, "category": "Redis", "severity": "fatal"},
        {**ALERT, "category": ""},
        {**ALERT, "severity": "info"},
    ]
    idx = matcher.build_rule_index(RULES)
    for alert in alerts:
        linear = [r["alert_rule_id"] for r in RULES
                  if matcher.match(alert, r["match_json"])]
        via_index = [r["alert_rule_id"]
                     for r in matcher.candidate_rules(idx, alert.get("category") or "")
                     if matcher.match(alert, r["match_json"])]
        assert via_index == linear, f"索引路径与线性扫描不一致：{alert}"
