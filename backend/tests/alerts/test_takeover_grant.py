"""告警接管白名单（算力保护，fail-closed）：配置面 403 / 匹配面断流 / 派发面 skip /
admin grants CRUD+审计 / platform_admin 豁免 / access 探询。

conftest 的 autouse fixture 给测试用户开了 grant——本文件的用例显式关掉再测。
"""
from __future__ import annotations

import asyncio
import time

from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, unwrap
from infra.external import alert_platform_mock

BASE = "/api/openops/v1/alerts"


def _crid() -> str:
    return f"crid_{time.time_ns()}"


def _set_grant(user_id: str, granted: bool) -> None:
    from alerts import repository as repo

    asyncio.run(repo.set_user_grant(user_id, granted, "test"))


def _mk_rule(client, iid: str, name: str):
    return client.post(f"{BASE}/rules", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "name": name, "categories": ["MySQL"]})


def test_access_and_config_gate(client):
    """未开通：access.granted=False，建规/预览全 403 且文案可引导；开通后放行。"""
    iid = create_instance(client)["instance_id"]
    _set_grant("0026demo01", False)

    assert unwrap(client.get(f"{BASE}/access", headers=USER_HEADERS))["granted"] is False
    r1 = _mk_rule(client, iid, "拦截")
    assert r1.status_code == 403 and "联系平台管理员" in r1.text
    r3 = client.get(f"{BASE}/history-preview", headers=USER_HEADERS,
                    params={"instance_id": iid, "categories": "MySQL"})
    assert r3.status_code == 403

    _set_grant("0026demo01", True)
    assert unwrap(client.get(f"{BASE}/access", headers=USER_HEADERS))["granted"] is True
    assert _mk_rule(client, iid, "放行").status_code < 400


def test_admin_bypass_and_grants_api(client):
    """platform_admin 天然豁免；grants API 往返 + 幂等 + 名单读取。"""
    assert unwrap(client.get(f"{BASE}/access", headers=ADMIN_HEADERS))["granted"] is True

    got = unwrap(client.post(f"{BASE.replace('/alerts', '')}/admin/alerts/grants:update".replace(
        "/api/openops/v1/admin", "/api/openops/v1/admin"), headers=ADMIN_HEADERS,
        json={"client_request_id": _crid(), "user_id": "u_cap_1", "granted": True}))
    assert got == {"user_id": "u_cap_1", "granted": True}
    # 幂等重开
    unwrap(client.post("/api/openops/v1/admin/alerts/grants:update", headers=ADMIN_HEADERS,
                       json={"client_request_id": _crid(), "user_id": "u_cap_1", "granted": True}))
    items = unwrap(client.get("/api/openops/v1/admin/alerts/grants", headers=ADMIN_HEADERS))["items"]
    assert any(g["user_id"] == "u_cap_1" for g in items)

    unwrap(client.post("/api/openops/v1/admin/alerts/grants:update", headers=ADMIN_HEADERS,
                       json={"client_request_id": _crid(), "user_id": "u_cap_1", "granted": False}))
    items = unwrap(client.get("/api/openops/v1/admin/alerts/grants", headers=ADMIN_HEADERS))["items"]
    assert not any(g["user_id"] == "u_cap_1" for g in items)


def test_revoked_user_rules_leave_matching_plane(client):
    """匹配面断流：开通期建好规则 → 关闭开通 → 注入命中告警 → matched=0 不落库。"""
    iid = create_instance(client)["instance_id"]
    assert _mk_rule(client, iid, "MySQL 接管").status_code < 400

    _set_grant("0026demo01", False)
    alert_platform_mock._reset()
    alert_platform_mock._inject(title="MySQL 主库延迟>5s", category="MySQL", severity="fatal", app_id="APP-A")
    counters = unwrap(client.post("/api/openops/v1/admin/alerts:pull",
                                  headers=ADMIN_HEADERS))["counters"]
    assert counters["queued"] == 0, "名单外用户的规则不应参与匹配"
    _set_grant("0026demo01", True)


def test_queued_incident_skipped_after_revoke(client):
    """派发面双保险：排队后被移出名单 → dispatch 置 skipped 留痕，不再建诊断。"""
    iid = create_instance(client)["instance_id"]
    assert _mk_rule(client, iid, "MySQL 接管").status_code < 400
    alert_platform_mock._reset()
    alert_platform_mock._inject(title="MySQL 磁盘满", category="MySQL", severity="critical", app_id="APP-A")
    assert unwrap(client.post("/api/openops/v1/admin/alerts:pull",
                              headers=ADMIN_HEADERS))["counters"]["queued"] == 1

    _set_grant("0026demo01", False)
    started = unwrap(client.post("/api/openops/v1/admin/alerts:dispatch",
                                 headers=ADMIN_HEADERS))["started"]
    assert started == 0
    items = unwrap(client.get(f"{BASE}/incidents", headers=USER_HEADERS,
                              params={"instance_id": iid}))["items"]
    assert items and items[0]["status"] == "skipped"
    _set_grant("0026demo01", True)


def test_admin_overview_exposes_kafka_health(client):
    """观测补强：overview 带 kafka 消费心跳段（初始态 started_at=None=循环未起）。"""
    got = unwrap(client.get("/api/openops/v1/admin/alerts/overview", headers=ADMIN_HEADERS))
    assert "kafka" in got
    for key in ("started_at", "last_batch_at", "last_counters", "batches", "consumed", "skipped_bad"):
        assert key in got["kafka"]
    assert got["kafka"]["started_at"] is None  # 测试环境不起真消费循环
