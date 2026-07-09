"""B8 沙箱执行面测试（fake 后端；真 Docker 用例见 test_sandbox_docker 标记）。

覆盖 31 号：SBX-001（首 run 建容器 / 末 run 关闭后 idle→TTL 回收）、SBX-002（同用户复用）、
CANCEL-007（容量满开 run 被拒）。生命周期直接驱动 SandboxExecutor（不依赖 Docker）。
"""
from __future__ import annotations

import asyncio

import pytest
from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, create_run, unwrap
from sandbox.executor import executor as sandbox_executor

_CFG = {"max_user_containers_per_host": 2, "user_container_idle_ttl_minutes": 15,
        "container_cpu_limit": 0.5, "container_memory_limit_mib": 2048}


def test_sbx_001_run_open_creates_reuse_release(client):
    """SBX-001/002：开 run 建容器 + 同用户第二个 run 复用；两 run 全关后置 idle。"""
    async def scenario():
        c1 = await sandbox_executor.ensure_user_container("uA", "run-1", _CFG)
        assert c1.status == "active" and c1.active_run_count == 1
        c2 = await sandbox_executor.ensure_user_container("uA", "run-2", _CFG)
        assert c2 is c1 and c2.active_run_count == 2  # 一个用户一个容器（复用）
        await sandbox_executor.release_user_container("uA", "run-1")
        assert c1.status == "active" and c1.active_run_count == 1  # 还有活跃 run，常驻
        await sandbox_executor.release_user_container("uA", "run-2")
        assert c1.status == "idle" and c1.active_run_count == 0  # 末个 run 关闭 → idle
        assert sandbox_executor.get("uA") is not None  # idle 期仍占名额（TTL 内可复用）
        await sandbox_executor.close_all()

    asyncio.run(scenario())


def test_sbx_002_capacity_full_rejects_new_session(client):
    """CANCEL-007：容器数达上限时，新用户开 run 被拒 SANDBOX_CAPACITY_FULL；未到 TTL 的 idle 不被抢占。"""
    from domain.errors import ApiError, Err

    async def scenario():
        await sandbox_executor.ensure_user_container("u1", "r1", _CFG)
        await sandbox_executor.ensure_user_container("u2", "r2", _CFG)  # 满（max=2）
        with pytest.raises(ApiError) as ei:
            await sandbox_executor.ensure_user_container("u3", "r3", _CFG)
        assert ei.value.code == Err.SANDBOX_CAPACITY_FULL and ei.value.status == 429
        # u1 关闭 → idle 但未到 TTL：strict_ttl 下不提前回收，u3 仍被拒
        await sandbox_executor.release_user_container("u1", "r1")
        with pytest.raises(ApiError):
            await sandbox_executor.ensure_user_container("u3", "r3", _CFG)
        # idle TTL=0 → 立即可回收腾位，u3 成功
        c = await sandbox_executor.ensure_user_container("u3", "r3", {**_CFG, "user_container_idle_ttl_minutes": 0})
        assert c.status == "active"
        await sandbox_executor.close_all()

    asyncio.run(scenario())


def test_sbx_003_run_lifecycle_via_api_audits_container(client):
    """端到端：开 run 写 sandbox.container.ready 审计 + 容器就位；关 run 后容器置 idle。"""
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    assert any(e["event_type"] == "sandbox.container.ready" for e in events)
    # 用户 0026demo01 的容器已就位、active
    c = sandbox_executor.get("0026demo01")
    assert c is not None and c.status == "active" and c.active_run_count == 1

    unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}:close", headers=USER_HEADERS))
    c2 = sandbox_executor.get("0026demo01")
    assert c2 is not None and c2.status == "idle"  # 末 run 关闭 → idle（TTL>0 未回收）
