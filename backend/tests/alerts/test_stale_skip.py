"""陈旧告警留痕跳过（2026-08-15 拍板：消费延迟不静默丢——命中的建 skipped 单告知用户）。

年龄闸在 附着/冷却之后、上限之前：告警时间距今 > alert_stale_message_age_s（默认 1800，
0=关）→ state=skipped/reason=stale_consumer_lag，清单可见可 :retry；未命中陈旧随
unmatched 丢；started_at 缺失 fail-open。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

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


def _pull(client) -> dict:
    return unwrap(client.post("/api/openops/v1/admin/alerts:pull", headers=ADMIN_HEADERS))["counters"]


def _setup_instance(client) -> str:
    iid = create_instance(client)["instance_id"]
    unwrap(client.post(f"{BASE}/rules", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "name": "MySQL 接管", "categories": ["MySQL"]}))
    return iid


def _old_ts(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def test_stale_matched_leaves_visible_skip(client):
    """命中+陈旧（40 分钟前）→ stale 计数 + skipped/stale_consumer_lag 留痕 +
    用户清单可见且 state_reason 透出（「延迟放弃」的前端依据）+ 可重试。"""
    iid = _setup_instance(client)
    alert_platform_mock._inject(alert_id="ALM-STALE-1", title="MySQL 主库延迟>5s",
                                category="MySQL", severity="fatal", app_id="APP-A",
                                started_at=_old_ts(40))
    counters = _pull(client)
    assert counters["stale"] == 1 and counters["queued"] == 0

    inc = unwrap(client.get(f"{BASE}/incidents", headers=USER_HEADERS,
                            params={"instance_id": iid, "state": "skipped"}))["items"][0]
    assert inc["state_reason"] == "stale_consumer_lag"

    ev = next(r for r in unwrap(client.get(f"{BASE}/events", headers=USER_HEADERS,
                                           params={"instance_id": iid}))["items"]
              if r["alert_no"] == "ALM-STALE-1")
    assert ev["takeover_status"] == "none" and ev["state_reason"] == "stale_consumer_lag"

    retried = unwrap(client.post(f"{BASE}/incidents/{inc['incident_id']}:retry",
                                 headers=USER_HEADERS, json={"client_request_id": _crid()}))
    assert retried["status"] == "queued"  # 用户可手动重新入队自动诊断


def test_stale_unmatched_still_dropped(client):
    """未命中+陈旧：随 unmatched 丢（本来就不落库，无感知面），不计 stale。"""
    _setup_instance(client)
    alert_platform_mock._inject(title="Nginx 4xx 升高", category="Nginx",
                                severity="warning", app_id="APP-A", started_at=_old_ts(40))
    counters = _pull(client)
    assert counters["unmatched"] == 1 and counters["stale"] == 0


def test_fresh_alert_unaffected_and_zero_disables(client):
    """新鲜命中照常入队；阈值=0 关闭年龄闸后陈旧也照常入队。"""
    iid = _setup_instance(client)
    alert_platform_mock._inject(title="MySQL 新鲜告警", category="MySQL",
                                severity="critical", app_id="APP-A", started_at=_old_ts(1))
    assert _pull(client)["queued"] == 1

    unwrap(client.post("/api/openops/v1/admin/alerts/config:update", headers=ADMIN_HEADERS,
                       json={"client_request_id": _crid(),
                             "updates": {"alert_stale_message_age_s": 0}, "reason": "关闸验证"}))
    alert_platform_mock._inject(title="MySQL 陈旧但闸已关", category="MySQL",
                                severity="critical", app_id="APP-A", started_at=_old_ts(90))
    counters = _pull(client)
    unwrap(client.post("/api/openops/v1/admin/alerts/config:update", headers=ADMIN_HEADERS,
                       json={"client_request_id": _crid(),
                             "updates": {"alert_stale_message_age_s": 1800}, "reason": "复位"}))
    assert counters["queued"] == 1 and counters["stale"] == 0
    assert len(unwrap(client.get(f"{BASE}/incidents", headers=USER_HEADERS,
                                 params={"instance_id": iid, "state": "queued"}))["items"]) == 2


def test_stale_storm_dedup_and_each_leaves_trace(client):
    """陈旧风暴削峰分工：同指纹重发（风暴主形态）由去重窗口拦；同组不同指纹各留一单
    （各自可 retry，信息完整——skipped 单刻意不触发组冷却，latest_ended_at_by_group
    只看 completed/failed）。"""
    _setup_instance(client)
    for _ in range(3):  # 同指纹重发：只第一条建单，其余 dedup
        alert_platform_mock._inject(title="MySQL 同指纹陈旧", category="MySQL",
                                    severity="critical", app_id="APP-A",
                                    fingerprint="fp_stale_same", started_at=_old_ts(35))
    counters = _pull(client)
    assert counters["stale"] == 1 and counters["deduped"] == 2


def test_missing_started_at_fail_open(client):
    """started_at 缺失：不判陈旧（fail-open 不误杀），照常入队。
    绕过 mock（它会给缺省时间戳填 now）直调 ingest_batch。"""
    import asyncio as _aio

    from alerts import ingest

    _setup_instance(client)
    counters = _aio.run(ingest.ingest_batch([{
        "alert_id": "ALM-NOTS", "title": "MySQL 无时间戳", "category": "MySQL",
        "severity": "critical", "app_id": "APP-A", "fingerprint": "fp_nots"}]))
    assert counters["queued"] == 1 and counters["stale"] == 0
