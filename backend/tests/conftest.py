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


USER_HEADERS = {"X-OpenOps-Mock-User": "0026demo01", "X-OpenOps-Mock-Name": "LinYi"}
ADMIN_HEADERS = {"X-OpenOps-Mock-User": "admin", "X-OpenOps-Mock-Name": "Admin"}
OTHER_HEADERS = {"X-OpenOps-Mock-User": "0099other", "X-OpenOps-Mock-Name": "Other"}

ROOT = Path(__file__).resolve().parents[1]
DDL = ROOT / "sql" / "openops_v1_core.sql"
TABLES = [
    "model_access_grant",
    "model_asset",
    "audit_event",
    "approval_request",
    "scope_snapshot",
    "agent_run",
    "user_llm_config",
    "user_secret",
    "mcp_tool_annotation",
    "mcp_tool_catalog",
    "mcp_asset_version",
    "mcp_asset",
    "skill_asset_version",
    "skill_asset",
    "instance_asset_binding",
    "agent_team_config_version",
    "agent_team_instance",
    "agent_team_template_version",
    "agent_team_template",
    "platform_runtime_config",
    "user_whitelist",
    "openops_user",
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
    with TestClient(app) as c:
        yield c
    task_registry.reset()
    events.reset()
    idempotency.clear()
    scope_service._reset_cache()
    omodel_mock._reset()
    asset_reconcile_service._reset()


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
