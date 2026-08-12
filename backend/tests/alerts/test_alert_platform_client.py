"""告警平台客户端：mock 变更流语义 + real 对齐契约 + 历史查询（stub-httpx，照 test_external_real 范式）。"""
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


# ============================ list_history（29.10 历史告警查询） ============================

def _hist_env(monkeypatch):
    _real_env(monkeypatch)
    monkeypatch.setenv(
        "OPENOPS_ALERT_QUERY_URL",
        "http://wesee.console.hissit/observe/unifieduery/api/v1/e888/p232/alarm_list_for_sreagent")


def _dt(s: str):
    from datetime import datetime
    return datetime.fromisoformat(s)


async def test_real_list_history_body_url_bearer(monkeypatch):
    """wire 词表翻译全景：北京时间串 / alarmLevels 数字 / moTypeList / projectIds / enterpriseId 有无两态。"""
    _hist_env(monkeypatch)
    monkeypatch.delenv("OPENOPS_ALERT_ENTERPRISE_ID", raising=False)
    payload = {"status": "OK", "message": "success!",
               "data": {"columns": [], "datas": [{
                   "alarmCode": "ac1", "alarmLevel": "1", "moType": "MySQL",
                   "ciName": "mysql-01", "projectId": "p144", "alarmTitle": "t",
                   "alarmDesc": "d", "status": "5", "incidentClosedTime": "1786071740000",
                   "alarmTime": "2026-08-06T21:59:29.000+00:00", "duration": 60}]}}
    cap = _install(monkeypatch, lambda m, u, k: _Resp(200, payload))

    res = await alert_platform_client.list_history(
        start=_dt("2026-08-02T10:00:00+08:00"), end=_dt("2026-08-09T10:00:00+08:00"),
        categories=["MySQL", "Docker"], severities=["fatal", "critical"],
        project_ids=["p144"], page_no=1, page_size=20)

    method, url, kwargs = cap[0]
    assert (method, url) == ("POST", "http://wesee.console.hissit/observe/unifieduery/api/v1/e888/p232/alarm_list_for_sreagent")
    body = kwargs["json"]
    assert body["startTime"] == "2026-08-02 10:00:00" and body["endTime"] == "2026-08-09 10:00:00"
    assert body["moTypeList"] == ["MySQL", "Docker"]
    assert body["alarmLevels"] == [1, 2]          # fatal/critical → 数字反查
    assert body["projectIds"] == ["p144"]
    assert body["pageNo"] == 1 and body["pageSize"] == 20
    assert "enterpriseId" not in body             # env 未配不带（文档已改非必填）
    assert kwargs["_ctor"]["headers"]["Authorization"] == "Bearer svc-tkn-1"
    # 行已映射为预览行（内部形状）
    row = res["rows"][0]
    assert row["alert_no"] == "ac1" and row["severity"] == "fatal"
    assert row["alert_status"] == "closed" and row["appid"] == "p144"
    assert row["detail_url"] == ""                # 未配 ALARM_*_URL：不拼链（编号退纯文本）
    assert res["total"] == 1                      # 无 total 字段退化为本页行数（R6）


async def test_real_list_history_detail_url_by_enterprise(monkeypatch):
    """ALARM_OP_URL/ALARM_KWE_URL 配置后按 enterpriseId 分发拼 ?alarmCode=。"""
    _hist_env(monkeypatch)
    monkeypatch.setenv("ALARM_OP_URL", "https://op.example/detail")
    monkeypatch.setenv("ALARM_KWE_URL", "https://kwe.example/detail")
    payload = {"status": "OK", "data": {"datas": [
        {"alarmCode": "acO", "alarmLevel": "1", "moType": "MySQL", "ciName": "m1",
         "enterpriseId": "8" * 32, "status": "1"},
        {"alarmCode": "acK", "alarmLevel": "2", "moType": "MySQL", "ciName": "m2",
         "enterpriseId": "1" * 32, "status": "1"},
        {"alarmCode": "acX", "alarmLevel": "3", "moType": "MySQL", "ciName": "m3",
         "enterpriseId": "e-other", "status": "1"}]}}
    _install(monkeypatch, lambda m, u, k: _Resp(200, payload))
    res = await alert_platform_client.list_history(
        start=_dt("2026-08-08T10:00:00+08:00"), end=_dt("2026-08-09T10:00:00+08:00"),
        categories=[], severities=[], project_ids=None)
    urls = {r["alert_no"]: r["detail_url"] for r in res["rows"]}
    assert urls["acO"] == "https://op.example/detail?alarmCode=acO"
    assert urls["acK"] == "https://kwe.example/detail?alarmCode=acK"
    assert urls["acX"] == ""


async def test_real_list_history_enterprise_and_failed_status(monkeypatch):
    _hist_env(monkeypatch)
    monkeypatch.setenv("OPENOPS_ALERT_ENTERPRISE_ID", "e888")
    cap = _install(monkeypatch, lambda m, u, k: _Resp(200, {"status": "OK", "data": {"datas": []}}))
    await alert_platform_client.list_history(
        start=_dt("2026-08-08T10:00:00+08:00"), end=_dt("2026-08-09T10:00:00+08:00"),
        categories=[], severities=[], project_ids=None)
    assert cap[0][2]["json"]["enterpriseId"] == "e888"
    assert "moTypeList" not in cap[0][2]["json"] and "projectIds" not in cap[0][2]["json"]

    _install(monkeypatch, lambda m, u, k: _Resp(200, {"status": "FAILED", "message": "bad"}))
    with pytest.raises(AlertPlatformError) as e1:
        await alert_platform_client.list_history(
            start=_dt("2026-08-08T10:00:00+08:00"), end=_dt("2026-08-09T10:00:00+08:00"),
            categories=[], severities=[], project_ids=None)
    assert e1.value.kind == "http"


async def test_real_list_history_query_url_fail_closed(monkeypatch):
    _real_env(monkeypatch)
    monkeypatch.delenv("OPENOPS_ALERT_QUERY_URL", raising=False)
    with pytest.raises(AlertPlatformError) as e1:
        await alert_platform_client.list_history(
            start=_dt("2026-08-08T10:00:00+08:00"), end=_dt("2026-08-09T10:00:00+08:00"),
            categories=[], severities=[], project_ids=None)
    assert e1.value.kind == "config"

    monkeypatch.setenv("OPENOPS_ALERT_QUERY_URL", "http://{host}/alarm_list")
    with pytest.raises(AlertPlatformError) as e2:
        await alert_platform_client.list_history(
            start=_dt("2026-08-08T10:00:00+08:00"), end=_dt("2026-08-09T10:00:00+08:00"),
            categories=[], severities=[], project_ids=None)
    assert e2.value.kind == "config"


async def test_mock_list_history_window_and_filters():
    """mock 样本：7 天窗计数 > 3 天窗计数（切窗有差可断言）；类别/级别过滤生效。"""
    from datetime import datetime, timedelta, timezone

    end = datetime.now(timezone.utc)

    async def count(days: int, cats: list[str], sevs: list[str]) -> int:
        page = await alert_platform_client.list_history(
            start=end - timedelta(days=days), end=end,
            categories=cats, severities=sevs, project_ids=None, page_size=50)
        return page["total"]

    n3 = await count(3, ["MySQL"], ["fatal", "critical"])
    n7 = await count(7, ["MySQL"], ["fatal", "critical"])
    assert 0 < n3 < n7                                  # D5 的 MySQL 行只落 7 天窗
    assert await count(7, ["PostgreSQL"], []) >= 1
    assert await count(7, ["MySQL"], ["warning"]) == 0  # 样本 MySQL 无 warning
