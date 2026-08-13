"""摄入管线全链路（HTTP 面）：注入 mock 告警 → admin :pull 单轮 → 用户端清单断言。

覆盖：入队/未命中、指纹去重窗口、附着式聚合+严重度升级、实例上限溢出、
忽略/重试/评价动作与状态机 409、summary 计数、resolved 只落库。
"""
from __future__ import annotations

import time

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


def _setup_instance(client, *, severities: list[str] | None = None,
                    strategies: list[str] | None = None,
                    max_open: int | None = None) -> str:
    inst = create_instance(client)
    iid = inst["instance_id"]
    unwrap(client.post(f"{BASE}/subscription:update", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "enabled": True, "max_open_incidents": max_open}))
    unwrap(client.post(f"{BASE}/rules", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "name": "MySQL 接管", "categories": ["MySQL"],
                             "severities": severities or [],
                             "strategies": strategies or []}))
    return iid


def _incidents(client, iid: str, **params) -> list[dict]:
    return unwrap(client.get(f"{BASE}/incidents", headers=USER_HEADERS,
                             params={"instance_id": iid, **params}))["items"]


def test_ingest_queues_matching_and_counts_unmatched(client):
    iid = _setup_instance(client, severities=["fatal", "critical"])
    alert_platform_mock._inject(title="MySQL 主库延迟>5s", category="MySQL", severity="fatal",
                                app_id="APP-A", labels={"service": "pay-core"})
    alert_platform_mock._inject(title="Nginx 4xx 升高", category="Nginx", severity="warning")

    counters = _pull(client)
    assert counters["queued"] == 1 and counters["unmatched"] == 1

    items = _incidents(client, iid, state="queued")
    assert len(items) == 1
    inc = items[0]
    assert inc["category"] == "MySQL" and inc["severity"] == "fatal"
    assert inc["app_id"] == "APP-A" and inc["alert_count"] == 1
    assert inc["matched_rule"]["name"] == "MySQL 接管" and inc["run_id"] is None

    detail = unwrap(client.get(f"{BASE}/incidents/{inc['incident_id']}", headers=USER_HEADERS))
    assert detail["alerts_total"] == 1 and detail["queue_position"] == 1
    assert detail["labels"].get("service") == "pay-core"
    assert detail["timeline"][0]["event"].startswith("命中规则")


def test_dedup_window_bumps_counts_without_new_incident(client):
    iid = _setup_instance(client)
    alert_platform_mock._inject(title="Redis 内存>85%", category="MySQL", severity="critical",
                                app_id="APP-A", fingerprint="fp_same")
    assert _pull(client)["queued"] == 1
    alert_platform_mock._inject(title="Redis 内存>85%", category="MySQL", severity="critical",
                                app_id="APP-A", fingerprint="fp_same")
    counters = _pull(client)
    assert counters["deduped"] == 1 and counters["queued"] == 0

    items = _incidents(client, iid)
    assert len(items) == 1 and items[0]["alert_count"] == 2  # 去重窗口内：单不重建、计数前进

    detail = unwrap(client.get(f"{BASE}/incidents/{items[0]['incident_id']}", headers=USER_HEADERS))
    assert detail["alerts_total"] == 1 and detail["alerts"][0]["seen_count"] == 2


def test_attach_same_group_and_escalate_severity(client):
    iid = _setup_instance(client)  # severities 不限
    alert_platform_mock._inject(title="MySQL 连接池耗尽", category="MySQL", severity="warning",
                                app_id="APP-A", fingerprint="fp_a1")
    assert _pull(client)["queued"] == 1
    alert_platform_mock._inject(title="MySQL 连接池耗尽", category="MySQL", severity="fatal",
                                app_id="APP-A", fingerprint="fp_a2")  # 同组不同指纹
    counters = _pull(client)
    assert counters["attached"] == 1 and counters["queued"] == 0

    items = _incidents(client, iid)
    assert len(items) == 1
    assert items[0]["alert_count"] == 2 and items[0]["severity"] == "fatal"  # 附着 + 升级

    detail = unwrap(client.get(f"{BASE}/incidents/{items[0]['incident_id']}", headers=USER_HEADERS))
    assert detail["alerts_total"] == 2


def test_instance_overflow_creates_skipped_with_reason(client):
    iid = _setup_instance(client, max_open=1)
    alert_platform_mock._inject(title="告警一", category="MySQL", app_id="APP-A", fingerprint="fp_o1")
    alert_platform_mock._inject(title="告警二", category="MySQL", app_id="APP-A", fingerprint="fp_o2")  # 不同组
    counters = _pull(client)
    assert counters["queued"] == 1 and counters["skipped"] == 1

    skipped = _incidents(client, iid, state="skipped")
    assert len(skipped) == 1 and skipped[0]["state_reason"] == "overflow_instance"

    # 溢出单可人工重新入队
    retried = unwrap(client.post(f"{BASE}/incidents/{skipped[0]['incident_id']}:retry",
                                 headers=USER_HEADERS, json={"client_request_id": _crid()}))
    assert retried["status"] == "queued"


def test_ignore_retry_feedback_state_machine(client):
    iid = _setup_instance(client)
    alert_platform_mock._inject(title="待忽略", category="MySQL", app_id="APP-A", fingerprint="fp_ig")
    _pull(client)
    inc = _incidents(client, iid)[0]
    iid_inc = inc["incident_id"]

    ignored = unwrap(client.post(f"{BASE}/incidents/{iid_inc}:ignore", headers=USER_HEADERS,
                                 json={"client_request_id": _crid()}))
    assert ignored["status"] == "ignored"
    again = client.post(f"{BASE}/incidents/{iid_inc}:ignore", headers=USER_HEADERS,
                        json={"client_request_id": _crid()})
    assert again.status_code == 409  # 非 queued 不可忽略

    back = unwrap(client.post(f"{BASE}/incidents/{iid_inc}:retry", headers=USER_HEADERS,
                              json={"client_request_id": _crid()}))
    assert back["status"] == "queued"

    fb = unwrap(client.post(f"{BASE}/incidents/{iid_inc}:feedback", headers=USER_HEADERS,
                            json={"client_request_id": _crid(), "feedback": "positive",
                                  "note": "诊断准确"}))
    assert fb["user_feedback"] == "positive" and fb["feedback_note"] == "诊断准确"


def test_summary_counts_and_change_seq(client):
    iid = _setup_instance(client)
    alert_platform_mock._inject(title="s1", category="MySQL", app_id="APP-A", fingerprint="fp_s1")
    _pull(client)
    s = unwrap(client.get(f"{BASE}/summary", headers=USER_HEADERS, params={"instance_id": iid}))
    assert s["queued"] == 1 and s["attention"] == 1 and s["latest_change_seq"] > 0


def test_unmatched_alert_not_archived(client):
    """②' 未命中不存档（2026-08-13 内网反馈：全网 Kafka 流量全存会灌爆 event 表）。"""
    _setup_instance(client)
    alert_platform_mock._inject(alert_id="ALM-UNM1", title="Nginx 4xx 升高", category="Nginx",
                                severity="warning", app_id="APP-A")
    counters = _pull(client)
    assert counters["unmatched"] == 1
    stored = unwrap(client.get("/api/openops/v1/admin/alerts/events",
                               headers=ADMIN_HEADERS))["items"]
    assert not any(r["alert_no"] == "ALM-UNM1" for r in stored), "未命中告警不应存档"


def test_resolved_only_lands_event_without_incident(client):
    iid = _setup_instance(client)
    alert_platform_mock._inject(title="已恢复的告警", category="MySQL", status="resolved",
                                fingerprint="fp_res")
    counters = _pull(client)
    assert counters["resolved"] == 1 and counters["queued"] == 0
    assert _incidents(client, iid) == []


def test_disabled_subscription_means_no_match(client):
    inst = create_instance(client)
    iid = inst["instance_id"]  # 不开订阅、只建规则 → list_enabled_rules 不含它
    unwrap(client.post(f"{BASE}/rules", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "name": "未开通", "categories": ["MySQL"]}))
    alert_platform_mock._inject(title="x", category="MySQL", fingerprint="fp_d1")
    counters = _pull(client)
    assert counters["unmatched"] == 1 and _incidents(client, iid) == []


def test_unmatched_batch_zero_fingerprint_lookups(client, monkeypatch):
    """⓪ 匹配前置守卫：未命中批零指纹查询（吞吐硬语义——全网 ≈3000 条/s 靠它追平）。"""
    import asyncio as _aio

    from alerts import ingest
    from alerts import repository as _repo

    _setup_instance(client)  # 有启用规则在场，未命中仍零查询才算数
    calls = {"n": 0}
    real = _repo.get_event_by_fingerprint

    async def counting(*a, **k):
        calls["n"] += 1
        return await real(*a, **k)

    monkeypatch.setattr(_repo, "get_event_by_fingerprint", counting)
    alerts = [{"alert_id": f"zz{i}", "title": f"其他应用告警{i}", "category": "Nginx",
               "severity": "warning", "fingerprint": f"fp_zz{i}"} for i in range(50)]
    counters = _aio.run(ingest.ingest_batch(alerts))
    assert counters["unmatched"] == 50 and counters["queued"] == 0
    assert calls["n"] == 0, "未命中告警不应触达指纹查询（零 DB 粗滤被破坏）"
