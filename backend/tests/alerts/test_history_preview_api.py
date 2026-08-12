"""规则编辑器第二步「历史告警预览」端点：平台主路径 / 参数校验 / 归属 / 降级本地库 /
projectIds 实时探询（scope_service.peek_effective_appids，2026-08-10 内网 [Required] 实证）。

mock 档 `_impl()` 走 alert_platform_mock.list_history 样本（相对日期 D1/D2 vs D5/D6，
appid=APP-A/B/C 与 omodel mock ws_pay_abc 同词表——peek 出的 projectIds 过滤后命中）；
降级路径用 monkeypatch 打炸对应环节模拟故障。
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
                "alert_status", "description", "started_at", "detail_url", "enterprise_id"):
        assert key in row, f"预览行缺 {key}"
    assert row["category"] == "MySQL"
    # 企业分发拼链（mock 样本：APP-A→OP 32×8、APP-B→KWE 32×1、APP-C→其他不可跳）
    ent_urls = {r["enterprise_id"]: r["detail_url"] for r in got7["items"]}
    assert all(("alarmCode=" in u) for e, u in ent_urls.items() if e in ("8" * 32, "1" * 32))

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


def test_omodel_down_falls_back_without_platform_call(client, monkeypatch):
    """peek 拿不到范围（omodel 不可达）→ 不打必败的平台请求，直接 local_fallback。"""
    from infra.external import alert_platform_client

    iid = _setup_instance(client)

    async def _no_scope(*a, **k):
        raise RuntimeError("omodel down")

    calls = {"n": 0}

    async def _count_history(**k):
        calls["n"] += 1
        return {"rows": [], "total": 0}

    monkeypatch.setattr("infra.external.omodel_client.resolve_scope", _no_scope)
    monkeypatch.setattr(alert_platform_client, "list_history", _count_history)
    from app import scope_service
    scope_service._reset_cache()  # 防上例缓存穿透

    got = unwrap(_preview(client, iid, categories="MySQL", since_days=7))
    assert got["source"] == "local_fallback"
    assert calls["n"] == 0, "拿不到 projectIds 时不应调用平台历史接口（对端四选一必填必败）"


def test_scope_override_seam_narrows_platform_rows(client, monkeypatch):
    """联调缝：OPENOPS_SCOPE_OVERRIDE_APPIDS 直通 peek → 平台样本按 projectIds 收窄。"""
    iid = _setup_instance(client)
    monkeypatch.setenv("OPENOPS_SCOPE_OVERRIDE_APPIDS", "APP-B")
    got = unwrap(_preview(client, iid, categories="MySQL,PostgreSQL,Docker", since_days=7))
    assert got["source"] == "platform"
    assert got["items"] and all(r["appid"] == "APP-B" for r in got["items"])


def test_peek_writes_no_scope_snapshot(client):
    """守卫：预览的 peek 是只读探询，绝不产生 scope 快照（快照只属于任务边界）。"""
    import asyncio as _aio

    from infra.repositories import runs as runs_repo

    iid = _setup_instance(client)
    unwrap(_preview(client, iid, categories="MySQL", since_days=7))
    snap = _aio.run(runs_repo.latest_scope_snapshot_by_instance(iid))
    assert snap is None, "peek 不该写 scope_snapshot"
