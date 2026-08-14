"""v3 分池并发闸（§5.1/§5.2，2026-08-15 落地）：交互硬保底 + 弹性额度 + 告警硬保底。

pool_admit 是两侧（交互准入 / 告警 dispatcher）共用的单一实现——矩阵在此钉死运营口径：
交互上限 5+5=10、告警上限 2+5=7；交互 5 恒保底、告警 2 恒保底。
"""
from __future__ import annotations

import time

import pytest

from app.run_state_service import pool_admit

CFG = {"model_total_concurrency": 12, "reserve_interactive": 5, "reserve_alert": 2}


def test_interactive_hard_reserve_immune_to_alert_load():
    """告警占满自己的上限（7）时，交互仍有 5 个硬保底名额。"""
    for n_user in range(5):
        assert pool_admit("user", n_user, 7, CFG), f"交互第 {n_user + 1} 个应走硬保底放行"
    assert not pool_admit("user", 5, 7, CFG), "保底用尽且弹性满（5+7=12）应拒绝"


def test_alert_hard_reserve_immune_to_user_load():
    """交互占满自己的上限（10）时，告警仍有 2 个硬保底名额。"""
    for n_alert in range(2):
        assert pool_admit("alert", 10, n_alert, CFG)
    assert not pool_admit("alert", 10, 2, CFG), "告警保底用尽且弹性满应拒绝"


def test_elastic_shared_first_come_first_served():
    """弹性额度（12−5−2=5）两池共抢：先到先得。"""
    assert pool_admit("user", 5, 2, CFG)      # 5+2=7 < 12：交互超保底走弹性
    assert pool_admit("alert", 5, 6, CFG)     # 5+6=11 < 12：告警超保底走弹性
    assert not pool_admit("user", 6, 6, CFG)  # 12 满：拒
    assert not pool_admit("alert", 6, 6, CFG)


def test_operational_ceilings():
    """运营口径断言（写进手册的数字）：交互上限 10、告警上限 7。"""
    assert pool_admit("user", 9, 0, CFG) and not pool_admit("user", 10, 0, CFG)
    assert pool_admit("alert", 0, 6, CFG) and not pool_admit("alert", 0, 7, CFG)


def test_zero_reserve_means_all_elastic():
    """reserve=0 合法：该池无保底、全走弹性（管理台可调出的边界形态）。"""
    cfg = {**CFG, "reserve_interactive": 0}
    assert not pool_admit("user", 0, 12, cfg), "无保底且弹性满：第一个交互任务也进不来"
    assert pool_admit("user", 0, 11, cfg)


def test_acquire_interactive_slot_uses_pool(client, monkeypatch):
    """_acquire_interactive_slot 三段式：每用户闸未满但全局分池满 → 拒绝（进排队）。"""
    import asyncio

    from app import run_state_service
    from runtime import task_registry

    monkeypatch.setattr(task_registry, "running_count", lambda uid, origin=None: 0)

    async def _zero(_uid):
        return 0

    monkeypatch.setattr(run_state_service.task_states, "count_running", _zero)
    monkeypatch.setattr(task_registry, "running_total",
                        lambda origin: {"user": 5, "alert": 7}[origin])
    cfg = {"per_user_running_task_limit": 2, **CFG}
    assert asyncio.run(run_state_service._acquire_interactive_slot("u1", cfg)) is False

    monkeypatch.setattr(task_registry, "running_total",
                        lambda origin: {"user": 4, "alert": 7}[origin])
    assert asyncio.run(run_state_service._acquire_interactive_slot("u1", cfg)) is True


def test_dispatcher_gate_blocks_when_pool_full(client, monkeypatch):
    """dispatcher 告警侧：硬上限内但分池满 → 本轮不取单（queued 单原地等空位）。"""
    import asyncio

    from alerts import dispatcher
    from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, unwrap
    from infra.external import alert_platform_mock
    from runtime import task_registry

    iid = create_instance(client)["instance_id"]
    unwrap(client.post("/api/openops/v1/alerts/rules", headers=USER_HEADERS,
                       json={"client_request_id": f"crid_{time.time_ns()}",
                             "agent_team_instance_id": iid,
                             "name": "MySQL 接管", "categories": ["MySQL"]}))
    alert_platform_mock._reset()
    alert_platform_mock._inject(title="MySQL 分池验证", category="MySQL",
                                severity="fatal", app_id="APP-A")
    assert unwrap(client.post("/api/openops/v1/admin/alerts:pull",
                              headers=ADMIN_HEADERS))["counters"]["queued"] == 1

    # 告警池 2 在跑（保底用尽）且交互 10 把弹性吃满 → 分池闸拒绝取单
    monkeypatch.setattr(task_registry, "running_total",
                        lambda origin: {"user": 10, "alert": 2}[origin])
    assert asyncio.run(dispatcher.dispatch_once()) == 0
    items = unwrap(client.get("/api/openops/v1/alerts/incidents", headers=USER_HEADERS,
                              params={"instance_id": iid, "state": "queued"}))["items"]
    assert len(items) == 1, "分池满时单应原地排队，不被消费"
