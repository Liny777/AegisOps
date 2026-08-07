"""摄入管线：拉取 → 指纹去重 → 内存匹配 → 附着式聚合/上限/冷却 → incident(queued)。

单轮入口 `run_once()` 供 pytest 与管理端 `POST /admin/alerts:pull` 直调（对齐
asset_reconcile 的 reconcile() 可直调先例）；后台循环由 dispatcher.start_background
注册 poller 反复调它。整轮失败写审计 + pull_state，绝不让异常杀死循环。

削峰分层（与设计文档编号一致）：
① 拉取批量背压（单批 alert_pull_batch_limit、单轮 ≤5 批）
② fingerprint 去重（dedup_window_s 内同指纹只 seen_count++，不触发匹配）
③ 附着式聚合（group_key 有未完结 incident → 附着不新建）
④ 组冷却（group_cooldown_s 内同组不建新单，event 留痕）
⑤ 有界队列 + 溢出丢弃（实例/全局上限 → skipped 留痕；高 severity 可踢队尾低者）
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from alerts import matcher
from alerts import repository as repo
from alerts import service
from infra.external import alert_platform_client
from infra.external.alert_platform_client import AlertPlatformError
from infra.repositories import audit

log = logging.getLogger("openops.alerts.ingest")

SOURCE_KEY = "default"
_MAX_BATCHES_PER_CYCLE = 5
_AUDIT_TRACE = str(uuid.uuid5(uuid.NAMESPACE_URL, "openops:alert-ingest"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_s(ts: Any) -> float:
    if ts is None:
        return float("inf")
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            return float("inf")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (_now() - ts).total_seconds()


def _normalize(alert: dict[str, Any]) -> dict[str, Any]:
    """对端 AlertDTO → 内部规范形态（截断/枚举归一，原始 severity 进 labels 备查）。"""
    title = str(alert.get("title") or "未命名告警")[:512]
    severity, changed = matcher.normalize_severity(str(alert.get("severity") or ""))
    labels = {}
    for k, v in list((alert.get("labels") or {}).items())[:64]:
        labels[str(k)[:128]] = str(v)[:1000]
    if changed and alert.get("severity"):
        labels["_raw_severity"] = str(alert["severity"])[:64]
    annotations = {str(k)[:128]: str(v)[:2000]
                   for k, v in list((alert.get("annotations") or {}).items())[:32]}
    description = str(alert.get("description") or "")[:8000]
    if description:
        annotations.setdefault("description", description)
    if alert.get("detail_url"):  # 告警平台详情页（清单编号外跳目标）：不建列，随 annotations 走
        annotations.setdefault("detail_url", str(alert["detail_url"])[:1000])
    status = str(alert.get("status") or "firing")
    return {
        "external_alert_id": str(alert.get("alert_id") or ""),
        "alert_object": str(alert.get("alert_object") or "")[:255],
        "strategy_name": str(alert.get("strategy_name") or "")[:255],
        "fingerprint": str(alert.get("fingerprint") or "")
                       or matcher.fingerprint_fallback(str(alert.get("source") or SOURCE_KEY),
                                                       title, labels),
        "status": status if status in ("firing", "resolved") else "firing",
        "severity": severity,
        "category": str(alert.get("category") or "")[:64],
        "title": title,
        "description": description,
        "app_id": str(alert.get("app_id") or "") or None,
        "labels": labels,
        "annotations": annotations,
        "started_at": alert.get("started_at"),
    }


def _new_counters() -> dict[str, Any]:
    return {"pulled": 0, "deduped": 0, "unmatched": 0, "attached": 0,
            "queued": 0, "skipped": 0, "cooldown": 0, "resolved": 0}


async def ingest_batch(alerts: list[dict[str, Any]], *, cfg: dict[str, Any] | None = None,
                       rules: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """一批 AlertDTO 走完整管线（传输无关入口：mock 轮询与 Kafka 消费共用）。

    单条坏数据只跳过不拖垮整批；批级异常（DB 不可用等）向上抛——Kafka 侧据此**不 commit**
    让消息重投（at-least-once + 指纹幂等 = 恰好一次效果）。
    """
    cfg = cfg if cfg is not None else await service.get_config()
    counters = _new_counters()
    if not cfg["alert_enabled"]:
        counters["disabled"] = True
        return counters
    rules = rules if rules is not None else await repo.list_enabled_rules()
    for alert in alerts:
        counters["pulled"] += 1
        try:
            await _ingest_alert(_normalize(alert), rules, cfg, counters)
        except Exception:  # noqa: BLE001 —— 单条坏数据只跳过，不拖垮整批
            log.warning("[alerts][ingest] 单条告警处理失败 alert_id=%s",
                        alert.get("alert_id"), exc_info=True)
    if counters["queued"]:
        try:  # dispatcher 不在场（单测局部跑）时静默——短询兜底会接住
            from alerts import dispatcher

            dispatcher.kick()
        except Exception:  # noqa: BLE001
            pass
    return counters


async def run_once() -> dict[str, Any]:
    """mock 档轮询单轮：拉增量（内存变更流+游标）→ ingest_batch；管理端 `:pull` 与测试共用。"""
    cfg = await service.get_config()
    counters = _new_counters()
    if not cfg["alert_enabled"]:
        counters["disabled"] = True
        return counters

    state = await repo.get_pull_state(SOURCE_KEY)
    cursor = str(((state or {}).get("cursor_json") or {}).get("cursor") or "")
    rules = await repo.list_enabled_rules()

    try:
        for _ in range(_MAX_BATCHES_PER_CYCLE):
            page = await alert_platform_client.list_changes(cursor, int(cfg["alert_pull_batch_limit"]))
            batch_counters = await ingest_batch(page["alerts"], cfg=cfg, rules=rules)
            for key, value in batch_counters.items():
                if isinstance(value, int):
                    counters[key] = counters.get(key, 0) + value
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
    except AlertPlatformError as e:
        if e.kind == "cursor_expired":
            earliest = str(e.detail.get("earliest_cursor") or "")
            await repo.save_pull_state(source_key=SOURCE_KEY, cursor=earliest, ok=False,
                                       error=f"cursor_expired→重对账 earliest={earliest[:24]}")
        else:
            await repo.save_pull_state(source_key=SOURCE_KEY, cursor=None, ok=False,
                                       error=f"{e.kind}: {e.message}")
        await audit.insert_event(
            audit_trace_id=_AUDIT_TRACE, event_type="alert.pull_failed", user_id="system",
            action="pull", reason_code=e.kind, payload_redacted={"error": e.message[:300]})
        counters["error"] = e.kind
        return counters

    await repo.save_pull_state(source_key=SOURCE_KEY, cursor=cursor, ok=True, error=None)
    return counters  # kick 已在 ingest_batch 内完成


async def _ingest_alert(alert: dict[str, Any], rules: list[dict[str, Any]],
                        cfg: dict[str, Any], counters: dict[str, Any]) -> None:
    existing = await repo.get_event_by_fingerprint(SOURCE_KEY, alert["fingerprint"])
    if existing is not None:
        within_window = _age_s(existing["last_seen_at"]) < float(cfg["alert_dedup_window_s"])
        await repo.touch_event(str(existing["alert_event_id"]), alert_status=alert["status"],
                               severity=alert["severity"], bump_seen=True)
        if alert["status"] == "resolved":
            counters["resolved"] += 1  # Phase1：resolved 只落库不驱动状态机
            return
        if within_window:
            counters["deduped"] += 1  # ② 去重窗口内：计数前进 + 所在未完结单 alert_count 同步前进
            await repo.bump_open_incident_counts(str(existing["alert_event_id"]))
            return
        event_id = str(existing["alert_event_id"])
    else:
        event_id = await repo.insert_event(
            source_key=SOURCE_KEY, external_alert_id=alert["external_alert_id"],
            fingerprint=alert["fingerprint"], alert_status=alert["status"],
            severity=alert["severity"], category=alert["category"], alert_name=alert["title"],
            alert_object=alert["alert_object"], strategy_name=alert["strategy_name"],
            appid=alert["app_id"], labels=alert["labels"], annotations=alert["annotations"],
            starts_at=alert["started_at"], payload=None)
        if alert["status"] == "resolved":
            counters["resolved"] += 1
            return

    by_instance: dict[str, list[dict[str, Any]]] = {}
    for rule in rules:
        if matcher.match(alert, rule.get("match_json") or {}):
            by_instance.setdefault(str(rule["agent_team_instance_id"]), []).append(rule)
    if not by_instance:
        counters["unmatched"] += 1
        return
    for instance_id, matched_rules in by_instance.items():
        await _route_to_instance(event_id, alert, instance_id, matched_rules, cfg, counters)


async def _route_to_instance(event_id: str, alert: dict[str, Any], instance_id: str,
                             matched_rules: list[dict[str, Any]], cfg: dict[str, Any],
                             counters: dict[str, Any]) -> None:
    owner = str(matched_rules[0]["owner_user_id"])
    rule_ids = [str(r["alert_rule_id"]) for r in matched_rules]
    gk = matcher.group_key(instance_id, alert["app_id"], alert["title"])

    open_inc = await repo.find_open_incident_by_group(gk)
    if open_inc is not None:  # ③ 附着：不新建不打断
        iid = str(open_inc["alert_incident_id"])
        await repo.attach_alert(iid)
        if matcher.severity_rank(alert["severity"]) < matcher.severity_rank(str(open_inc["severity"])):
            await repo.escalate_severity(iid, alert["severity"])
        await repo.link_event(iid, event_id, rule_ids)
        counters["attached"] += 1
        return

    last = await repo.latest_ended_at_by_group(gk)
    if last is not None and _age_s(last["ended_at"]) < float(cfg["alert_group_cooldown_s"]):
        counters["cooldown"] += 1  # ④ 组冷却：event 有痕，清单不刷屏
        return

    async def _skip(reason: str) -> None:
        iid = await repo.create_incident(
            instance_id=instance_id, owner=owner, rule_id=rule_ids[0],
            matched_rules=[{"rule_id": str(r["alert_rule_id"]), "rule_name": r["rule_name"]}
                           for r in matched_rules],
            group_key=gk, title=alert["title"], severity=alert["severity"],
            category=alert["category"], state="skipped", state_reason=reason,
            first_alert_at=alert["started_at"])
        await repo.link_event(iid, event_id, rule_ids)
        counters["skipped"] += 1

    sub = await repo.get_subscription(instance_id)
    max_open = (sub or {}).get("max_open_incidents") or int(cfg["alert_max_open_incidents_per_instance"])
    if await repo.count_open_by_instance(instance_id) >= int(max_open):
        await _skip("overflow_instance")  # ⑤ 实例上限：留痕不入队
        return

    if await repo.count_queued_global() >= int(cfg["alert_queue_max"]):
        victim = await repo.lowest_queued(matcher.SEVERITY_ORDER)
        if victim is not None and matcher.severity_rank(alert["severity"]) < matcher.severity_rank(
                str(victim["severity"])):
            await repo.transition(str(victim["alert_incident_id"]), from_states=["queued"],
                                  to_state="skipped", state_reason="overflow_evicted",
                                  set_ended=True)  # 严重优先：踢队尾低者腾位
            counters["skipped"] += 1
        else:
            await _skip("overflow_global")
            return

    iid = await repo.create_incident(
        instance_id=instance_id, owner=owner, rule_id=rule_ids[0],
        matched_rules=[{"rule_id": str(r["alert_rule_id"]), "rule_name": r["rule_name"]}
                       for r in matched_rules],
        group_key=gk, title=alert["title"], severity=alert["severity"],
        category=alert["category"], state="queued", state_reason=None,
        first_alert_at=alert["started_at"])
    await repo.link_event(iid, event_id, rule_ids)
    counters["queued"] += 1
