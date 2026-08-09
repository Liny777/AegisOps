"""规则编辑器第二步「历史告警预览」端点：平台主路径 / 参数校验 / 归属 / 降级本地库。

mock 档 `_impl()` 走 alert_platform_mock.list_history 样本（相对日期 D1/D2 vs D5/D6），
降级路径用 monkeypatch 把 client.list_history 打炸模拟平台故障。
"""
from __future__ import annotations

import time

import pytest

from conftest import OTHER_HEADERS, USER_HEADERS, create_instance, unwrap
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
    inst = create_instance(client, f"预览 Agent {time.time_ns()}")
    return inst["instance_id"]


def _preview(client, iid: str, headers=USER_HEADERS, **params):
    return client.get(f"{BASE}/history-preview", headers=headers,
                      params={"instance_id": iid, **params})


def test_platform_source_seven_columns_and_window(client):
    """主路径：source=platform；七列齐；7 天窗计数 > 3 天窗（样本 D5 只落 7 天窗）。"""
    iid = _setup_instance(client)
    got7 = unwrap(_preview(client, iid, categories="MySQL", severity="fatal,critical",
                           since_days=7))
    assert got7["source"] == "platform"
    assert got7["total"] > 0 and got7["items"]
    row = got7["items"][0]
    for key in ("alert_no", "category", "alert_object", "appid", "severity",
                "alert_status", "description", "started_at", "detail_url"):
        assert key in row, f"预览行缺 {key}"
    assert row["category"] == "MySQL"

    got3 = unwrap(_preview(client, iid, categories="MySQL", severity="fatal,critical",
                           since_days=3))
    assert got3["source"] == "platform"
    assert got3["total"] < got7["total"], "切 3 天窗应比 7 天窗少（D5 样本出窗）"


def test_multi_categories_and_default_severities(client):
    """多类别 CSV 一次传；severity 缺省=UI 三档全选。"""
    iid = _setup_instance(client)
    got = unwrap(_preview(client, iid, categories="MySQL,PostgreSQL", since_days=7))
    cats = {r["category"] for r in got["items"]}
    assert cats <= {"MySQL", "PostgreSQL"} and len(cats) == 2


def test_categories_blank_rejected(client):
    iid = _setup_instance(client)
    resp = _preview(client, iid, categories=" , ", since_days=7)
    assert resp.status_code == 400  # 全空串在 service 层 400（复用 _check_categories）


def test_non_owner_forbidden(client):
    iid = _setup_instance(client)
    resp = _preview(client, iid, headers=OTHER_HEADERS, categories="MySQL", since_days=7)
    assert resp.status_code == 403


def test_platform_down_falls_back_to_local(client, monkeypatch):
    """平台故障 → 降级本地事件库：source=local_fallback，行来自已落库事件。"""
    from infra.external.alert_platform_client import AlertPlatformError

    iid = _setup_instance(client)
    # 订阅 + 规则 + 注入 + 拉取：种一条本地落库的 MySQL 事件
    unwrap(client.post(f"{BASE}/subscription:update", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "enabled": True}))
    unwrap(client.post(f"{BASE}/rules", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "name": "MySQL 接管", "categories": ["MySQL"]}))
    alert_platform_mock._inject(alert_id="ALM-FB1", title="MySQL 主库延迟>5s",
                                category="MySQL", severity="fatal", app_id="APP-A",
                                alert_object="mysql-prod-03")
    from conftest import ADMIN_HEADERS
    unwrap(client.post("/api/openops/v1/admin/alerts:pull", headers=ADMIN_HEADERS))

    async def _boom(**kwargs):
        raise AlertPlatformError("network", "模拟平台故障")

    from infra.external import alert_platform_client
    monkeypatch.setattr(alert_platform_client, "list_history", _boom)

    got = unwrap(_preview(client, iid, categories="MySQL,PostgreSQL", since_days=7))
    assert got["source"] == "local_fallback"
    assert any(r["alert_no"] == "ALM-FB1" for r in got["items"]), "降级应回落到本地落库事件"
