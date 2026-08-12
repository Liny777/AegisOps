"""匹配面应用范围过滤（宁漏勿越权 fail-closed，2026-08-11 拍板）。

规则命中只代表类型/级别匹配，还须 alert.app_id ∈ 实例的 omodel 应用范围：
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
    unwrap(client.post(f"{BASE}/subscription:update", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "enabled": True}))
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
    alert_platform_mock._inject(title="MySQL 主库延迟>5s", category="MySQL",
                                severity="fatal", app_id="APP-ZZ")
    counters = _pull(client)
    assert counters["out_of_scope"] == 1 and counters["queued"] == 0
    assert counters["unmatched"] == 0  # 语义区分：命中了规则但越权 ≠ 未命中
    assert _incidents(client, iid) == []


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
    """omodel 挂但有历史快照 → 用快照边界照常接管（夜间降级兜底，不因 omodel 抖动漏单）。"""
    iid = _setup_instance(client)
    from infra.repositories import runs as runs_repo

    asyncio.run(runs_repo.insert_scope_snapshot(
        user_id="0026demo01", instance_id=iid, run_id=str(uuid.uuid4()), task_id="t_snap",
        workspace_id="ws_pay_abc", scope_revision="rev-snap", appids=["APP-A"],
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


def test_scope_override_seam(client, monkeypatch):
    """OPENOPS_SCOPE_OVERRIDE_APPIDS 联调缝优先级最高：覆盖后 APP-ZZ 也可入队。"""
    iid = _setup_instance(client)
    monkeypatch.setenv("OPENOPS_SCOPE_OVERRIDE_APPIDS", "APP-ZZ")
    alert_platform_mock._inject(title="MySQL 主库延迟>5s", category="MySQL",
                                severity="fatal", app_id="APP-ZZ")
    counters = _pull(client)
    assert counters["queued"] == 1 and counters["out_of_scope"] == 0
    assert len(_incidents(client, iid)) == 1
