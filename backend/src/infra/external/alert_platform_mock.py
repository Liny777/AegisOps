"""告警平台 mock：内存变更流（append-only changes log）+ 注入/重置测试钩子。

`OPENOPS_ALERT=mock`（默认）时启用；仅供 demo / pytest，不联真实告警平台。
游标 = 内部自增序号的字符串形式（与真实契约「不透明游标」同语义：调用方原样回传，
空游标从流头开始——mock 流仅进程内，不存在「历史回灌」问题）。

`OPENOPS_ALERT_MOCK_SEED=1` 时，首个 list_changes 遇到空流会自动播种两条演示告警，
供本地全链路 demo（设置页配规则 → 清单看流转 → 打开诊断会话）。pytest 不设该 env，
用 `_inject()` 精确控制。
"""
from __future__ import annotations

import copy
import os
import uuid
from datetime import datetime, timezone
from typing import Any

_SEQ = 0
_LOG: list[tuple[int, dict[str, Any]]] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mk(fields: dict[str, Any]) -> dict[str, Any]:
    """按契约 AlertDTO 补默认值（title 必给，其余可省）。"""
    title = str(fields.get("title") or "未命名告警")
    alert = {
        "alert_id": fields.get("alert_id") or "alt_" + uuid.uuid4().hex[:10],
        "fingerprint": fields.get("fingerprint") or "",
        "status": fields.get("status") or "firing",
        "severity": fields.get("severity") or "warning",
        "category": fields.get("category") or "",
        "title": title,
        "description": fields.get("description") or "",
        "alert_object": fields.get("alert_object") or "",
        "strategy_name": fields.get("strategy_name") or "",
        "detail_url": fields.get("detail_url")
                      or f"https://alerts.example.internal/detail/{fields.get('alert_id') or 'mock'}",
        "app_id": fields.get("app_id") or "",
        "labels": dict(fields.get("labels") or {}),
        "annotations": dict(fields.get("annotations") or {}),
        "started_at": fields.get("started_at") or _now(),
        "resolved_at": fields.get("resolved_at"),
        "updated_at": fields.get("updated_at") or _now(),
        "source": fields.get("source") or "mock",
    }
    return alert


def _seed_if_requested() -> None:
    if _LOG or os.environ.get("OPENOPS_ALERT_MOCK_SEED", "") != "1":
        return
    _inject(title="MySQL 主库延迟>5s", severity="fatal", category="MySQL",
            app_id="APP-A", alert_object="mysql-prod-03",
            strategy_name="MySQL 主从延迟监控",
            labels={"service": "pay-core", "env": "prod"},
            description="主从延迟 6.8s > 5s 持续 3 分钟")
    _inject(title="PGSQL 事务锁等待超阈值", severity="critical", category="PGSQL",
            app_id="APP-B", alert_object="pg-prod-01",
            strategy_name="PGSQL 事务锁等待监控",
            labels={"service": "order-db", "env": "prod"},
            description="锁等待事件 37 次/5min")


async def list_changes(cursor: str = "", limit: int = 200) -> dict[str, Any]:
    _seed_if_requested()
    try:
        start = int(cursor) if cursor else 0
    except ValueError:
        start = 0
    pending = [(seq, alert) for seq, alert in _LOG if seq > start]
    batch = pending[: max(1, int(limit))]
    last = batch[-1][0] if batch else start
    return {
        "alerts": [copy.deepcopy(alert) for _seq, alert in batch],
        "next_cursor": str(last),
        "has_more": len(pending) > len(batch),
    }


async def get_alert(alert_id: str) -> dict[str, Any] | None:
    for _seq, alert in reversed(_LOG):
        if alert["alert_id"] == alert_id:
            return copy.deepcopy(alert)
    return None


# ---- 测试/演示钩子 ----
def _inject(**fields: Any) -> dict[str, Any]:
    """向变更流追加一条告警（重复 alert_id + 新 updated_at = 同告警的状态变更，契合 update-log 语义）。"""
    global _SEQ
    _SEQ += 1
    alert = _mk(fields)
    _LOG.append((_SEQ, alert))
    return copy.deepcopy(alert)


def _reset() -> None:
    """清空变更流与序号（测试隔离）。"""
    global _SEQ
    _SEQ = 0
    _LOG.clear()
