from __future__ import annotations

from conftest import USER_HEADERS, unwrap


def test_init_002_lists_template_and_ready_workspaces(client):
    templates = unwrap(client.get("/api/openops/v1/templates/available", headers=USER_HEADERS))
    assert templates[0]["template_key"] == "sensai_fast_recovery"

    workspaces = unwrap(client.get("/api/openops/v1/workspaces", headers=USER_HEADERS))
    assert any(w["workspace_id"] == "ws_pay_abc" and w["sync_status"] == "ready" for w in workspaces)

    models = unwrap(client.get("/api/openops/v1/models/platform", headers=USER_HEADERS))
    assert any(m["model_id"] == "glm-5.1" and m["status"] == "active" for m in models)


def test_init_003_blocks_workspace_not_ready(client):
    templates = unwrap(client.get("/api/openops/v1/templates/available", headers=USER_HEADERS))
    response = client.post(
        "/api/openops/v1/agent-teams",
        headers=USER_HEADERS,
        json={
            "client_request_id": "init_not_ready",
            "template_version_id": templates[0]["template_version_id"],
            "name": "同步中 Agent",
            "workspace_id": "ws_syncing",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "WORKSPACE_NOT_READY"


def test_init_004_ignores_non_v1_overlay_fields(client):
    templates = unwrap(client.get("/api/openops/v1/templates/available", headers=USER_HEADERS))
    created = unwrap(
        client.post(
            "/api/openops/v1/agent-teams",
            headers=USER_HEADERS,
            json={
                "client_request_id": "init_overlay",
                "template_version_id": templates[0]["template_version_id"],
                "name": "覆盖配置 Agent",
                "workspace_id": "ws_pay_abc",
                "initial_overlay_json": {
                    "main_role_append": "只追加 main agent 提示词",
                    "knowledge": {"enabled": True},
                    "sub_agents": [{"key": "bad"}],
                },
            },
        )
    )["instance"]
    detail = unwrap(client.get(f"/api/openops/v1/agent-teams/{created['instance_id']}", headers=USER_HEADERS))
    overlay = detail["active_config_version"]["overlay_json"]
    assert overlay == {"main_role_append": "只追加 main agent 提示词"}


def test_init_006_client_request_id_is_idempotent(client):
    templates = unwrap(client.get("/api/openops/v1/templates/available", headers=USER_HEADERS))
    body = {
        "client_request_id": "same_create_instance",
        "template_version_id": templates[0]["template_version_id"],
        "name": "幂等 Agent",
        "workspace_id": "ws_pay_abc",
    }
    one = unwrap(client.post("/api/openops/v1/agent-teams", headers=USER_HEADERS, json=body))
    two = unwrap(client.post("/api/openops/v1/agent-teams", headers=USER_HEADERS, json=body))
    assert one["instance"]["instance_id"] == two["instance"]["instance_id"]


# ---- 缺陷批回归：DEF-3 幂等先占位 + IDEMPOTENCY_KEY_CONFLICT ----

def test_def3_concurrent_same_key_creates_single_run(client):
    """DEF-3：并发同 key create_run 只建 1 个 run（旧「先业务后写键」两并发各建一个）。"""
    import asyncio as _asyncio

    from app import run_state_service as rss
    from conftest import create_instance
    from infra import idempotency

    inst = create_instance(client)
    idempotency.clear()

    class _Req:
        client_request_id = "def3_concurrent"
        agent_team_instance_id = inst["instance_id"]

        def model_dump(self, exclude=None):
            return {"agent_team_instance_id": self.agent_team_instance_id}

    async def _go():
        user = {"user_id": "0026demo01"}
        results = await _asyncio.gather(rss.create_run(user, _Req()), rss.create_run(user, _Req()),
                                        return_exceptions=True)
        return results

    results = _asyncio.run(_go())
    oks = [r for r in results if isinstance(r, dict)]
    errs = [r for r in results if not isinstance(r, dict)]
    # 赢家恰一个；输家要么命中缓存（同 run_id）要么 409 处理中
    assert len(oks) >= 1
    run_ids = {r["run"]["agent_run_id"] for r in oks}
    assert len(run_ids) == 1, f"并发建出多个 run: {run_ids}"
    for e in errs:
        assert getattr(e, "code", "") == "IDEMPOTENCY_KEY_CONFLICT"


def test_def3_same_key_different_body_conflict(client):
    """INIT-006 补实现：同 client_request_id 不同请求体 → 409 IDEMPOTENCY_KEY_CONFLICT。"""
    from conftest import USER_HEADERS, create_instance, unwrap

    inst = create_instance(client)
    body = {"client_request_id": "def3_body", "agent_team_instance_id": inst["instance_id"]}
    unwrap(client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS, json=body))
    # 同 key 重放同体 → 命中缓存 200
    r = client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS, json=body)
    assert r.status_code == 200
    # 同 key 异体 → 409（幂等判定在业务校验之前，假 id 也行）
    r = client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS,
                    json={"client_request_id": "def3_body",
                          "agent_team_instance_id": "00000000-0000-0000-0000-000000000000"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_def3_rollback_allows_retry(client):
    """DEF-3：业务失败释放占位——首次 403（他人实例）后同 key 重试不被「处理中」卡死。"""
    from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, unwrap

    # admin 的实例（create_instance 硬编码 USER_HEADERS，这里手写）
    ws = unwrap(client.post("/api/openops/v1/workspaces", headers=ADMIN_HEADERS,
                            json={"client_request_id": "def3_ws_a", "name": "admin域", "app_ids": ["APP-A"]}))
    tv = unwrap(client.get("/api/openops/v1/templates/available", headers=ADMIN_HEADERS))[0]["template_version_id"]
    inst_admin = unwrap(client.post("/api/openops/v1/agent-teams", headers=ADMIN_HEADERS,
                                    json={"client_request_id": "def3_agt_a", "template_version_id": tv,
                                          "name": "admin实例", "workspace_id": ws["workspace_id"]}))["instance"]
    body = {"client_request_id": "def3_retry", "agent_team_instance_id": inst_admin["instance_id"]}
    r = client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS, json=body)
    assert r.status_code == 403  # 业务失败 → rollback 释放占位
    inst_mine = create_instance(client)
    body["agent_team_instance_id"] = inst_mine["instance_id"]
    r = client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS, json=body)
    assert r.status_code == 200  # 同 key 重试成功（异体但占位已释放，不撞 hash）


def test_root_path_shim_strips_prefix_only_when_present():
    """文根自剥（RootPathShim）：带前缀剥掉+root_path 回写；裸路径原样放行（sidecar 直连）。"""
    import asyncio as _asyncio

    from main import RootPathShim

    seen: dict = {}

    async def dummy(scope, receive, send):
        seen.clear(); seen.update(scope)

    shim = RootPathShim(dummy, "/aegisback")
    _asyncio.run(shim({"type": "http", "path": "/aegisback/api/x"}, None, None))
    assert seen["path"] == "/api/x" and seen["root_path"] == "/aegisback"
    _asyncio.run(shim({"type": "http", "path": "/aegisback"}, None, None))
    assert seen["path"] == "/"
    _asyncio.run(shim({"type": "http", "path": "/api/x"}, None, None))
    assert seen["path"] == "/api/x" and "root_path" not in seen
    _asyncio.run(shim({"type": "http", "path": "/aegisbackup/x"}, None, None))  # 相似名不误剥
    assert seen["path"] == "/aegisbackup/x"
