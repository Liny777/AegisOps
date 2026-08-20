"""规则匹配与聚合键（纯函数，零 IO——匹配矩阵单测直接打这里）。

匹配语义（与 sre_alert_rule.match_json 注释一致）：维度间 AND、维度内 OR、
空数组/缺省 = 该维度不限。keyword 匹配 title+description 拼接串（不区分大小写），
keyword_mode 恒 any（all 语义 Phase2 自定义编辑器再开）。
"""
from __future__ import annotations

import hashlib
from typing import Any

from alerts.rule_templates import DEFAULT_RULE_PROMPT

SEVERITIES = ("fatal", "critical", "warning", "info")
SEVERITY_ORDER = list(SEVERITIES)  # 下标越小越严重


def severity_rank(severity: str) -> int:
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        return len(SEVERITY_ORDER)


def normalize_severity(raw: str) -> tuple[str, bool]:
    """对端严重度 → 平台四档；未知值降级 warning 并返回 changed=True（原始值存 labels 备查）。"""
    s = (raw or "").strip().lower()
    if s in SEVERITIES:
        return s, False
    return "warning", True


def fingerprint_fallback(source: str, title: str, labels: dict[str, Any]) -> str:
    """对端缺 fingerprint 时的指纹：sha256(source + title + 规范化 labels)。"""
    canon = "|".join(f"{k}={labels[k]}" for k in sorted(labels))
    digest = hashlib.sha256(f"{source}|{title}|{canon}".encode("utf-8")).hexdigest()
    return "fp_" + digest[:40]


def group_key(instance_id: str, app_id: str | None, title: str) -> str:
    """聚合键：实例|应用|告警名——附着式聚合与组冷却都按它判定。"""
    return f"{instance_id}|{app_id or '-'}|{title}"


def app_id_from_group_key(gk: str) -> str:
    parts = gk.split("|", 2)
    if len(parts) < 2 or parts[1] == "-":
        return ""
    return parts[1]


def rule_candidates(inc: dict[str, Any]) -> list[str]:
    """本单命中的规则 id（组首 alert_rule_id 优先，matched_rules_json 序回落，去重保序）。

    派发前的规则闸与 dispatcher 的提示词装配共用这一份口径——两处各抄一份必漂。
    存量单两者皆空时返回 []，调用方按 fail-open 处理（宁放行勿误杀历史数据）。
    """
    ids = [str(inc["alert_rule_id"])] if inc.get("alert_rule_id") else []
    ids += [rid for m in (inc.get("matched_rules_json") or [])
            if (rid := str((m or {}).get("rule_id") or "")) and rid not in ids]
    return ids


def effective_prompt(p: str | None) -> str:
    """prompt 归一判等口径：空白 ≡ 系统默认（与 dispatcher 派发时 rule_prompt or
    DEFAULT_RULE_PROMPT 同源——归一后相等的两条规则派发行为完全一致，才可同组/合并）。"""
    return (p or "").strip() or DEFAULT_RULE_PROMPT


def prompt_hash(p: str | None) -> str:
    """prompt 组标（sre_alert_incident.prompt_group）：归一 prompt 的 sha256 前 12 位。
    内容哈希而非 rule_id——同组最老规则被删时组标不漂移，附着/冷却判定不断裂。"""
    return hashlib.sha256(effective_prompt(p).encode("utf-8")).hexdigest()[:12]


def prompt_groups(rules: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """按归一 prompt 分组（2026-08-19 拍板：提示词不同的命中规则分开建单各自诊断，
    相同的合并一单）。dict 插入序保序：组序=组内首规则在入参中的原序，组内保原序
    ——入参已由 list_enabled_rules 的 ORDER BY 固化为最老规则优先。"""
    groups: dict[str, list[dict[str, Any]]] = {}
    for rule in rules:
        groups.setdefault(effective_prompt(rule.get("prompt")), []).append(rule)
    return list(groups.items())


def rule_categories(match_json: dict[str, Any]) -> list[str]:
    """规则的类别集合归一：categories 多值 ∪ legacy 单值 category 回退。

    match() 与倒排索引构建必须走同一套归一——索引漏了 legacy 规则就是漏单。
    （service._rule_categories 是同构逻辑；matcher 零依赖 service，各自持有 4 行。）
    """
    cats = [c for c in (match_json.get("categories") or []) if c]
    if not cats:
        legacy = str(match_json.get("category") or "")
        cats = [legacy] if legacy else []
    return cats


def build_rule_index(rules: list[dict[str, Any]]) -> dict[str, Any]:
    """category 倒排索引（2026-08-14 吞吐：3000 条/s × 千级规则 = 每秒百万次 match 全扫
    扛不住）。桶里存**规则下标**：candidate_rules 按下标排序合并，保持与 rules 原序一致
    ——`matched_rules[0]["owner_user_id"]` 与各 prompt 组的首规则都依赖组内顺序
    （2026-08-19 起源头顺序由 list_enabled_rules 的 ORDER BY 固化为最老规则优先）。
    空类别规则（不限类型）进 wildcard 桶，每条告警都要带上它精判。
    只倒排 category 一维：severities/strategies 区分度低，keywords/label_selectors
    是子串/逐键语义不可倒排——候选集仍逐条跑完整 match() 精判，六维语义零变。
    """
    by_category: dict[str, list[int]] = {}
    wildcard: list[int] = []
    for i, rule in enumerate(rules):
        cats = rule_categories(rule.get("match_json") or {})
        if not cats:
            wildcard.append(i)
            continue
        for cat in cats:
            by_category.setdefault(cat, []).append(i)
    return {"rules": rules, "by_category": by_category, "wildcard": wildcard}


def candidate_rules(index: dict[str, Any], category: str) -> list[dict[str, Any]]:
    """告警类别 → 候选规则（类型桶 ∪ 通配桶，按原始下标序）。"""
    idxs = index["by_category"].get(category or "", [])
    wildcard = index["wildcard"]
    if not wildcard:
        picked = idxs
    elif not idxs:
        picked = wildcard
    else:
        picked = sorted(set(idxs) | set(wildcard))
    rules = index["rules"]
    return [rules[i] for i in picked]


def match(alert: dict[str, Any], match_json: dict[str, Any]) -> bool:
    """alert 为 ingest 规范化后的形态：category/severity/strategy_name/app_id/labels/title/description。

    v2 规则形态：categories 多值（2026-08-07 弹窗改多选，任一命中即过；存量单值 category
    兼容读取）；strategies 为勾选的监控策略名集合（空=该类型全部策略）。
    其余维度语义不变（维度间 AND、维度内 OR、空=不限）。
    """
    cats = rule_categories(match_json)  # categories 多值 ∪ legacy 单值回退（与索引同源）
    if cats and alert.get("category") not in cats:
        return False
    sevs = match_json.get("severities") or []
    if sevs and alert.get("severity") not in sevs:
        return False
    strategies = [s for s in (match_json.get("strategies") or []) if s]
    if strategies and alert.get("strategy_name") not in strategies:
        return False
    appids = match_json.get("appids") or []
    if appids and (alert.get("app_id") or "") not in appids:
        return False
    selectors = match_json.get("label_selectors") or {}
    labels = alert.get("labels") or {}
    for k, v in selectors.items():
        if str(labels.get(k, "")) != str(v):
            return False
    keywords = [k for k in (match_json.get("keywords") or []) if k]
    if keywords:
        haystack = f"{alert.get('title', '')} {alert.get('description', '')}".lower()
        if not any(k.lower() in haystack for k in keywords):
            return False
    return True
