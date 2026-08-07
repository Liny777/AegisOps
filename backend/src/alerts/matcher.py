"""规则匹配与聚合键（纯函数，零 IO——匹配矩阵单测直接打这里）。

匹配语义（与 sre_alert_rule.match_json 注释一致）：维度间 AND、维度内 OR、
空数组/缺省 = 该维度不限。keyword 匹配 title+description 拼接串（不区分大小写），
keyword_mode 恒 any（all 语义 Phase2 自定义编辑器再开）。
"""
from __future__ import annotations

import hashlib
from typing import Any

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


def match(alert: dict[str, Any], match_json: dict[str, Any]) -> bool:
    """alert 为 ingest 规范化后的形态：category/severity/strategy_name/app_id/labels/title/description。

    v2 规则形态：categories 多值（2026-08-07 弹窗改多选，任一命中即过；存量单值 category
    兼容读取）；strategies 为勾选的监控策略名集合（空=该类型全部策略）。
    其余维度语义不变（维度间 AND、维度内 OR、空=不限）。
    """
    cats = [c for c in (match_json.get("categories") or []) if c]
    if not cats:
        legacy = str(match_json.get("category") or "")  # 存量单值规则
        cats = [legacy] if legacy else []
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
