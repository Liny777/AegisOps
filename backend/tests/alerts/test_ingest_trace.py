"""定向追踪缝 OPENOPS_ALERT_TRACE（联调定位「我这条告警为什么没出现在清单」）。

命中 alarmId 或归属应用任一 → 决策链逐站 [alerts][trace] warning：
消费到 → 命中/未命中 → 范围过滤 → 去向（入队/附着/冷却/skip）。不配置零输出。
"""
from __future__ import annotations

import logging
import time

import pytest

from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, unwrap
from infra.external import alert_platform_mock

BASE = "/api/openops/v1/alerts"
LOGGER = "openops.alerts.ingest"


@pytest.fixture(autouse=True)
def _clean(client, monkeypatch):
    from app import scope_service

    alert_platform_mock._reset()
    scope_service._reset_cache()  # 防别的文件写的 30s 缓存污染范围断言
    monkeypatch.delenv("OPENOPS_ALERT_TRACE", raising=False)
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


def test_trace_by_alarm_id_shows_consume_and_unmatched(client, monkeypatch, caplog):
    """命中 alarmId：打「消费到」（证明 Kafka 到货+解析结果）与「未命中」及排查提示。"""
    monkeypatch.setenv("OPENOPS_ALERT_TRACE", "ALM-TR1")
    alert_platform_mock._inject(alert_id="ALM-TR1", title="Nginx 4xx 升高", category="Nginx",
                                severity="warning", app_id="APP-A")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        counters = _pull(client)
    assert counters["unmatched"] >= 1
    assert "消费到 alarmId=ALM-TR1" in caplog.text and "app_id=APP-A" in caplog.text
    assert "未命中任何启用规则" in caplog.text


def test_trace_by_app_shows_scope_block(client, monkeypatch, caplog):
    """命中应用 ID + 范围外：打出实例范围与归属集合，一眼见口径差。"""
    _setup_instance(client)
    monkeypatch.setenv("OPENOPS_ALERT_TRACE", "APP-ZZ")
    alert_platform_mock._inject(title="MySQL 主库延迟>5s", category="MySQL",
                                severity="fatal", app_id="APP-ZZ")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        counters = _pull(client)
    assert counters["out_of_scope"] == 1
    assert "范围过滤拦截" in caplog.text
    assert "APP-ZZ" in caplog.text and "APP-A" in caplog.text  # 归属 与 实例范围 都可见


def test_trace_queued_destination(client, monkeypatch, caplog):
    """命中且入队：终点站打 incident 编号。"""
    _setup_instance(client)
    monkeypatch.setenv("OPENOPS_ALERT_TRACE", "ALM-TR3")
    alert_platform_mock._inject(alert_id="ALM-TR3", title="MySQL 磁盘满", category="MySQL",
                                severity="critical", app_id="APP-A")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        counters = _pull(client)
    assert counters["queued"] == 1
    assert "入队成功" in caplog.text and "ALM-TR3" in caplog.text


def test_no_trace_env_silent(client, caplog):
    """不配置：全链零 [alerts][trace] 输出（热路径零噪音）。"""
    _setup_instance(client)
    alert_platform_mock._inject(title="MySQL 磁盘满", category="MySQL",
                                severity="critical", app_id="APP-A")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        counters = _pull(client)
    assert counters["queued"] == 1
    assert "[alerts][trace]" not in caplog.text
