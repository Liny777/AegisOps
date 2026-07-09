from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def unwrap(response):
    assert response.status_code < 400, response.text
    return response.json()["data"]


def test_me_and_templates():
    me = unwrap(client.get("/api/openops/v1/me"))
    assert me["login_key"] == "0026demo01"
    templates = unwrap(client.get("/api/openops/v1/templates/available"))
    assert templates[0]["template_key"] == "sre_fast_recovery"


def test_create_instance_run_task_and_approval():
    instance = unwrap(
        client.post(
            "/api/openops/v1/agent-teams",
            json={"template_id": "tpl_sre_fast_recovery", "instance_name": "测试 AgentTeam", "workspace_id": "ws_pay_abc"},
        )
    )
    run = unwrap(client.post("/api/openops/v1/agent-runs", json={"agent_team_instance_id": instance["agent_team_instance_id"]}))
    task = unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks", json={"prompt": "巡检 APP-A"}))
    assert task["status"] == "approval_pending"
    approvals = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/approvals"))
    assert approvals
    decided = unwrap(client.post(f"/api/openops/v1/approvals/{approvals[0]['approval_request_id']}:decide", json={"decision": "approved"}))
    assert decided["decision"] == "approved"
    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state"))
    assert state["run"]["active_task"]["status"] == "completed"


def test_model_switch_cancel_and_close():
    run = unwrap(client.post("/api/openops/v1/agent-runs", json={"agent_team_instance_id": "agt_pay_fast_recovery"}))
    selected = unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}:select-model", json={"llm_config_id": "llm_user_gpt4o"}))
    assert selected["selected_model"] == "llm_user_gpt4o"
    task = unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks", json={"prompt": "执行一次定界"}))
    cancelled = unwrap(client.post(f"/api/openops/v1/tasks/{task['task_id']}:cancel", json={"reason": "用户停止"}))
    assert cancelled["status"] == "cancelled"
    closed = unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}:close", json={"reason": "结束会话"}))
    assert closed["run_status"] == "closed"


def test_admin_requires_platform_admin():
    forbidden = client.get("/api/openops/v1/admin/overview")
    assert forbidden.status_code == 403
    overview = unwrap(client.get("/api/openops/v1/admin/overview", headers={"X-OpenOps-Mock-Role": "platform_admin"}))
    assert overview["runtime_config"]["max_user_containers_per_host"] == 26


def test_ddl_has_no_foreign_keys_or_triggers():
    ddl = open("sql/openops_v1_core.sql", encoding="utf-8").read().upper()
    assert "FOREIGN KEY" not in ddl
    assert "REFERENCES" not in ddl
    assert "CREATE TRIGGER" not in ddl
    assert "CREATE FUNCTION" not in ddl
