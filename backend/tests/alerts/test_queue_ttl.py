"""排队超时翻失败（2026-08-14 拍板：排队超 alert_queue_max_age_s 自动「接管失败」留痕，
清单可见可手动重试；判据 queued_at——:retry 重置计时不被立刻再翻）。"""
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


def _queued_incident(client) -> tuple[str, str]:
    iid = create_instance(client)["instance_id"]
    unwrap(client.post(f"{BASE}/rules", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "name": "MySQL 接管", "categories": ["MySQL"]}))
    alert_platform_mock._inject(title=f"MySQL 排队超时验证 {time.time_ns()}", category="MySQL",
                                severity="critical", app_id="APP-A")
    assert unwrap(client.post("/api/openops/v1/admin/alerts:pull",
                              headers=ADMIN_HEADERS))["counters"]["queued"] == 1
    inc = unwrap(client.get(f"{BASE}/incidents", headers=USER_HEADERS,
                            params={"instance_id": iid, "state": "queued"}))["items"][0]
    return iid, inc["incident_id"]


def _age_queued(incident_id: str, seconds: int) -> None:
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("update sre_alert_incident set queued_at = now() - make_interval(secs => %s) "
                     "where alert_incident_id = %s", (seconds, incident_id))


def test_expire_stale_queued_flips_to_failed_visible(client, monkeypatch):
    from alerts import repository as repo
    from infra.external import welink_client

    # 2026-08-19 拍板守卫：queue_expired 批量翻转不发 WeLink（防重启追赶期轰炸，清单可见即可）
    notified: list = []
    monkeypatch.setattr(welink_client, "send_welink_message_for_person",
                        lambda uid, data: notified.append((uid, data)))

    iid, inc_id = _queued_incident(client)
    _age_queued(inc_id, 90000)  # 拨老过默认 86400
    assert asyncio.run(repo.expire_stale_queued(86400)) == 1

    inc = unwrap(client.get(f"{BASE}/incidents", headers=USER_HEADERS,
                            params={"instance_id": iid}))["items"][0]
    assert inc["status"] == "failed" and inc["state_reason"] == "queue_expired"
    # 清单可见闭环：接管投影 done + 结果列「失败」（agent_result=failed 点亮红 Pill）
    ev = unwrap(client.get(f"{BASE}/events", headers=USER_HEADERS,
                           params={"instance_id": iid}))["items"][0]
    assert ev["takeover_status"] == "done" and ev["agent_result"] == "failed"
    assert notified == []  # 批量过期零通知


def test_retry_resets_clock_and_survives_expiry(client):
    from alerts import repository as repo

    iid, inc_id = _queued_incident(client)
    _age_queued(inc_id, 90000)
    assert asyncio.run(repo.expire_stale_queued(86400)) == 1
    # 手动重试回队：requeue 重置 queued_at → 同一 TTL 再跑不被翻（判据 queued_at 的守卫）
    got = unwrap(client.post(f"{BASE}/incidents/{inc_id}:retry", headers=USER_HEADERS,
                             json={"client_request_id": _crid()}))
    assert got["status"] == "queued"
    assert asyncio.run(repo.expire_stale_queued(86400)) == 0
    items = unwrap(client.get(f"{BASE}/incidents", headers=USER_HEADERS,
                              params={"instance_id": iid, "state": "queued"}))["items"]
    assert len(items) == 1


def test_config_knob_exists_with_bounds(client):
    got = unwrap(client.get("/api/openops/v1/admin/alerts/config", headers=ADMIN_HEADERS))
    assert got["config"]["alert_queue_max_age_s"] == 86400
    bad = client.post("/api/openops/v1/admin/alerts/config:update", headers=ADMIN_HEADERS,
                      json={"client_request_id": _crid(),
                            "updates": {"alert_queue_max_age_s": 60}, "reason": "越界"})
    assert bad.status_code == 400  # VALIDATION_FAILED（[300,604800] 边界拦截）
