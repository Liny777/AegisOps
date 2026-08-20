"""匹配面应用范围过滤（宁漏勿越权 fail-closed，2026-08-11 拍板）。

规则命中只代表类型/级别匹配，还须 告警归属应用（appIdList 全集，2026-08-13 改交集
判定——内网多 projectId 消息只看首元素会误拦）与实例的 omodel 应用范围有交集：
peek（override 缝→30s 缓存→实时）→ scope 快照兜底 → 都拿不到 = 整实例拦截。
测试实例绑 ws_pay_abc → mock omodel 范围 APP-A/B/C。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

import pytest

from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, unwrap
from infra.external import alert_platform_mock

BASE = "/api/openops/v1/alerts"


@pytest.fixture(autouse=True)
def _clean(client):
    """清 mock 变更流 + 清 scope 30s 缓存（别的文件跑过 resolve_for_task 会写同 key 缓存，
    污染本文件「omodel 挂掉」的用例——peek 会命中缓存拿到范围，fail-closed 分支就测不到）。"""
    from app import scope_service

    alert_platform_mock._reset()
    scope_service._reset_cache()
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


def _incidents(client, iid: str) -> list[dict]:
    return unwrap(client.get(f"{BASE}/incidents", headers=USER_HEADERS,
                             params={"instance_id": iid}))["items"]


def test_out_of_scope_app_blocked(client):
    """规则命中但 app 不在实例范围（APP-ZZ ∉ APP-A/B/C）→ 拦截计数，不建单。"""
    iid = _setup_instance(client)
    alert_platform_mock._inject(alert_id="ALM-SCOPE-1", title="MySQL 主库延迟>5s",
                                category="MySQL", severity="fatal", app_id="APP-ZZ")
    counters = _pull(client)
    assert counters["out_of_scope"] == 1 and counters["queued"] == 0
    assert counters["unmatched"] == 0  # 语义区分：命中了规则但越权 ≠ 未命中
    assert _incidents(client, iid) == []
    # 被拦的行仍落库留痕（未命中不存档的例外面）：排查抓手 = matched_only=false 后门
    # 看全量留痕（默认口径只显示命中+白名单行，R14 排查须显式开）
    stored = unwrap(client.get("/api/openops/v1/admin/alerts/events", headers=ADMIN_HEADERS,
                               params={"matched_only": "false"}))["items"]
    assert any(r["alert_no"] == "ALM-SCOPE-1" for r in stored)


def test_appid_list_intersection_allows(client):
    """29.11 多值 appIdList：首元素范围外但列表含 APP-A → 交集非空放行（只看首值会误拦）。"""
    iid = _setup_instance(client)
    alert_platform_mock._inject(title="MySQL 主库延迟>5s", category="MySQL",
                                severity="fatal", app_id="APP-ZZ",
                                annotations={"app_id_list": '["APP-ZZ", "APP-A"]'})
    counters = _pull(client)
    assert counters["queued"] == 1 and counters["out_of_scope"] == 0
    assert len(_incidents(client, iid)) == 1


def test_missing_app_id_blocked(client):
    """app_id 缺失无法判定归属 → 同拦（R14：联调期观察 out_of_scope 计数定位缺失面）。"""
    iid = _setup_instance(client)
    alert_platform_mock._inject(title="MySQL 磁盘满", category="MySQL", severity="critical")
    counters = _pull(client)
    assert counters["out_of_scope"] == 1 and counters["queued"] == 0
    assert _incidents(client, iid) == []


def test_scope_unavailable_fails_closed_with_warning(client, monkeypatch, caplog):
    """omodel 挂 + 无历史快照 → 整实例拦截 + 宁漏勿越权 warning 留痕（可观测）。"""
    iid = _setup_instance(client)

    async def _boom(*a, **k):
        raise RuntimeError("omodel down")

    from infra.external import omodel_client

    monkeypatch.setattr(omodel_client, "resolve_scope", _boom)
    alert_platform_mock._inject(title="MySQL 连接飙升", category="MySQL",
                                severity="critical", app_id="APP-A")
    with caplog.at_level(logging.WARNING, logger="openops.alerts.ingest"):
        counters = _pull(client)
    assert counters["out_of_scope"] == 1 and counters["queued"] == 0
    assert "宁漏勿越权" in caplog.text
    assert _incidents(client, iid) == []


def test_scope_snapshot_fallback_when_omodel_down(client, monkeypatch):
    """omodel 挂但有历史快照（revision 与实例行一致）→ 用快照边界照常接管
    （夜间降级兜底，不因 omodel 抖动漏单）。"""
    iid = _setup_instance(client)
    from infra.repositories import agent_teams
    from infra.repositories import runs as runs_repo

    inst = asyncio.run(agent_teams.get_instance(iid))
    asyncio.run(runs_repo.insert_scope_snapshot(
        user_id="0026demo01", instance_id=iid, run_id=str(uuid.uuid4()), task_id="t_snap",
        workspace_id="ws_pay_abc", scope_revision=str(inst["scope_revision"]), appids=["APP-A"],
        omodel_request_id="req_snap", compute_reason="test"))

    async def _boom(*a, **k):
        raise RuntimeError("omodel down")

    from infra.external import omodel_client

    monkeypatch.setattr(omodel_client, "resolve_scope", _boom)
    alert_platform_mock._inject(title="MySQL 主库延迟>5s", category="MySQL",
                                severity="fatal", app_id="APP-A")
    counters = _pull(client)
    assert counters["queued"] == 1 and counters["out_of_scope"] == 0
    items = _incidents(client, iid)
    assert len(items) == 1 and items[0]["app_id"] == "APP-A"


def test_stale_snapshot_rejected_after_scope_change(client, monkeypatch, caplog):
    """范围变更后（实例行 revision 已推进）旧快照作废：omodel 挂也不采信旧边界——
    fail-closed 拦截 + warning 留痕（2026-08-19：改范围后告警仍按旧 scope 接管的修复）。"""
    iid = _setup_instance(client)
    from infra.repositories import agent_teams
    from infra.repositories import runs as runs_repo

    asyncio.run(runs_repo.insert_scope_snapshot(
        user_id="0026demo01", instance_id=iid, run_id=str(uuid.uuid4()), task_id="t_old",
        workspace_id="ws_pay_abc", scope_revision="rev-before-change", appids=["APP-A"],
        omodel_request_id="req_old", compute_reason="test"))
    # 模拟写路径推进：workspace 范围内容变更 → 引用实例 revision 到新值
    asyncio.run(agent_teams.bump_scope_revision_by_workspace(
        "ws_pay_abc", "rev-after-change", "test"))

    async def _boom(*a, **k):
        raise RuntimeError("omodel down")

    from infra.external import omodel_client

    monkeypatch.setattr(omodel_client, "resolve_scope", _boom)
    alert_platform_mock._inject(title="MySQL 主库延迟>5s", category="MySQL",
                                severity="fatal", app_id="APP-A")
    with caplog.at_level(logging.WARNING, logger="openops.alerts.ingest"):
        counters = _pull(client)
    assert counters["out_of_scope"] == 1 and counters["queued"] == 0
    assert "历史快照作废" in caplog.text
    assert _incidents(client, iid) == []


def test_resolve_from_last_snapshot_rejects_stale_revision(client):
    """start_task 夜间降级同口径（2026-08-19）：revision 一致 → 降级可用；
    实例行 revision 被写路径推进后 → 旧快照不得用于起诊断（None，调用方按原错抛）。"""
    iid = _setup_instance(client)
    from app import scope_service
    from infra.repositories import agent_teams
    from infra.repositories import runs as runs_repo

    inst = asyncio.run(agent_teams.get_instance(iid))
    asyncio.run(runs_repo.insert_scope_snapshot(
        user_id="0026demo01", instance_id=iid, run_id=str(uuid.uuid4()), task_id="t_deg",
        workspace_id="ws_pay_abc", scope_revision=str(inst["scope_revision"]), appids=["APP-A"],
        omodel_request_id="req_deg", compute_reason="test"))
    ctx = asyncio.run(scope_service.resolve_from_last_snapshot(
        "0026demo01", inst, str(uuid.uuid4()), "t_deg2", "trace"))
    assert ctx is not None and ctx["degraded"] is True and ctx["effective_appids"] == ["APP-A"]

    asyncio.run(agent_teams.bump_scope_revision_by_workspace("ws_pay_abc", "rev-changed", "test"))
    inst2 = asyncio.run(agent_teams.get_instance(iid))
    assert asyncio.run(scope_service.resolve_from_last_snapshot(
        "0026demo01", inst2, str(uuid.uuid4()), "t_deg3", "trace")) is None


def test_scope_override_seam(client, monkeypatch):
    """OPENOPS_SCOPE_OVERRIDE_APPIDS 联调缝优先级最高：覆盖后 APP-ZZ 也可入队。"""
    iid = _setup_instance(client)
    monkeypatch.setenv("OPENOPS_SCOPE_OVERRIDE_APPIDS", "APP-ZZ")
    alert_platform_mock._inject(title="MySQL 主库延迟>5s", category="MySQL",
                                severity="fatal", app_id="APP-ZZ")
    counters = _pull(client)
    assert counters["queued"] == 1 and counters["out_of_scope"] == 0
    assert len(_incidents(client, iid)) == 1
