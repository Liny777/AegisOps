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

from app import asset_reconcile_service, scope_service  # noqa: E402
from infra import idempotency  # noqa: E402
from infra.external import omodel_mock  # noqa: E402
from main import app  # noqa: E402
from runtime import events, task_registry  # noqa: E402
from sandbox.executor import executor as sandbox_executor  # noqa: E402


USER_HEADERS = {"X-OpenOps-Mock-User": "0026demo01", "X-OpenOps-Mock-Name": "LinYi"}
ADMIN_HEADERS = {"X-OpenOps-Mock-User": "admin", "X-OpenOps-Mock-Name": "Admin"}
OTHER_HEADERS = {"X-OpenOps-Mock-User": "0099other", "X-OpenOps-Mock-Name": "Other"}

ROOT = Path(__file__).resolve().parents[1]
DDL = ROOT / "sql" / "openops_v1_core.sql"
TABLES = [
    "sre_model_access_grant",
    "sre_model_asset",
    "sre_audit_event",
    "sre_approval_request",
    "sre_scope_snapshot",
    "sre_agent_run",
    "sre_user_llm_config",
    "sre_user_secret",
    "sre_mcp_tool_annotation",
    "sre_mcp_tool_catalog",
    "sre_mcp_asset_version",
    "sre_mcp_asset",
    "sre_skill_asset_version",
    "sre_skill_asset",
    "sre_instance_asset_binding",
    "sre_agent_team_config_version",
    "sre_agent_team_instance",
    "sre_agent_team_template_version",
    "sre_agent_team_template",
    "sre_platform_runtime_config",
    "sre_user_whitelist",
    "sre_openops_user",
]


def reset_database() -> None:
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(DDL.read_text(encoding="utf-8"))
        conn.execute("TRUNCATE TABLE " + ", ".join(TABLES))


@pytest.fixture()
def client() -> TestClient:
    reset_database()
    events.reset()
    task_registry.reset()
    idempotency.clear()
    scope_service._reset_cache()
    omodel_mock._reset()
    asset_reconcile_service._reset()
    sandbox_executor._by_user.clear()  # B8：每用例清沙箱容器注册表（fake 后端，进程内）
    with TestClient(app) as c:
        yield c
    task_registry.reset()
    events.reset()
    idempotency.clear()
    scope_service._reset_cache()
    omodel_mock._reset()
    asset_reconcile_service._reset()


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
