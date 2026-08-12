"""alerts 切片应用层：规则/订阅 CRUD、alert 域热更新配置、清单投影。

owner 校验口径对齐 agent_team_service（NOT_FOUND / FORBIDDEN）；写操作幂等走
infra.idempotency；配置更新照 runtime_config_service 的「整批校验 + reason 必填 + 审计」。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from alerts import repository as repo
from alerts.rule_templates import DEFAULT_RULE_PROMPT, RULE_TEMPLATES
from alerts.schemas import (
    AlertConfigUpdateRequest,
    BatchRulesRequest,
    CreateRuleRequest,
    DeleteRuleRequest,
    SubscriptionUpdateRequest,
    UpdateRuleRequest,
)
from domain.errors import ApiError, Err
from infra import idempotency
from infra.db import row_json
from infra.external import alert_platform_client
from infra.external.alert_platform_client import AlertPlatformError
from infra.repositories import agent_teams, audit, runs, runtime_config

log = logging.getLogger("openops.alerts.service")

DOMAIN_ALERT = "alert"
SEVERITIES = ("fatal", "critical", "warning", "info")
SEVERITY_ORDER = list(SEVERITIES)  # 下标越小越严重（队列优先级/溢出踢除共用）

# ---- alert 域热更新配置（种子只补缺失键；数值边界防手滑打挂系统） ----
CONFIG_DEFAULTS: dict[str, Any] = {
    "alert_enabled": True,                       # 全局热停开关（env 决定启动，DB 决定热停）
    "alert_max_concurrent_diagnosis": 2,         # 诊断并发闸（模型峰值并发 = 本值 ×(1+max_children)）
    "alert_queue_max": 50,                       # 全局 queued 上限，溢出按低 severity 丢 skipped
    "alert_max_open_incidents_per_instance": 10, # 实例未完结上限（订阅行可覆盖）
    "alert_dedup_window_s": 300,                 # 同指纹合并窗口
    "alert_group_cooldown_s": 900,               # 同 group_key 完成后的冷却
    "alert_diagnosis_timeout_s": 900,            # 单次诊断超时 → 取消 → failed
    "alert_max_retries": 1,                      # 起 run 失败/中断的重试上限
    "alert_retry_backoff_s": 60,                 # 重试退避
    "alert_requeue_max_age_s": 3600,             # 重启 requeue 的陈旧上限
    "alert_run_idle_ttl_minutes": 0,             # 0=跟随平台 run_idle_ttl；>0 提前关告警 run
    "alert_pull_batch_limit": 200,               # 单批拉取上限
}

_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "alert_max_concurrent_diagnosis": (1, 16),
    "alert_queue_max": (1, 1000),
    "alert_max_open_incidents_per_instance": (1, 100),
    "alert_dedup_window_s": (0, 86400),
    "alert_group_cooldown_s": (0, 86400),
    "alert_diagnosis_timeout_s": (60, 7200),
    "alert_max_retries": (0, 5),
    "alert_retry_backoff_s": (1, 3600),
    "alert_requeue_max_age_s": (60, 604800),
    "alert_run_idle_ttl_minutes": (0, 1440),
    "alert_pull_batch_limit": (1, 1000),
}


def _check_config(key: str, value: Any) -> Any:
    if key == "alert_enabled":
        if not isinstance(value, bool):
            raise ApiError(Err.VALIDATION_FAILED, f"{key} 必须是布尔值")
        return value
    if key in _INT_BOUNDS:
        lo, hi = _INT_BOUNDS[key]
        if not isinstance(value, int) or isinstance(value, bool) or not (lo <= value <= hi):
            raise ApiError(Err.VALIDATION_FAILED, f"{key} 必须是 [{lo},{hi}] 内的整数")
        return value
    raise ApiError(Err.VALIDATION_FAILED, f"未知配置键 {key}")


async def ensure_config_defaults() -> None:
    have = {r["config_key"] for r in await runtime_config.get_domain(DOMAIN_ALERT)}
    for key, value in CONFIG_DEFAULTS.items():
        if key not in have:
            await runtime_config.upsert(DOMAIN_ALERT, key, value,
                                        description="7x24 告警接管运行旋钮", reason="seed", by="system")


async def get_config() -> dict[str, Any]:
    """默认值 + DB 覆盖（读侧永远给全量键，管理台/循环都吃这份）。"""
    merged = dict(CONFIG_DEFAULTS)
    for row in await runtime_config.get_domain(DOMAIN_ALERT):
        key = row["config_key"]
        if key in merged:
            merged[key] = row["config_value_json"]
    return merged


async def update_config(admin: dict[str, Any], req: AlertConfigUpdateRequest) -> dict[str, Any]:
    checked = {k: _check_config(k, v) for k, v in req.updates.items()}  # 先整批校验再落库
    for key, value in checked.items():
        await runtime_config.upsert(DOMAIN_ALERT, key, value, reason=req.reason,
                                    by=admin["user_id"])
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "openops:alert-config")),
        event_type="alert.config_updated",
        user_id=admin["user_id"], action="update", actor_type="user",
        payload_redacted={"updates": checked, "reason": req.reason})
    return await get_config()


# ============================ 规则 / 订阅 ============================


def _owner_instance(row: dict[str, Any] | None, user_id: str) -> dict[str, Any]:
    if row is None:
        raise ApiError(Err.NOT_FOUND, "Agent 实例不存在")
    if row["owner_user_id"] != user_id:
        raise ApiError(Err.FORBIDDEN, "无权操作该 Agent 实例")
    return row


async def _require_takeover_grant(user: dict[str, Any]) -> None:
    """告警接管功能白名单闸（2026-08-09 算力保护）：写路径（订阅/规则/预览）统一入口。

    fail-closed：名单空 = 全员关闭；platform_admin 天然豁免（管理员开名单前得能自测）。
    读路径（清单/规则列表）不拦——名单外用户自然无数据，前端靠 /alerts/access 显示引导。
    """
    if user.get("role") == "platform_admin":
        return
    if not await repo.is_user_granted(user["user_id"]):
        raise ApiError(Err.FORBIDDEN,
                       "告警接管功能未开通：算力资源有限按需开放，请联系平台管理员开通")


UI_SEVERITIES = ("fatal", "critical", "warning")  # 本期口径三档（info 保留在 schema 不出 UI）


def _check_ui_severities(severities: list[str]) -> list[str]:
    for s in severities:
        if s not in UI_SEVERITIES:
            raise ApiError(Err.VALIDATION_FAILED,
                           f"非法严重度 {s}（本期可选：{'/'.join(UI_SEVERITIES)}）")
    return list(severities)


def _check_categories(categories: list[str]) -> list[str]:
    """类型多选：去空白、去重保序；剩空即 400（pydantic 只保证列表非空，挡不住全空串）。"""
    out: list[str] = []
    for c in categories:
        c = (c or "").strip()
        if c and c not in out:
            out.append(c)
    if not out:
        raise ApiError(Err.VALIDATION_FAILED, "策略类型至少选择一个")
    return out


def _rule_categories(match: dict[str, Any]) -> list[str]:
    """读侧统一出口：新形态 categories[] 优先，存量单值 category 兼容折装为单元素列表。"""
    cats = [c for c in (match.get("categories") or []) if c]
    if cats:
        return cats
    legacy = str(match.get("category") or "")
    return [legacy] if legacy else []


def rule_out(row: dict[str, Any]) -> dict[str, Any]:
    match = row.get("match_json") or {}
    return {
        "rule_id": str(row["alert_rule_id"]),
        "agent_team_instance_id": str(row["agent_team_instance_id"]),
        "name": row["rule_name"],
        "description": row.get("description") or "",
        "categories": _rule_categories(match),
        "enabled": bool(row["enabled"]),
        "source": row.get("rule_source") or "custom",
        "severities": match.get("severities") or [],
        "strategies": match.get("strategies") or [],
        "prompt": row.get("prompt") or "",
        "app_ids": match.get("appids") or [],
        "keywords": match.get("keywords") or [],
        "label_selectors": match.get("label_selectors") or {},
        "updated_at": row_json(row).get("last_update_date"),
    }


async def list_rules(user: dict[str, Any], instance_id: str) -> dict[str, Any]:
    _owner_instance(await agent_teams.get_instance(instance_id), user["user_id"])
    rows = await repo.list_rules(instance_id)
    return {"rules": [rule_out(r) for r in rows]}


async def create_rule(user: dict[str, Any], req: CreateRuleRequest) -> dict[str, Any]:
    await _require_takeover_grant(user)
    uid = user["user_id"]
    inst = _owner_instance(await agent_teams.get_instance(req.agent_team_instance_id), uid)
    cached = await idempotency.begin(uid, "alert_rule_create", req.client_request_id,
                                     req.model_dump(exclude={"client_request_id"}))
    if cached is not None:
        return cached
    try:
        if await repo.rule_name_exists(req.agent_team_instance_id, req.name):
            raise ApiError(Err.VALIDATION_FAILED, f"规则名「{req.name}」已存在")
        match = {
            "categories": _check_categories(req.categories),
            "severities": _check_ui_severities(req.severities),
            "strategies": [s for s in req.strategies if s],
            "appids": [], "label_selectors": {}, "keywords": [], "keyword_mode": "any",
        }
        rid = await repo.create_rule(
            instance_id=req.agent_team_instance_id, owner=uid, name=req.name,
            description=req.description, source=req.source, match=match,
            prompt=req.prompt.strip(), enabled=req.enabled, by=uid)
        await audit.insert_event(
            audit_trace_id=str(inst["agent_team_instance_id"]), event_type="alert.rule_created",
            user_id=uid, instance_id=req.agent_team_instance_id, action="create", actor_type="user",
            payload_redacted={"rule_name": req.name, "categories": req.categories,
                              "strategies": len(req.strategies)})
        row = await repo.get_rule(rid)
        result = {"rule": rule_out(row)} if row else {"rule": None}
        return await idempotency.commit(uid, "alert_rule_create", req.client_request_id, result)
    except Exception:
        await idempotency.rollback(uid, "alert_rule_create", req.client_request_id)
        raise


async def batch_rules(user: dict[str, Any], req: BatchRulesRequest) -> dict[str, Any]:
    """批量启用/禁用/删除（配置列表复选框）。全部行须归属本人实例，任一越权整批拒绝。"""
    await _require_takeover_grant(user)
    uid = user["user_id"]
    rows = await repo.list_rules_by_ids(req.rule_ids)
    if len(rows) != len(set(req.rule_ids)):
        raise ApiError(Err.NOT_FOUND, "部分规则不存在或已删除")
    for row in rows:
        if row["owner_user_id"] != uid:
            raise ApiError(Err.FORBIDDEN, "无权操作他人实例的规则")
    if req.action == "delete":
        updated = await repo.soft_delete_rules(req.rule_ids, uid)
    else:
        updated = await repo.set_rules_enabled(req.rule_ids, req.action == "enable", uid)
    await audit.insert_event(
        audit_trace_id=str(rows[0]["agent_team_instance_id"]), event_type="alert.rules_batch",
        user_id=uid, instance_id=str(rows[0]["agent_team_instance_id"]), action=req.action,
        actor_type="user", payload_redacted={"count": updated, "action": req.action})
    return {"updated": updated}


async def _owned_rule(user: dict[str, Any], rule_id: str) -> dict[str, Any]:
    rule = await repo.get_rule(rule_id)
    if rule is None:
        raise ApiError(Err.NOT_FOUND, "规则不存在")
    _owner_instance(await agent_teams.get_instance(str(rule["agent_team_instance_id"])),
                    user["user_id"])
    return rule


async def update_rule(user: dict[str, Any], rule_id: str, req: UpdateRuleRequest) -> dict[str, Any]:
    await _require_takeover_grant(user)
    rule = await _owned_rule(user, rule_id)
    instance_id = str(rule["agent_team_instance_id"])
    match = dict(rule.get("match_json") or {})
    name = req.name if req.name is not None else rule["rule_name"]
    if req.name is not None and await repo.rule_name_exists(instance_id, req.name, rule_id):
        raise ApiError(Err.VALIDATION_FAILED, f"规则名「{req.name}」已存在")
    if req.severities is not None:
        match["severities"] = _check_ui_severities(req.severities)
    if req.categories is not None:
        match["categories"] = _check_categories(req.categories)
        match.pop("category", None)  # 存量单值形态就地升级，避免双键并存歧义
    if req.strategies is not None:
        match["strategies"] = [s for s in req.strategies if s]
    if req.app_ids is not None:
        match["appids"] = [a for a in req.app_ids if a]
    if req.keywords is not None:
        match["keywords"] = [k for k in req.keywords if k]
    if req.label_selectors is not None:
        match["label_selectors"] = dict(req.label_selectors)
    match.setdefault("keyword_mode", "any")
    await repo.update_rule(
        rule_id, name=name,
        description=req.description if req.description is not None else (rule.get("description") or ""),
        match=match,
        prompt=req.prompt.strip() if req.prompt is not None else (rule.get("prompt") or ""),
        enabled=req.enabled if req.enabled is not None else bool(rule["enabled"]),
        by=user["user_id"])
    await audit.insert_event(
        audit_trace_id=instance_id, event_type="alert.rule_updated", user_id=user["user_id"],
        instance_id=instance_id, action="update", actor_type="user",
        payload_redacted={"rule_id": rule_id,
                          "fields": [k for k, v in req.model_dump().items()
                                     if k != "client_request_id" and v is not None]})
    refreshed = await repo.get_rule(rule_id)
    return rule_out(refreshed or rule)


async def delete_rule(user: dict[str, Any], rule_id: str, _req: DeleteRuleRequest) -> dict[str, Any]:
    await _require_takeover_grant(user)
    rule = await _owned_rule(user, rule_id)
    instance_id = str(rule["agent_team_instance_id"])
    await repo.soft_delete_rule(rule_id, user["user_id"])
    await audit.insert_event(
        audit_trace_id=instance_id, event_type="alert.rule_deleted", user_id=user["user_id"],
        instance_id=instance_id, action="delete", actor_type="user",
        payload_redacted={"rule_id": rule_id, "rule_name": rule["rule_name"]})
    return {"deleted": True}


async def get_subscription(user: dict[str, Any], instance_id: str) -> dict[str, Any]:
    _owner_instance(await agent_teams.get_instance(instance_id), user["user_id"])
    row = await repo.get_subscription(instance_id)
    if row is None:
        return {"agent_team_instance_id": instance_id, "enabled": False,
                "max_open_incidents": None}  # 无行 = 未开通接管
    return {"agent_team_instance_id": instance_id, "enabled": bool(row["enabled"]),
            "max_open_incidents": row.get("max_open_incidents")}


async def update_subscription(user: dict[str, Any], req: SubscriptionUpdateRequest) -> dict[str, Any]:
    await _require_takeover_grant(user)
    uid = user["user_id"]
    _owner_instance(await agent_teams.get_instance(req.agent_team_instance_id), uid)
    await repo.upsert_subscription(
        instance_id=req.agent_team_instance_id, owner=uid, enabled=req.enabled,
        max_open_incidents=req.max_open_incidents, by=uid)
    await audit.insert_event(
        audit_trace_id=req.agent_team_instance_id, event_type="alert.subscription_updated",
        user_id=uid, instance_id=req.agent_team_instance_id, action="update", actor_type="user",
        payload_redacted={"enabled": req.enabled, "max_open_incidents": req.max_open_incidents})
    return await get_subscription(user, req.agent_team_instance_id)


def rule_templates() -> dict[str, Any]:
    """配置弹窗数据源：三类模板 + UI 三档级别 + 默认提示词（用户可改后存 rule.prompt）。"""
    return {"templates": RULE_TEMPLATES, "severities": list(UI_SEVERITIES),
            "default_prompt": DEFAULT_RULE_PROMPT}


# ============================ 接管清单（incidents） ============================

INCIDENT_STATES = ("queued", "diagnosing", "completed", "failed", "skipped", "ignored")

_SKIP_REASON_TEXT = {
    "overflow_instance": "实例未完结聚合单已达上限，本条溢出丢弃（留痕）",
    "overflow_global": "全局待诊队列已满，本条溢出丢弃（留痕）",
    "overflow_evicted": "队列满时被更高严重度告警挤出",
    "cooldown": "同组冷却期内不重复诊断",
    "timeout": "诊断超时被取消",
    "interrupted_by_restart": "服务重启中断且超出重试预算",
    "stale": "重启后发现告警已陈旧，不再补诊",
    "no_scope": "无可用范围快照（实例从未成功解析过 scope）",
    "disabled": "接管开关已关闭",
}


def _duration_s(row: dict[str, Any]) -> int | None:
    start, end = row.get("first_alert_at"), row.get("ended_at")
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds()))


def incident_out(row: dict[str, Any]) -> dict[str, Any]:
    from alerts import matcher

    matched = row.get("matched_rules_json") or []
    j = row_json(row)
    return {
        "incident_id": str(row["alert_incident_id"]),
        "instance_id": str(row["agent_team_instance_id"]),
        "category": row.get("category") or "",
        "title": row.get("title") or "",
        "severity": row.get("severity") or "warning",
        "status": row.get("incident_state"),
        "state_reason": row.get("state_reason"),
        "agent_result": row.get("agent_result"),
        "user_feedback": row.get("user_feedback"),
        "feedback_note": row.get("feedback_note") or "",
        "app_id": matcher.app_id_from_group_key(str(row.get("group_key") or "")),
        "alert_count": int(row.get("alert_count") or 1),
        "matched_rule": ({"rule_id": matched[0].get("rule_id"), "name": matched[0].get("rule_name")}
                         if matched else None),
        "run_id": str(row["agent_run_id"]) if row.get("agent_run_id") else None,
        "queue_position": None,  # 列表不算位次（省查询）；详情里给
        "result_summary": row.get("result_summary"),
        "retry_count": int(row.get("retry_count") or 0),
        "started_at": j.get("first_alert_at"),
        "diagnosis_started_at": j.get("started_at"),
        "ended_at": j.get("ended_at"),
        "duration_s": _duration_s(row),
        "updated_at": j.get("last_update_date"),
    }


def _parse_states(states: str | None) -> list[str] | None:
    if not states:
        return None
    out = [s.strip() for s in states.split(",") if s.strip()]
    for s in out:
        if s not in INCIDENT_STATES:
            raise ApiError(Err.VALIDATION_FAILED, f"非法状态 {s}")
    return out or None


def _parse_severities(severities: str | None) -> list[str] | None:
    if not severities:
        return None
    out = [s.strip() for s in severities.split(",") if s.strip()]
    for s in out:
        if s not in SEVERITIES:
            raise ApiError(Err.VALIDATION_FAILED, f"非法严重度 {s}")
    return out or None


async def _instance_names(rows: list[dict[str, Any]]) -> dict[str, str]:
    """页内 distinct 实例名（清单/详情展示用；实例已删时留空）。"""
    names: dict[str, str] = {}
    for iid in {str(r["agent_team_instance_id"]) for r in rows}:
        inst = await agent_teams.get_instance(iid)
        names[iid] = str(inst["instance_name"]) if inst else ""
    return names


async def list_incidents(user: dict[str, Any], *, instance_id: str | None, states: str | None,
                         severities: str | None, search: str | None, page: int,
                         page_size: int) -> dict[str, Any]:
    if instance_id:
        _owner_instance(await agent_teams.get_instance(instance_id), user["user_id"])
    rows, total = await repo.list_incidents(
        owner=user["user_id"], instance_id=instance_id, states=_parse_states(states),
        severities=_parse_severities(severities), search=search, page=page, page_size=page_size)
    names = await _instance_names(rows)
    items = []
    for r in rows:
        out = incident_out(r)
        out["instance_name"] = names.get(str(r["agent_team_instance_id"]), "")
        items.append(out)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def _owned_incident(user: dict[str, Any], incident_id: str) -> dict[str, Any]:
    row = await repo.get_incident(incident_id)
    if row is None:
        raise ApiError(Err.NOT_FOUND, "聚合单不存在")
    if row["owner_user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权查看该聚合单")
    return row


def _timeline(row: dict[str, Any]) -> list[dict[str, Any]]:
    j = row_json(row)
    steps: list[dict[str, Any]] = []
    matched = row.get("matched_rules_json") or []
    rule_name = matched[0].get("rule_name") if matched else ""
    steps.append({"at": j.get("creation_date"),
                  "event": f"命中规则「{rule_name}」入队" if rule_name else "命中订阅规则入队"})
    if j.get("started_at"):
        steps.append({"at": j.get("started_at"), "event": "创建诊断会话，开始自动诊断"})
    state = row.get("incident_state")
    if j.get("ended_at"):
        label = {"completed": "诊断完成", "failed": "诊断失败", "skipped": "溢出/冷却丢弃",
                 "ignored": "人工忽略"}.get(state, state)
        reason = _SKIP_REASON_TEXT.get(str(row.get("state_reason") or ""), "")
        steps.append({"at": j.get("ended_at"), "event": f"{label}{('：' + reason) if reason else ''}"})
    return steps


def _event_out(e: dict[str, Any]) -> dict[str, Any]:
    j = row_json(e)
    return {
        "alert_id": e.get("external_alert_id") or str(e["alert_event_id"]),
        "title": e.get("alert_name") or "",
        "status": e.get("alert_status"),
        "severity": e.get("severity"),
        "seen_count": int(e.get("seen_count") or 1),
        "started_at": j.get("starts_at") or j.get("first_seen_at"),
        "resolved_at": j.get("last_seen_at") if e.get("alert_status") == "resolved" else None,
    }


async def get_incident_detail(user: dict[str, Any], incident_id: str) -> dict[str, Any]:
    row = await _owned_incident(user, incident_id)
    out = incident_out(row)
    out["instance_name"] = (await _instance_names([row])).get(str(row["agent_team_instance_id"]), "")
    events, events_total = await repo.list_incident_events(incident_id, limit=50)
    out["alerts"] = [_event_out(e) for e in events]
    out["alerts_total"] = events_total
    latest = events[0] if events else None
    out["fingerprint"] = (latest or {}).get("fingerprint") or ""
    out["labels"] = (latest or {}).get("labels_json") or {}
    out["annotations"] = (latest or {}).get("annotations_json") or {}
    out["timeline"] = _timeline(row)
    state = row.get("incident_state")
    if state in ("failed", "skipped"):
        reason = str(row.get("state_reason") or "")
        out["error"] = {"code": reason or state,
                        "message": _SKIP_REASON_TEXT.get(reason, row.get("result_summary") or reason)}
    else:
        out["error"] = None
    if state == "queued":
        out["queue_position"] = await repo.queue_position(row, list(SEVERITY_ORDER))
    return out


async def ignore_incident(user: dict[str, Any], incident_id: str) -> dict[str, Any]:
    row = await _owned_incident(user, incident_id)
    n = await repo.transition(incident_id, from_states=["queued"], to_state="ignored",
                              state_reason="ignored_by_user", by=user["user_id"], set_ended=True)
    if not n:
        raise ApiError(Err.ALERT_INCIDENT_STATE_INVALID,
                       f"仅排队中的聚合单可忽略（当前 {row.get('incident_state')}）")
    await audit.insert_event(
        audit_trace_id=str(row["agent_team_instance_id"]), event_type="alert.incident_ignored",
        user_id=user["user_id"], instance_id=str(row["agent_team_instance_id"]),
        action="ignore", actor_type="user", payload_redacted={"incident_id": incident_id})
    return incident_out(await repo.get_incident(incident_id) or row)


async def retry_incident(user: dict[str, Any], incident_id: str) -> dict[str, Any]:
    row = await _owned_incident(user, incident_id)
    if row.get("incident_state") not in ("failed", "skipped", "ignored"):
        raise ApiError(Err.ALERT_INCIDENT_STATE_INVALID,
                       f"仅失败/跳过/已忽略的聚合单可重新入队（当前 {row.get('incident_state')}）")
    await repo.requeue(incident_id, bump_retry=False, by=user["user_id"])
    await audit.insert_event(
        audit_trace_id=str(row["agent_team_instance_id"]), event_type="alert.incident_retried",
        user_id=user["user_id"], instance_id=str(row["agent_team_instance_id"]),
        action="retry", actor_type="user", payload_redacted={"incident_id": incident_id})
    try:
        from alerts import dispatcher

        dispatcher.kick()
    except Exception:  # noqa: BLE001 —— B5 前 dispatcher 不在场；短询兜底
        pass
    return incident_out(await repo.get_incident(incident_id) or row)


async def feedback_incident(user: dict[str, Any], incident_id: str, *, feedback: str,
                            note: str) -> dict[str, Any]:
    row = await _owned_incident(user, incident_id)
    await repo.set_feedback(incident_id, feedback=feedback, note=note, by=user["user_id"])
    await audit.insert_event(
        audit_trace_id=str(row["agent_team_instance_id"]), event_type="alert.incident_feedback",
        user_id=user["user_id"], instance_id=str(row["agent_team_instance_id"]),
        action="feedback", decision=feedback, actor_type="user",
        payload_redacted={"incident_id": incident_id, "note": note[:200]})
    return incident_out(await repo.get_incident(incident_id) or row)


# ============================ 事件清单（全量告警视角，v2 需求 1.3） ============================

EVENT_STATUSES = ("closed", "assigned", "unassigned")
TAKEOVER_STATUSES = ("done", "processing", "none")


async def _viewer_scope_appids(uid: str, instance_id: str | None) -> list[str]:
    """普通用户「未接管告警」可见范围：所选实例（或本人全部实例）最近 scope 快照的 appids 并集。
    从未成功 resolve 过的实例贡献空集（其视角看不到未接管行——计划内决策）。"""
    if instance_id:
        instances = [instance_id]
    else:
        rows = await agent_teams.list_by_owner(uid)
        instances = [str(r["agent_team_instance_id"]) for r in rows]
    appids: set[str] = set()
    for iid in instances:
        snap = await runs.latest_scope_snapshot_by_instance(iid)
        if snap:
            appids.update(a for a in (snap.get("effective_appids_snapshot") or []) if a)
    return sorted(appids)


def _takeover_of(inc_state: Any) -> str:
    if not inc_state or inc_state in ("skipped", "ignored"):
        return "none"  # 留痕单不算接管（与「未分派」口径联动）
    if inc_state in ("queued", "diagnosing"):
        return "processing"
    return "done"  # completed / failed（结果列区分 已恢复/失败）


def _alarm_urls() -> tuple[str, str]:
    """告警平台跳转根（用户指定裸名 env，2026-08-10）：OP=32 个 8 企业，KWE=32 个 1 企业。"""
    import os

    return (os.environ.get("ALARM_OP_URL", "").strip(),
            os.environ.get("ALARM_KWE_URL", "").strip())


def event_row_out(row: dict[str, Any], viewer_uid: str) -> dict[str, Any]:
    from infra.external.alert_inet_contract import alarm_detail_url

    j = row_json(row)
    annotations = row.get("annotations_json") or {}
    labels = row.get("labels_json") or {}
    enterprise_id = str(labels.get("enterprise_id") or "")
    detail_url = str(annotations.get("detail_url") or "")
    if not detail_url and enterprise_id:  # 内网体无外链：按企业分发拼（env 未配→仍空，编号退纯文本）
        op_url, kwe_url = _alarm_urls()
        detail_url = alarm_detail_url(enterprise_id, str(row.get("external_alert_id") or ""),
                                      op_url=op_url, kwe_url=kwe_url)
    takeover = _takeover_of(row.get("inc_state"))
    resolved = row.get("alert_status") == "resolved"
    alert_status = "closed" if resolved else ("assigned" if takeover != "none" else "unassigned")
    start_dt = row.get("starts_at") or row.get("first_seen_at")
    end_dt = row.get("last_seen_at") if resolved else None
    duration_s = max(0, int((end_dt - start_dt).total_seconds())) \
        if (start_dt is not None and end_dt is not None) else None
    run_id = str(row["inc_run_id"]) if row.get("inc_run_id") else None
    return {
        "alert_no": row.get("external_alert_id") or str(row["alert_event_id"]),
        "category": row.get("category") or "",
        "alert_object": row.get("alert_object") or "",
        "appid": row.get("appid") or "",
        "title": row.get("alert_name") or "",
        "description": str(annotations.get("description") or ""),
        "alert_status": alert_status,
        "severity": row.get("severity") or "warning",
        "takeover_status": takeover,
        "agent_result": row.get("inc_agent_result") if takeover == "done" else (
            "processing" if takeover == "processing" else None),
        "user_feedback": row.get("inc_user_feedback"),
        "feedback_note": row.get("inc_feedback_note") or "",
        "detail_url": detail_url,
        "enterprise_id": enterprise_id,
        "incident_id": str(row["inc_id"]) if row.get("inc_id") else None,
        "run_id": run_id,
        "run_clickable": bool(run_id) and row.get("inc_owner") == viewer_uid,
        "seen_count": int(row.get("seen_count") or 1),
        "started_at": j.get("starts_at") or j.get("first_seen_at"),
        "ended_at": j.get("last_seen_at") if resolved else None,
        "duration_s": duration_s,
    }


def _check_enum(value: str | None, allowed: tuple[str, ...], label: str) -> str | None:
    if value and value not in allowed:
        raise ApiError(Err.VALIDATION_FAILED, f"非法{label} {value}")
    return value or None


async def list_alert_events(user: dict[str, Any], *, instance_id: str | None,
                            alert_status: str | None, severities: str | None,
                            takeover: str | None, category: str | None, search: str | None,
                            since_days: int | None = None,
                            page: int, page_size: int) -> dict[str, Any]:
    uid = user["user_id"]
    if instance_id:
        _owner_instance(await agent_teams.get_instance(instance_id), uid)
    # 2026-07-31 拍板：未命中规则的告警**不进用户清单**（丢弃；事件仍落库 30 天）。
    # 空 scope 列表 = 只看本人接管过的单；仅弹窗预览（since_days）保留旧口径
    # 「appid∈该 Agent scope 快照的未命中告警」——预览的存在意义就是看订阅面。
    rows, total = await repo.list_events(
        lateral_owner=uid, lateral_instance=instance_id,
        scope_appids=(await _viewer_scope_appids(uid, instance_id)) if since_days else [],
        alert_status=_check_enum(alert_status, EVENT_STATUSES, "告警状态"),
        severities=_parse_severities(severities),
        takeover=_check_enum(takeover, TAKEOVER_STATUSES, "接管状态"),
        category=category or None, search=search or None, since_days=since_days,
        page=page, page_size=page_size)
    return {"items": [event_row_out(r, uid) for r in rows], "total": total,
            "page": page, "page_size": page_size}


async def _local_preview(uid: str, instance_id: str, appids: list[str], cats: list[str],
                         sevs: list[str], since_days: int, page_size: int) -> dict[str, Any]:
    """预览的本地降级面（只剩已落库事件）：平台不可用与拿不到 projectIds 两处共用。"""
    rows, total = await repo.list_events(
        lateral_owner=uid, lateral_instance=instance_id, scope_appids=appids,
        alert_status=None, severities=sevs, takeover=None,
        category=None, categories=cats, search=None,
        since_days=since_days, page=1, page_size=page_size)
    return {"items": [event_row_out(r, uid) for r in rows], "total": total,
            "page": 1, "page_size": page_size, "source": "local_fallback"}


async def history_preview(user: dict[str, Any], *, instance_id: str, categories: str,
                          severities: str | None, since_days: int,
                          page_size: int) -> dict[str, Any]:
    """规则编辑器第二步预览：主路径=平台历史接口（29.10 alarm_list，真全量历史，
    未命中规则的告警也可见），平台不可用时降级本地事件库（只剩已落库面）。

    响应恒带 source ∈ platform|local_fallback，前端据此提示数据来源。
    projectIds 走 **omodel 实时探询**（scope_service.peek_effective_appids：override 缝
    → 30s 缓存 → resolve；2026-08-10 内网实证对端「应用/产品/子产品/Hrn 四选一必填」，
    不能不传）——预览是用户在线操作，请求上下文有认证，实时链成立；快照口径
    （_viewer_scope_appids）只是任务边界的审计残影，新实例恒为空，弃用。
    **peek 拿不到范围时不打必败请求**，直接落本地降级。
    """
    from app import scope_service  # 局部导入：service 顶层不背 core app 依赖

    await _require_takeover_grant(user)
    uid = user["user_id"]
    inst = _owner_instance(await agent_teams.get_instance(instance_id), uid)
    cats = _check_categories([c for c in categories.split(",")])
    sevs = _parse_severities(severities) or list(UI_SEVERITIES)
    appids = await scope_service.peek_effective_appids(uid, inst)
    if not appids:
        log.info("[alerts][preview] 实例 %s 拿不到 effective_appids（omodel 不可达或空范围），"
                 "跳过平台查询直接本地降级", instance_id)
        return await _local_preview(uid, instance_id, appids, cats, sevs, since_days, page_size)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(since_days))
    try:
        page = await alert_platform_client.list_history(
            start=start, end=end, categories=cats, severities=sevs,
            project_ids=appids, page_no=1, page_size=page_size)
        return {"items": page["rows"], "total": page["total"],
                "page": 1, "page_size": page_size, "source": "platform"}
    except AlertPlatformError as e:
        log.warning("[alerts][preview] 平台历史接口不可用（%s: %s），回落本地事件库",
                    e.kind, e.message[:200])
        return await _local_preview(uid, instance_id, appids, cats, sevs, since_days, page_size)


async def takeover_access(user: dict[str, Any]) -> dict[str, Any]:
    """用户侧探询：告警接管功能对我是否开通（前端设置页/清单页据此渲染引导空态）。"""
    granted = user.get("role") == "platform_admin" or await repo.is_user_granted(user["user_id"])
    return {"granted": granted}


async def admin_list_grants(_admin: dict[str, Any]) -> dict[str, Any]:
    return {"items": [row_json(r) for r in await repo.list_user_grants()]}


async def admin_set_grant(admin: dict[str, Any], *, target_user_id: str,
                          granted: bool, client_request_id: str) -> dict[str, Any]:
    """开通/关闭某用户的告警接管（幂等；审计必落）。关闭即刻断流：匹配加载面 join 白名单。"""
    target = (target_user_id or "").strip()
    if not target:
        raise ApiError(Err.VALIDATION_FAILED, "user_id 不能为空")
    await repo.set_user_grant(target, granted, admin["user_id"])
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="alert.grant_updated",
        user_id=admin["user_id"], instance_id=None, action="grant" if granted else "revoke",
        actor_type="admin",
        payload_redacted={"target_user_id": target, "granted": granted,
                          "client_request_id": client_request_id})
    return {"user_id": target, "granted": granted}


async def admin_list_alert_events(admin: dict[str, Any], *, user_id: str | None,
                                  alert_status: str | None, severities: str | None,
                                  takeover: str | None, category: str | None,
                                  search: str | None, since_days: int | None = None,
                                  page: int, page_size: int) -> dict[str, Any]:
    """管理员全量视角：无可见性子句；user_id 非空=按该用户视角投影接管状态。"""
    rows, total = await repo.list_events(
        lateral_owner=user_id or None, lateral_instance=None, scope_appids=None,
        alert_status=_check_enum(alert_status, EVENT_STATUSES, "告警状态"),
        severities=_parse_severities(severities),
        takeover=_check_enum(takeover, TAKEOVER_STATUSES, "接管状态"),
        category=category or None, search=search or None, since_days=since_days,
        page=page, page_size=page_size)
    return {"items": [event_row_out(r, admin["user_id"]) for r in rows], "total": total,
            "page": page, "page_size": page_size}


async def admin_overview() -> dict[str, Any]:
    """管理台概览：队列水位、24h 丢弃、游标健康、Kafka 消费心跳、派发器活跃 worker 数。"""
    from alerts import dispatcher, kafka_source
    from alerts.ingest import SOURCE_KEY

    pull = await repo.get_pull_state(SOURCE_KEY)
    pull_out = None
    if pull is not None:
        j = row_json(pull)
        pull_out = {"cursor": (pull.get("cursor_json") or {}).get("cursor") or "",
                    "last_pull_at": j.get("last_pull_at"), "last_ok_at": j.get("last_ok_at"),
                    "last_status": pull.get("last_status"), "last_error": pull.get("last_error")}
    return {
        "counts": await repo.global_overview_counts(),
        "pull_state": pull_out,
        # Kafka 消费心跳（2026-08-11 观测补强）：real 档 pull_state 恒 null（那是 HTTP 游标
        # 链路的），消费死活以此为准——started_at 空=循环没起，last_batch_at 停走=消费停了
        "kafka": dict(kafka_source.health),
        "active_workers": sum(1 for w in getattr(dispatcher, "_workers", set()) if not w.done()),
        "config": await get_config(),
    }


async def summary(user: dict[str, Any], instance_id: str | None) -> dict[str, Any]:
    if instance_id:
        _owner_instance(await agent_teams.get_instance(instance_id), user["user_id"])
    counts = await repo.summary_counts(owner=user["user_id"], instance_id=instance_id)
    counts["latest_change_seq"] = await repo.latest_change_seq(
        owner=user["user_id"], instance_id=instance_id)
    counts["instance_id"] = instance_id
    return counts
