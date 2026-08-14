"""三级队列有效优先级（§5.3，2026-08-15 落地）：老化防饿死 + 管理员置顶软插队。

有效优先级 = manual_priority ? -1 : greatest(0, severity_rank − floor(等待分钟/aging))
取队/踢除/位次三处同用一个表达式；置顶仅 queued、不打断在跑的（拍板⑯）。
"""
from __future__ import annotations

import asyncio
import os
import time

import psycopg
import pytest

from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, unwrap
from infra.external import alert_platform_mock

BASE = "/api/openops/v1/alerts"


@pytest.fixture(autouse=True)
def _clean_mock():
    alert_platform_mock._reset()
    yield
    alert_platform_mock._reset()


def _crid() -> str:
    return f"crid_{time.time_ns()}"


def _setup_instance(client) -> str:
    iid = create_instance(client)["instance_id"]
    unwrap(client.post(f"{BASE}/rules", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "name": "MySQL 接管", "categories": ["MySQL"]}))
    return iid


def _queue_two(client, iid: str) -> tuple[str, str]:
    """入队 warning + fatal 两单（不同组），返回 (warning_id, fatal_id)。"""
    alert_platform_mock._inject(title=f"普通告警 {time.time_ns()}", category="MySQL",
                                severity="warning", app_id="APP-A")
    alert_platform_mock._inject(title=f"致命告警 {time.time_ns()}", category="MySQL",
                                severity="fatal", app_id="APP-A")
    assert unwrap(client.post("/api/openops/v1/admin/alerts:pull",
                              headers=ADMIN_HEADERS))["counters"]["queued"] == 2
    items = unwrap(client.get(f"{BASE}/incidents", headers=USER_HEADERS,
                              params={"instance_id": iid, "state": "queued"}))["items"]
    warn = next(i for i in items if i["severity"] == "warning")
    fatal = next(i for i in items if i["severity"] == "fatal")
    return warn["incident_id"], fatal["incident_id"]


def _age_queued(incident_id: str, minutes: int) -> None:
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("update sre_alert_incident set queued_at = now() - make_interval(mins => %s) "
                     "where alert_incident_id = %s", (minutes, incident_id))


def _pick(aging_minutes: int) -> dict:
    from alerts import matcher
    from alerts import repository as repo

    return asyncio.run(repo.pick_next_queued(matcher.SEVERITY_ORDER, aging_minutes))


def test_aging_promotes_waiting_alert(client):
    """老化防饿死：warning 排队 25 分钟（aging=10 → 升两档=致命档），同档比 fatal 更早入队 → 先出。"""
    iid = _setup_instance(client)
    warn_id, fatal_id = _queue_two(client, iid)
    _age_queued(warn_id, 25)

    assert str(_pick(aging_minutes=10)["alert_incident_id"]) == warn_id, "老化后应先出等了 25 分钟的普通告警"
    assert str(_pick(aging_minutes=1440)["alert_incident_id"]) == fatal_id, "关掉老化（1440）严格按严重度"


def test_prioritize_jumps_queue_and_cancel_restores(client):
    """置顶=有效优先级 -1 越过 fatal 排队首；取消恢复原序；审计留痕。"""
    iid = _setup_instance(client)
    warn_id, fatal_id = _queue_two(client, iid)

    unwrap(client.post(f"/api/openops/v1/admin/alerts/incidents/{warn_id}:prioritize",
                       headers=ADMIN_HEADERS,
                       json={"client_request_id": _crid(), "reason": "现网故障给它让路"}))
    assert str(_pick(10)["alert_incident_id"]) == warn_id, "置顶应越过 fatal 排队首"
    # 位次口径与取队同序：置顶单详情 queue_position=1
    detail = unwrap(client.get(f"{BASE}/incidents/{warn_id}", headers=USER_HEADERS))
    assert detail["queue_position"] == 1

    unwrap(client.post(f"/api/openops/v1/admin/alerts/incidents/{warn_id}:prioritize",
                       headers=ADMIN_HEADERS,
                       json={"client_request_id": _crid(), "reason": "误操作回退", "cancel": True}))
    assert str(_pick(1440)["alert_incident_id"]) == fatal_id, "取消置顶后恢复严重度序"

    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        n = conn.execute("select count(*) from sre_audit_event "
                         "where event_type='alert.incident_prioritized'").fetchone()[0]
    assert n >= 2, "置顶与取消都应写审计"


def test_prioritize_requires_queued_state(client):
    """仅排队中可置顶：ignored 单置顶 → 409（不抢占语义的边界）。"""
    iid = _setup_instance(client)
    warn_id, _ = _queue_two(client, iid)
    unwrap(client.post(f"{BASE}/incidents/{warn_id}:ignore", headers=USER_HEADERS,
                       json={"client_request_id": _crid()}))
    r = client.post(f"/api/openops/v1/admin/alerts/incidents/{warn_id}:prioritize",
                    headers=ADMIN_HEADERS,
                    json={"client_request_id": _crid(), "reason": "不该成功"})
    assert r.status_code == 409
