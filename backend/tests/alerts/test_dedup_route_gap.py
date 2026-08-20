"""去重滚动窗口补路由（2026-08-19：多用户「只有一人生效」修复）。

touch 每次推进 last_seen → 持续重发（间隔<窗口）的告警窗口**永不过期**（滚动窗口）；
窗口内 return 又在路由之前——首见时刻没接上的实例（规则后建/当时被范围闸拦）会被
永久拦截。修复=窗口内对「命中规则但从未建过单」的实例补走完整路由（范围/附着/
冷却/上限闸照过）；接过的实例（含已完结单）不补，重建节奏归组冷却语义。
"""
from __future__ import annotations

import time

import pytest

from conftest import ADMIN_HEADERS, OTHER_HEADERS, USER_HEADERS, create_instance, unwrap
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


def _rule(client, headers, iid) -> None:
    unwrap(client.post(f"{BASE}/rules", headers=headers,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "name": "MySQL 接管", "categories": ["MySQL"]}))


def _other_instance(client) -> str:
    import asyncio

    from infra.repositories import users as users_repo

    # conftest 只给 0099other 加了告警白名单；建实例还需平台准入白名单
    asyncio.run(users_repo.upsert_user("0099other", "Other", "user"))
    asyncio.run(users_repo.add_whitelist("0099other", "test"))
    templates = unwrap(client.get("/api/openops/v1/templates/available", headers=OTHER_HEADERS))
    created = unwrap(client.post("/api/openops/v1/agent-teams", headers=OTHER_HEADERS,
                                 json={"client_request_id": _crid(),
                                       "template_version_id": templates[0]["template_version_id"],
                                       "name": "other 的 Agent", "workspace_id": "ws_pay_abc"}))
    return str(created["instance"]["instance_id"])


def _incidents(client, headers, iid) -> list[dict]:
    return unwrap(client.get(f"{BASE}/incidents", headers=headers,
                             params={"instance_id": iid}))["items"]


def _inject_same() -> None:
    alert_platform_mock._inject(title="MySQL 主库延迟>5s", category="MySQL",
                                severity="critical", app_id="APP-A",
                                fingerprint="fp_route_gap")


def test_window_resend_routes_to_late_subscriber(client):
    """主场景：user1 首见建单 → user2 后建规则 → 窗口内重发给 user2 补建单
    （同一条重发 deduped 与 queued 并存）；user1 不重复；再重发两家都不再新建。"""
    iid1 = create_instance(client)["instance_id"]
    _rule(client, USER_HEADERS, iid1)
    _inject_same()
    assert _pull(client)["queued"] == 1

    iid2 = _other_instance(client)
    _rule(client, OTHER_HEADERS, iid2)
    _inject_same()
    counters = _pull(client)
    assert counters["deduped"] == 1 and counters["queued"] == 1  # 事件级合并 + user2 补建单

    assert len(_incidents(client, USER_HEADERS, iid1)) == 1  # user1 不重复建
    inc2 = _incidents(client, OTHER_HEADERS, iid2)
    assert len(inc2) == 1 and inc2[0]["app_id"] == "APP-A"

    # takeovers 的 owner 可见性（2026-08-20 审查：过滤此前零覆盖）：同一事件两家各一单，
    # 各自清单行只见本人的姊妹单——他人单混入即隐私口径（他人单不投影）被绕过
    inc1_id = _incidents(client, USER_HEADERS, iid1)[0]["incident_id"]
    ev1 = unwrap(client.get(f"{BASE}/events", headers=USER_HEADERS))["items"][0]
    assert [t["incident_id"] for t in ev1["takeovers"]] == [inc1_id]
    ev2 = unwrap(client.get(f"{BASE}/events", headers=OTHER_HEADERS))["items"][0]
    assert [t["incident_id"] for t in ev2["takeovers"]] == [inc2[0]["incident_id"]]

    _inject_same()
    counters = _pull(client)
    assert counters["deduped"] == 1 and counters["queued"] == 0  # 双方都接过：回到纯合并
    assert len(_incidents(client, USER_HEADERS, iid1)) == 1
    assert len(_incidents(client, OTHER_HEADERS, iid2)) == 1


def test_route_gap_still_respects_scope_gate(client, monkeypatch):
    """补路由不是范围后门：user2 实例 scope 不含该 app → 补路由被 out_of_scope 拦。"""
    from app import scope_service

    orig = scope_service.peek_effective_appids

    async def _peek(uid, inst, **kw):
        if uid == "0099other":
            return ["APP-Z"]  # user2 的范围不含 APP-A
        return await orig(uid, inst, **kw)

    monkeypatch.setattr(scope_service, "peek_effective_appids", _peek)

    iid1 = create_instance(client)["instance_id"]
    _rule(client, USER_HEADERS, iid1)
    _inject_same()
    assert _pull(client)["queued"] == 1

    iid2 = _other_instance(client)
    _rule(client, OTHER_HEADERS, iid2)
    _inject_same()
    counters = _pull(client)
    assert counters["deduped"] == 1 and counters["out_of_scope"] == 1 and counters["queued"] == 0
    assert _incidents(client, OTHER_HEADERS, iid2) == []


def test_taken_instance_not_rerouted_after_finish(client):
    """接过的实例不再补（含已完结/已忽略单）：重建节奏归组冷却语义，补路由不绕过它。"""
    iid1 = create_instance(client)["instance_id"]
    _rule(client, USER_HEADERS, iid1)
    _inject_same()
    assert _pull(client)["queued"] == 1
    inc = _incidents(client, USER_HEADERS, iid1)[0]
    unwrap(client.post(f"{BASE}/incidents/{inc['incident_id']}:ignore", headers=USER_HEADERS,
                       json={"client_request_id": _crid()}))

    _inject_same()
    counters = _pull(client)
    assert counters["deduped"] == 1 and counters["queued"] == 0  # linked 含 user1：不重建
    assert len(_incidents(client, USER_HEADERS, iid1)) == 1
