from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

import psycopg
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("OPENOPS_ORCH_DELAY_MS", "10")
os.environ.setdefault("OPENOPS_DATABASE_URL", "postgresql://openops:openops@localhost:5432/openops")
# 探测默认已是 real（生产 fail-closed）；测试须显式退回 mock，否则建 llm-config / 测试连接会真打网
os.environ.setdefault("OPENOPS_LLM_PROBE", "mock")
# ⚠ 刻意**不注入** OPENOPS_PLATFORM_GLM_API_KEY：注入了 seed 的一次性 backfill 就会把它加密进
# sre_model_asset，于是 runtime 认为平台模型「可跑」→ 真去构建 OpenAIChatModel 并打 seed 里那个
# 真实 bigmodel.cn 地址（实测整片 agentscope 用例 401 失败）。测试要的是 stub：无 Key = 回退 stub。
# 需要「已配 Key」语义的用例自己经 API 注册一个带 api_key 的资产（见 test_model_acl）。
# 假设 checkpoint 默认关（0=禁用）：防无关用例在 update_diagnosis_board 上被动等 10s；
# 要测它的用例用 monkeypatch.setattr(diagnosis_checkpoint, "CHECKPOINT_TIMEOUT_S", ...) 显式开
os.environ.setdefault("OPENOPS_DIAG_CHECKPOINT_TIMEOUT_S", "0")

import asyncio  # noqa: E402
import sys  # noqa: E402

if sys.platform == "win32":  # Windows pytest：psycopg3 async 需 SelectorEventLoop（同 backend/run.py）
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app import asset_reconcile_service, asset_registry_service, scope_service  # noqa: E402
from infra import idempotency  # noqa: E402
from infra.external import omodel_mock  # noqa: E402
from main import app  # noqa: E402
from runtime import events, task_registry  # noqa: E402
from sandbox.executor import executor as sandbox_executor  # noqa: E402


USER_HEADERS = {"X-OpenOps-Mock-User": "0026demo01", "X-OpenOps-Mock-Name": "LinYi"}
ADMIN_HEADERS = {"X-OpenOps-Mock-User": "admin", "X-OpenOps-Mock-Name": "Admin"}
OTHER_HEADERS = {"X-OpenOps-Mock-User": "0099other", "X-OpenOps-Mock-Name": "Other"}

ROOT = Path(__file__).resolve().parents[1]
# DDL 事实源 = core + 各垂直切片自带（切片新增 sql/slices/*.sql 时这里零改动）。
# 部署侧的对应关系见 docs/deploy-intranet.md 与 docker-compose.yml 的 initdb 挂载。
DDL_FILES = [ROOT / "sql" / "openops_v1_core.sql",
             *sorted((ROOT / "sql" / "slices").glob("*.sql"))]


def reset_database() -> None:
    """重放全部 DDL（幂等）+ 清空所有表。

    表清单**从库里读**而不是手排：本 schema 无外键（test_ddl_002 守着），TRUNCATE 无顺序
    要求；而手排列表漏一项 = 跨用例数据污染，是最难定位的一类失败（且新增切片表时
    还得记得回来改这里）。
    """
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        for f in DDL_FILES:
            conn.execute(f.read_text(encoding="utf-8"))
        tables = [r[0] for r in conn.execute(
            "select tablename from pg_tables where schemaname = current_schema()").fetchall()]
        if tables:
            conn.execute("TRUNCATE TABLE " + ", ".join(f'"{t}"' for t in tables))



@pytest.fixture()
def client() -> TestClient:
    reset_database()
    events.reset()
    task_registry.reset()
    idempotency.clear()
    scope_service._reset_cache()
    omodel_mock._reset()
    asset_reconcile_service._reset()
    asset_registry_service._reset_user_skill_sync()  # 清个人 skill 同步节流，避免跨用例泄漏
    sandbox_executor._by_user.clear()  # B8：每用例清沙箱容器注册表（fake 后端，进程内）
    with TestClient(app) as c:
        yield c
    task_registry.reset()
    events.reset()
    idempotency.clear()
    scope_service._reset_cache()
    omodel_mock._reset()
    asset_reconcile_service._reset()
    asset_registry_service._reset_user_skill_sync()


@pytest.fixture(autouse=True)
def _grant_alert_takeover(request):
    """tests/alerts/ 下自动给测试身份开通告警接管白名单（2026-08-09 算力保护 fail-closed）。

    存量 alerts 用例都以「功能已开通」为前提写就；白名单自身语义由
    tests/alerts/test_takeover_grant.py 显式关 grant 后专测。
    不放 tests/alerts/conftest.py：同名模块会遮蔽顶层 `from conftest import ...`。
    仅当用例自身请求了 client（有 DB）才补种——纯函数用例（inet_contract/matcher 等）零开销；
    getfixturevalue 拿到的是**已实例化**的 client，保证晚于 reset_database，grant 不被清。
    """
    if "tests/alerts" in str(getattr(request.node, "fspath", "")) and "client" in request.fixturenames:
        import asyncio as _aio

        from alerts import repository as _repo

        request.getfixturevalue("client")

        async def _apply() -> None:
            await _repo.set_user_grant("0026demo01", True, "conftest")
            await _repo.set_user_grant("0099other", True, "conftest")

        _aio.run(_apply())
    yield


def _runtime_params() -> list[str]:
    """fail-closed 类用例跑双 runtime（B6-RT-001）；无 agentscope 环境自动降级只跑 mock。"""
    try:
        import agentscope  # noqa: F401
        return ["mock", "agentscope"]
    except ModuleNotFoundError:
        return ["mock"]


@pytest.fixture(params=_runtime_params())
def runtime_backend(request, monkeypatch):
    monkeypatch.setenv("OPENOPS_RUNTIME", request.param)
    return request.param


def unwrap(response):
    assert response.status_code < 400, response.text
    return response.json()["data"]


def create_instance(client: TestClient, name: str = "测试 AgentTeam") -> dict[str, Any]:
    templates = unwrap(client.get("/api/openops/v1/templates/available", headers=USER_HEADERS))
    return unwrap(
        client.post(
            "/api/openops/v1/agent-teams",
            headers=USER_HEADERS,
            json={
                "client_request_id": f"crid_{time.time_ns()}",
                "template_version_id": templates[0]["template_version_id"],
                "name": name,
                "workspace_id": "ws_pay_abc",
            },
        )
    )["instance"]


def create_run(client: TestClient, instance_id: str) -> dict[str, Any]:
    return unwrap(
        client.post(
            "/api/openops/v1/agent-runs",
            headers=USER_HEADERS,
            json={"client_request_id": f"crid_{time.time_ns()}", "agent_team_instance_id": instance_id},
        )
    )["run"]


def wait_until(fn: Callable[[], Any], timeout: float = 2.0, interval: float = 0.03) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    return last
