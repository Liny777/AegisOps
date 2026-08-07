"""告警平台客户端三件套：mock 变更流语义 + real 对齐契约（stub-httpx，照 test_external_real 范式）。"""
from __future__ import annotations

import pytest

from infra.external import alert_platform_client, alert_platform_mock
from infra.external.alert_platform_client import AlertPlatformError
from test_external_real import _Resp, _install


@pytest.fixture(autouse=True)
def _clean_mock():
    alert_platform_mock._reset()
    yield
    alert_platform_mock._reset()


# ============================ mock：变更流语义 ============================

async def test_mock_cursor_pagination_and_has_more():
    for i in range(3):
        alert_platform_mock._inject(title=f"a{i}", category="MySQL")

    page1 = await alert_platform_client.list_changes("", limit=2)
    assert [a["title"] for a in page1["alerts"]] == ["a0", "a1"]
    assert page1["has_more"] is True

    page2 = await alert_platform_client.list_changes(page1["next_cursor"], limit=2)
    assert [a["title"] for a in page2["alerts"]] == ["a2"]
    assert page2["has_more"] is False

    # 无新数据：返回原游标，重放安全（契约 6.2 语义）
    page3 = await alert_platform_client.list_changes(page2["next_cursor"], limit=2)
    assert page3["alerts"] == []
    assert page3["next_cursor"] == page2["next_cursor"]


async def test_mock_update_log_semantics_same_alert_reappears():
    """同一 alert_id 的状态变更重现流中（update-log / at-least-once）。"""
    first = alert_platform_mock._inject(title="mysql 延迟", status="firing", severity="fatal")
    alert_platform_mock._inject(alert_id=first["alert_id"], title="mysql 延迟",
                                status="resolved", resolved_at="2026-07-30T10:00:00+08:00")

    page = await alert_platform_client.list_changes("", limit=10)
    assert len(page["alerts"]) == 2
    assert page["alerts"][0]["status"] == "firing"
    assert page["alerts"][1]["status"] == "resolved"

    detail = await alert_platform_client.get_alert(first["alert_id"])
    assert detail is not None and detail["status"] == "resolved"  # 详情取最新
    assert await alert_platform_client.get_alert("alt_nonexistent") is None


async def test_mock_fills_contract_defaults():
    alert_platform_mock._inject(title="裸告警")
    page = await alert_platform_client.list_changes("", limit=1)
    a = page["alerts"][0]
    for key in ("alert_id", "fingerprint", "status", "severity", "category", "title",
                "description", "app_id", "labels", "annotations", "started_at",
                "resolved_at", "updated_at", "source"):
        assert key in a, f"AlertDTO 缺字段 {key}"
    assert a["status"] == "firing" and a["severity"] == "warning" and a["source"] == "mock"


# ============================ real：契约对齐 ============================

def _real_env(monkeypatch):
    monkeypatch.setenv("OPENOPS_ALERT", "real")
    monkeypatch.setenv("OPENOPS_ALERT_BASE_URL", "http://alerts.internal:9090")
    monkeypatch.setenv("OPENOPS_ALERT_TOKEN", "svc-tkn-1")


async def test_real_list_changes_url_params_and_bearer(monkeypatch):
    _real_env(monkeypatch)
    payload = {"alerts": [{"alert_id": "alt_1", "title": "t", "status": "firing",
                           "severity": "fatal", "updated_at": "2026-07-30T09:00:00+08:00"}],
               "next_cursor": "c2", "has_more": True}
    cap = _install(monkeypatch, lambda m, u, k: _Resp(200, payload))

    res = await alert_platform_client.list_changes("c1", limit=100)
    method, url, kwargs = cap[0]
    assert (method, url) == ("GET", "http://alerts.internal:9090/openapi/alerts/v1/changes")
    assert kwargs["params"] == {"cursor": "c1", "limit": 100}
    assert kwargs["_ctor"]["headers"]["Authorization"] == "Bearer svc-tkn-1"
    assert res["next_cursor"] == "c2" and res["has_more"] is True and len(res["alerts"]) == 1


async def test_real_error_mapping(monkeypatch):
    _real_env(monkeypatch)

    _install(monkeypatch, lambda m, u, k: _Resp(401, {"error": {"code": "X"}}))
    with pytest.raises(AlertPlatformError) as e1:
        await alert_platform_client.list_changes("c1")
    assert e1.value.kind == "auth"

    _install(monkeypatch, lambda m, u, k: _Resp(410, {"error": {"code": "CURSOR_EXPIRED"},
                                                      "earliest_cursor": "e9"}))
    with pytest.raises(AlertPlatformError) as e2:
        await alert_platform_client.list_changes("c1")
    assert e2.value.kind == "cursor_expired"
    assert e2.value.detail["earliest_cursor"] == "e9"

    _install(monkeypatch, lambda m, u, k: _Resp(200, {"whatever": True}))  # 缺 alerts 数组
    with pytest.raises(AlertPlatformError) as e3:
        await alert_platform_client.list_changes("c1")
    assert e3.value.kind == "http"


async def test_real_get_alert_404_returns_none(monkeypatch):
    _real_env(monkeypatch)
    cap = _install(monkeypatch, lambda m, u, k: _Resp(404, {}))
    assert await alert_platform_client.get_alert("alt_gone") is None
    assert cap[0][1].endswith("/openapi/alerts/v1/alerts/alt_gone")


async def test_real_config_fail_closed(monkeypatch):
    """real 档缺 token / BASE_URL 带模板占位：立即 config 错，绝不静默降级。"""
    monkeypatch.setenv("OPENOPS_ALERT", "real")
    monkeypatch.setenv("OPENOPS_ALERT_BASE_URL", "http://alerts.internal:9090")
    monkeypatch.delenv("OPENOPS_ALERT_TOKEN", raising=False)
    with pytest.raises(AlertPlatformError) as e1:
        await alert_platform_client.list_changes("")
    assert e1.value.kind == "config"

    monkeypatch.setenv("OPENOPS_ALERT_TOKEN", "tkn")
    monkeypatch.setenv("OPENOPS_ALERT_BASE_URL", "http://{host}:9090")
    with pytest.raises(AlertPlatformError) as e2:
        await alert_platform_client.list_changes("")
    assert e2.value.kind == "config"
