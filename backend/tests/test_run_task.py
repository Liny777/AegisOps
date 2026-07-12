from __future__ import annotations

import time

from conftest import USER_HEADERS, create_instance, create_run, unwrap, wait_until


def start_task(client, run_id: str, text: str = "巡检 APP-A"):
    return unwrap(
        client.post(
            f"/api/openops/v1/agent-runs/{run_id}/tasks",
            headers=USER_HEADERS,
            json={"client_request_id": f"task_{time.time_ns()}", "input_text": text},
        )
    )


def test_run_001_003_task_start_resolves_scope_and_waits_for_ask(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    task = start_task(client, run["agent_run_id"])
    assert task["status"] == "running"

    approval = wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/approvals", headers=USER_HEADERS)),
    )
    assert approval[0]["tool_call_name"] == "recover_execute"

    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state", headers=USER_HEADERS))
    assert state["active_task"]["status"] == "running"
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    event_types = {e["event_type"] for e in events}
    assert "task.started" in event_types
    assert "scope.resolved" in event_types
    assert "openops.approval.required" in event_types


def test_run_002_cannot_create_run_on_other_owner_instance(client):
    instance = create_instance(client)
    forbidden = client.post(
        "/api/openops/v1/agent-runs",
        headers={"X-OpenOps-Mock-User": "admin", "X-OpenOps-Mock-Name": "Admin"},
        json={"client_request_id": "other_run", "agent_team_instance_id": instance["instance_id"]},
    )
    assert forbidden.status_code == 403


def test_run_005_closed_run_rejects_new_task(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    closed = unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}:close", headers=USER_HEADERS, json={}))
    assert closed["run_status"] == "closed"

    response = client.post(
        f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks",
        headers=USER_HEADERS,
        json={"client_request_id": "closed_task", "input_text": "还想继续"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RUN_ALREADY_CLOSED"


def test_cancel_001_task_cancel_keeps_run_active(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    task = start_task(client, run["agent_run_id"])

    cancelled = unwrap(client.post(f"/api/openops/v1/tasks/{task['task_id']}:cancel", headers=USER_HEADERS, json={}))
    assert cancelled["status"] == "cancelled"
    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state", headers=USER_HEADERS))
    assert state["run"]["run_status"] == "active"

    next_task = start_task(client, run["agent_run_id"], "继续新的巡检")
    assert next_task["status"] == "running"


def test_model_switch_is_run_level_only(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    selected = unwrap(
        client.post(
            f"/api/openops/v1/agent-runs/{run['agent_run_id']}:select-model",
            headers=USER_HEADERS,
            json={"client_request_id": "model_select", "model_source": "qwen3.5-instruct"},
        )
    )
    assert selected["selected_model"] == "qwen3.5-instruct"

    cfgs = unwrap(client.get(f"/api/openops/v1/agent-teams/{instance['instance_id']}/config-versions", headers=USER_HEADERS))
    assert len(cfgs) == 1


def test_resolve_available_skills_unifies_skill_key(monkeypatch):
    """键基统一：平台 skill 按 skill_key；用户绑定 skill 也按 skill_key（旧数据无 key 回退 display_name）。
    三面同源（执行门禁/LLM 工具描述/composer「/」列表）必须用同一个键。"""
    import asyncio

    from app import run_state_service as rss

    async def _skills(uid, include_platform=True):
        return [{"skill_key": "alarm-query", "display_name": "告警查询", "status": "active",
                 "version_no": 2, "checksum_sha256": "abc", "source_type": "platform"}]

    async def _bindings(cv):
        return [
            {"asset_type": "skill", "skill_status": "enabled", "skill_key": "my-tool",
             "skill_display_name": "我的工具", "skill_version_no": 1},
            {"asset_type": "skill", "skill_status": "enabled", "skill_key": None,
             "skill_display_name": "旧数据无key", "skill_version_no": 1},  # 回退 display_name
            {"asset_type": "mcp", "skill_status": None, "skill_key": None, "skill_display_name": None},
        ]

    from infra.repositories import assets as assets_repo

    monkeypatch.setattr(assets_repo, "list_skills", _skills)
    from infra.repositories import agent_teams as at_repo

    monkeypatch.setattr(at_repo, "list_binding_details", _bindings)

    out = asyncio.run(rss.resolve_available_skills("u1", "cv1"))
    assert set(out) == {"alarm-query", "my-tool", "旧数据无key"}
    assert out["alarm-query"]["display_name"] == "告警查询"
    assert out["my-tool"]["source_type"] == "user" and out["my-tool"]["display_name"] == "我的工具"


def test_available_skills_endpoint_shape_and_ownership(client):
    """GET /agent-teams/{id}/available-skills：与执行门禁同源；他人实例 403。"""
    from conftest import OTHER_HEADERS, USER_HEADERS, create_instance, unwrap

    inst = create_instance(client)
    rows = unwrap(client.get(f"/api/openops/v1/agent-teams/{inst['instance_id']}/available-skills",
                             headers=USER_HEADERS))
    assert isinstance(rows, list) and rows  # seed 平台 skill（inspection）至少 1 条
    assert {"skill_key", "display_name", "source_type"} <= set(rows[0])
    assert any(r["skill_key"] == "inspection" for r in rows)

    r = client.get(f"/api/openops/v1/agent-teams/{inst['instance_id']}/available-skills", headers=OTHER_HEADERS)
    assert r.status_code == 403


def test_run_title_autoname_and_rename(client):
    """会话名：首个任务输入自动起名（前 30 字）；:rename 覆盖并落审计；他人 403。"""
    from conftest import OTHER_HEADERS

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    rid = run["agent_run_id"]
    assert run.get("run_title") in (None, "")  # 新会话未起名

    long_text = "支付下单接口刚才延迟突然变高，帮我看下是什么问题，谢谢啦，多余的字要被裁掉"
    start_task(client, rid, long_text)
    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/state", headers=USER_HEADERS))
    title = state["run"]["run_title"]
    assert title and len(title) <= 31 and title.startswith(long_text[:10])
    assert title.endswith("…")  # 超 30 字截断标记

    # 列表面（侧栏历史会话数据源）带 run_title
    runs_list = unwrap(client.get("/api/openops/v1/agent-runs", headers=USER_HEADERS))
    assert any(r["agent_run_id"] == rid and r["run_title"] == title for r in runs_list)

    # 重命名：trim + 生效 + 审计
    renamed = unwrap(client.post(f"/api/openops/v1/agent-runs/{rid}:rename", headers=USER_HEADERS,
                                 json={"client_request_id": "rn1", "title": "  Redis 连接池  排查  "}))
    assert renamed["run_title"] == "Redis 连接池 排查"
    state2 = unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/state", headers=USER_HEADERS))
    assert state2["run"]["run_title"] == "Redis 连接池 排查"
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{rid}", headers=USER_HEADERS))
    assert "run.renamed" in {e["event_type"] for e in events}

    # 再起任务不覆盖既有名称（只有首个任务起名）
    start_task(client, rid, "第二个任务的输入不该改标题")
    state3 = unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/state", headers=USER_HEADERS))
    assert state3["run"]["run_title"] == "Redis 连接池 排查"

    # 他人无权改名；空白标题 400
    r = client.post(f"/api/openops/v1/agent-runs/{rid}:rename", headers=OTHER_HEADERS,
                    json={"client_request_id": "rn2", "title": "hack"})
    assert r.status_code == 403
    r2 = client.post(f"/api/openops/v1/agent-runs/{rid}:rename", headers=USER_HEADERS,
                     json={"client_request_id": "rn3", "title": "   "})
    assert r2.status_code in (400, 422)  # service trim 后空 → VALIDATION_FAILED（422=pydantic 拦）


def test_run_delete_soft_and_list_excludes(client):
    """会话删除（软删）：列表不再含该 run、他人 403、审计 run.deleted、删后 state 404。"""
    from conftest import OTHER_HEADERS

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    rid = run["agent_run_id"]

    # 他人无权删
    r = client.post(f"/api/openops/v1/agent-runs/{rid}:delete", headers=OTHER_HEADERS, json={})
    assert r.status_code == 403

    out = unwrap(client.post(f"/api/openops/v1/agent-runs/{rid}:delete", headers=USER_HEADERS, json={}))
    assert out["deleted"] is True

    runs_list = unwrap(client.get("/api/openops/v1/agent-runs", headers=USER_HEADERS))
    assert all(r2["agent_run_id"] != rid for r2 in runs_list)  # 列表不再含
    r2 = client.get(f"/api/openops/v1/agent-runs/{rid}/state", headers=USER_HEADERS)
    assert r2.status_code == 404  # get_run 过滤 deleted_at
