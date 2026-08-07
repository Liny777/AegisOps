"""事件清单（全量告警视角）：可见性矩阵 / 三值投影 / 筛选搜索 / admin 按用户查看。

scope 快照通过真实 start_task 产生（mock omodel ws_pay_abc → APP-A/B/C），随后取消任务释放额度。
"""
from __future__ import annotations

import time

import pytest

from conftest import ADMIN_HEADERS, OTHER_HEADERS, USER_HEADERS, create_instance, create_run, unwrap
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


def _setup_with_scope(client) -> str:
    """A 用户：建实例+订阅+MySQL 规则，并跑一次任务产生 scope 快照（APP-A/B/C）后取消。"""
    inst = create_instance(client, f"事件清单 Agent {time.time_ns()}")
    iid = inst["instance_id"]
    unwrap(client.post(f"{BASE}/subscription:update", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "enabled": True}))
    unwrap(client.post(f"{BASE}/rules", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "name": "MySQL 接管", "categories": ["MySQL"]}))
    run = create_run(client, iid)
    task = unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks",
                              headers=USER_HEADERS,
                              json={"client_request_id": _crid(), "input_text": "巡检 APP-A"}))
    client.post(f"/api/openops/v1/tasks/{task['task_id']}:cancel", headers=USER_HEADERS,
                json={"client_request_id": _crid()})
    return iid


def _events(client, headers=USER_HEADERS, **params) -> list[dict]:
    return unwrap(client.get(f"{BASE}/events", headers=headers, params=params))["items"]


def _seed_three(client) -> None:
    alert_platform_mock._inject(alert_id="ALM-E1", title="MySQL 主库延迟>5s", category="MySQL",
                                severity="fatal", app_id="APP-A", alert_object="mysql-prod-03",
                                strategy_name="MySQL 主从延迟监控",
                                detail_url="https://alert.example/e1")
    alert_platform_mock._inject(alert_id="ALM-E2", title="Nginx 4xx 升高", category="Nginx",
                                severity="warning", app_id="APP-B", alert_object="ngx-edge-1")
    alert_platform_mock._inject(alert_id="ALM-E3", title="范围外告警", category="Nginx",
                                severity="warning", app_id="APP-ZZ", alert_object="zz-host")
    assert _pull(client)["pulled"] == 3


def test_visibility_matrix_and_projection(client):
    iid = _setup_with_scope(client)
    _seed_three(client)

    # 2026-07-31 拍板：未命中规则的告警丢弃不进用户清单——普通清单只见本人接管过的 E1
    rows = _events(client, instance_id=iid)
    by_no = {r["alert_no"]: r for r in rows}
    assert set(by_no) == {"ALM-E1"}, "未命中的 E2/E3 不应进用户清单"
    e1 = by_no["ALM-E1"]
    assert e1["takeover_status"] == "processing" and e1["alert_status"] == "assigned"
    assert e1["detail_url"] == "https://alert.example/e1"
    assert e1["alert_object"] == "mysql-prod-03" and e1["appid"] == "APP-A"
    assert _events(client, instance_id=iid, takeover="none") == []  # 清单无未命中行

    # 弹窗预览（since_days）保留旧口径：scope 内的未命中 E2 可见（订阅面评估），范围外 E3 仍不可见
    pv = {r["alert_no"]: r for r in _events(client, instance_id=iid, since_days=3)}
    assert set(pv) == {"ALM-E1", "ALM-E2"}
    assert pv["ALM-E2"]["takeover_status"] == "none" and pv["ALM-E2"]["alert_status"] == "unassigned"
    assert pv["ALM-E2"]["run_clickable"] is False

    # 他人视角：未白名单直接 403（比空清单更强的隔离）
    other = client.get(f"{BASE}/events", headers=OTHER_HEADERS)
    assert other.status_code == 403


def test_admin_full_view_and_per_user_projection(client):
    iid = _setup_with_scope(client)
    _seed_three(client)

    full = unwrap(client.get("/api/openops/v1/admin/alerts/events",
                             headers=ADMIN_HEADERS))["items"]
    assert {r["alert_no"] for r in full} == {"ALM-E1", "ALM-E2", "ALM-E3"}  # 全量含范围外
    e1 = next(r for r in full if r["alert_no"] == "ALM-E1")
    assert e1["takeover_status"] == "processing"  # 任意 owner 最新单投影

    per_other = unwrap(client.get("/api/openops/v1/admin/alerts/events",
                                  headers=ADMIN_HEADERS,
                                  params={"user_id": "0099other"}))["items"]
    e1_other = next(r for r in per_other if r["alert_no"] == "ALM-E1")
    assert e1_other["takeover_status"] == "none"  # 按该用户视角：他没接管

    assert client.get(f"{BASE.replace('/alerts', '/admin/alerts')}/events",
                      headers=USER_HEADERS).status_code == 403


def test_filters_search_and_status_projection(client):
    iid = _setup_with_scope(client)
    _seed_three(client)
    alert_platform_mock._inject(alert_id="ALM-E4", title="已恢复的MySQL告警", category="MySQL",
                                severity="critical", app_id="APP-C", status="resolved",
                                alert_object="mysql-prod-09")
    _pull(client)

    # resolved → 已关闭（未命中行仅预览可见——清单已按拍板丢弃）
    assert _events(client, instance_id=iid, alert_status="closed") == []
    closed = _events(client, instance_id=iid, alert_status="closed", since_days=3)
    assert [r["alert_no"] for r in closed] == ["ALM-E4"]
    assert closed[0]["ended_at"] is not None and closed[0]["duration_s"] is not None

    # ignored 单 → 接管状态回到 none、告警状态回未分派（留痕单不算接管）
    inc = unwrap(client.get(f"{BASE}/incidents", headers=USER_HEADERS,
                            params={"instance_id": iid}))["items"][0]
    unwrap(client.post(f"{BASE}/incidents/{inc['incident_id']}:ignore", headers=USER_HEADERS,
                       json={"client_request_id": _crid()}))
    e1 = next(r for r in _events(client, instance_id=iid) if r["alert_no"] == "ALM-E1")
    assert e1["takeover_status"] == "none" and e1["alert_status"] == "unassigned"

    # category / severity / search 三筛（未命中行的断言走预览口径）
    assert all(r["category"] == "MySQL"
               for r in _events(client, instance_id=iid, category="MySQL"))
    assert {r["alert_no"] for r in _events(client, instance_id=iid, severity="warning",
                                           since_days=3)} == {"ALM-E2"}
    assert [r["alert_no"] for r in _events(client, instance_id=iid, search="mysql-prod-03")] == ["ALM-E1"]
    assert [r["alert_no"] for r in _events(client, instance_id=iid, search="ALM-E2",
                                           since_days=3)] == ["ALM-E2"]
    assert _events(client, instance_id=iid, search="不存在关键词") == []


def test_since_days_window_for_rule_preview(client):
    """配置弹窗「最近 3 天同类告警预览」的窗口语义：since_days 过滤 + 类型×级别组合。"""
    import os

    import psycopg

    iid = _setup_with_scope(client)
    _seed_three(client)
    # 老化 E1 到 5 天前（同步连接直改，test_ddl_008 同法）
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("update sre_alert_event set last_seen_at = now() - interval '5 days' "
                     "where external_alert_id = 'ALM-E1'")

    recent = {r["alert_no"] for r in _events(client, instance_id=iid, since_days=3)}
    assert "ALM-E1" not in recent and "ALM-E2" in recent  # 窗口外被滤、窗口内保留
    assert "ALM-E1" in {r["alert_no"] for r in _events(client, instance_id=iid)}  # 不带窗口仍可见

    # 预览的典型调用形态：类型 + 级别组 + 3 天窗
    pv = _events(client, instance_id=iid, since_days=3, category="Nginx", severity="warning")
    assert {r["alert_no"] for r in pv} == {"ALM-E2"}
    assert client.get(f"{BASE}/events", headers=USER_HEADERS,
                      params={"since_days": 99}).status_code == 422  # 上限 30 由 Query 校验
