from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .mock_store import store


class CreateWorkspaceRequest(BaseModel):
    display_name: str = "新的工作范围"
    appids: list[str] = Field(default_factory=lambda: ["APP-A"])


class CreateAgentTeamRequest(BaseModel):
    template_id: str = "tpl_sre_fast_recovery"
    instance_name: str
    workspace_id: str = "ws_pay_abc"
    llm_config_id: str = "llm_platform_default"
    main_role_append: str = ""


class SaveConfigRequest(BaseModel):
    main_role_append: str | None = None
    llm_config_id: str | None = None
    change_reason: str = "user_update"
    client_request_id: str | None = None


class AssetBindingRequest(BaseModel):
    asset_type: str
    asset_id: str
    asset_version_id: str
    agent_key: str = "main"
    client_request_id: str | None = None


class CreateRunRequest(BaseModel):
    agent_team_instance_id: str
    client_request_id: str | None = None


class StartTaskRequest(BaseModel):
    prompt: str
    explicit_skill_id: str | None = None
    client_request_id: str | None = None


class SelectModelRequest(BaseModel):
    llm_config_id: str


class DecisionRequest(BaseModel):
    decision: str
    reason: str | None = None


class CreateSecretRequest(BaseModel):
    secret_name: str
    secret_kind: str = "llm_api_key"
    secret_value: str
    client_request_id: str | None = None


class CreateLlmConfigRequest(BaseModel):
    display_name: str
    base_url: str
    model_name: str
    secret_ref_id: str
    context_window_tokens: int = 64000
    max_output_tokens: int = 4096
    timeout_ms: int = 30000
    max_retries: int = 1
    supports_tool_calling: bool = True
    supports_streaming: bool = True
    supports_json_mode: bool = False
    extra_params_json: dict[str, Any] = Field(default_factory=dict)


def envelope(data: Any, audit_trace_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"data": data, "request_id": f"req_{uuid4().hex[:12]}"}
    if audit_trace_id:
        payload["audit_trace_id"] = audit_trace_id
    return payload


def api_error(status_code: int, code: str, message: str, retryable: bool = False) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "retryable": retryable, "details_redacted_json": {}},
    )


def resolve_user(
    x_openops_mock_role: str | None = Header(default=None),
    x_openops_mock_whitelist: str | None = Header(default=None),
    x_openops_mock_user: str | None = Header(default=None),
) -> dict[str, Any]:
    role = x_openops_mock_role or "user"
    login_key = x_openops_mock_user or ("admin" if role == "platform_admin" else "0026demo01")
    whitelisted = (x_openops_mock_whitelist or "true").lower() != "false"
    user = store.current_user(role=role, login_key=login_key, whitelisted=whitelisted)
    if not whitelisted:
        raise api_error(403, "NOT_WHITELISTED", "当前 W3 账号未进入 OpenOps V1 白名单。")
    return user


def require_admin(user: dict[str, Any]) -> None:
    if user["role"] != "platform_admin":
        raise api_error(403, "FORBIDDEN", "仅平台管理员可以访问该接口。")


app = FastAPI(title="OpenOps V1 Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail, "request_id": f"req_{uuid4().hex[:12]}"})


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/api/openops/v1/me")
def me(
    x_openops_mock_role: str | None = Header(default=None),
    x_openops_mock_whitelist: str | None = Header(default=None),
    x_openops_mock_user: str | None = Header(default=None),
) -> dict[str, Any]:
    user = resolve_user(x_openops_mock_role, x_openops_mock_whitelist, x_openops_mock_user)
    owned = [item for item in store.instances.values() if item["owner_user_id"] == user["user_id"]]
    return envelope(
        {
            **user,
            "has_instances": bool(owned),
            "recent_instance_id": owned[0]["agent_team_instance_id"] if owned else None,
        }
    )


@app.get("/api/openops/v1/me/profile")
def me_profile(user: dict[str, Any] = Header(default=None, alias="x-unused")) -> dict[str, Any]:
    return envelope(store.current_user())


@app.get("/api/openops/v1/templates/available")
def available_templates() -> dict[str, Any]:
    return envelope(list(store.templates.values()))


@app.post("/api/openops/v1/workspaces")
def create_workspace(payload: CreateWorkspaceRequest) -> dict[str, Any]:
    workspace_id = f"ws_{uuid4().hex[:10]}"
    workspace = {
        "workspace_id": workspace_id,
        "display_name": payload.display_name,
        "scope_revision": "rev-initial",
        "status": "ready",
        "effective_appids_preview": payload.appids,
    }
    store.workspaces[workspace_id] = workspace
    return envelope(workspace)


@app.get("/api/openops/v1/workspaces/{workspace_id}")
def get_workspace(workspace_id: str) -> dict[str, Any]:
    workspace = store.workspaces.get(workspace_id)
    if not workspace:
        raise api_error(404, "WORKSPACE_NOT_FOUND", "工作范围不存在。")
    return envelope(workspace)


@app.get("/api/openops/v1/workspaces/{workspace_id}/status")
def workspace_status(workspace_id: str) -> dict[str, Any]:
    workspace = store.workspaces.get(workspace_id)
    if not workspace:
        raise api_error(404, "WORKSPACE_NOT_FOUND", "工作范围不存在。")
    return envelope({"workspace_id": workspace_id, "status": workspace["status"], "scope_revision": workspace["scope_revision"]})


@app.get("/api/openops/v1/agent-teams")
def list_agent_teams() -> dict[str, Any]:
    return envelope(list(store.instances.values()))


@app.post("/api/openops/v1/agent-teams")
def create_agent_team(payload: CreateAgentTeamRequest) -> dict[str, Any]:
    user = store.current_user()
    return envelope(store.create_instance(user, payload.model_dump()))


@app.get("/api/openops/v1/agent-teams/{instance_id}")
def get_agent_team(instance_id: str) -> dict[str, Any]:
    instance = store.instances.get(instance_id)
    if not instance:
        raise api_error(404, "INSTANCE_NOT_FOUND", "AgentTeam 实例不存在。")
    config = store.config_versions[instance["active_config_version_id"]]
    return envelope({"instance": instance, "active_config_version": config})


@app.post("/api/openops/v1/agent-teams/{instance_id}/config-versions")
def save_config_version(instance_id: str, payload: SaveConfigRequest) -> dict[str, Any]:
    config = store.create_config_version(instance_id, payload.model_dump(exclude_none=True))
    if not config:
        raise api_error(404, "INSTANCE_NOT_FOUND", "AgentTeam 实例不存在。")
    return envelope(config)


@app.get("/api/openops/v1/agent-teams/{instance_id}/config-versions")
def list_config_versions(instance_id: str) -> dict[str, Any]:
    versions = [v for v in store.config_versions.values() if v["agent_team_instance_id"] == instance_id]
    return envelope(sorted(versions, key=lambda item: item["version_no"], reverse=True))


@app.post("/api/openops/v1/agent-teams/{instance_id}/asset-bindings")
def create_asset_binding(instance_id: str, payload: AssetBindingRequest) -> dict[str, Any]:
    instance = store.instances.get(instance_id)
    if not instance:
        raise api_error(404, "INSTANCE_NOT_FOUND", "AgentTeam 实例不存在。")
    binding_id = f"bind_{uuid4().hex[:12]}"
    binding = {
        "binding_id": binding_id,
        "agent_team_instance_id": instance_id,
        "config_version_id": instance["active_config_version_id"],
        "asset_type": payload.asset_type,
        "asset_id": payload.asset_id,
        "asset_version_id": payload.asset_version_id,
        "agent_key": payload.agent_key,
        "status": "active",
    }
    store.bindings[binding_id] = binding
    store.add_audit("asset.binding.created", binding)
    return envelope(binding)


@app.delete("/api/openops/v1/asset-bindings/{binding_id}")
def delete_asset_binding(binding_id: str) -> dict[str, Any]:
    binding = store.bindings.get(binding_id)
    if not binding:
        raise api_error(404, "BINDING_NOT_FOUND", "绑定不存在。")
    binding["status"] = "deleted"
    store.add_audit("asset.binding.deleted", {"binding_id": binding_id})
    return envelope(binding)


@app.get("/api/openops/v1/assets/skills")
def list_skills() -> dict[str, Any]:
    return envelope(list(store.skills.values()))


@app.post("/api/openops/v1/assets/skills")
def upload_skill(payload: dict[str, Any]) -> dict[str, Any]:
    skill_id = f"skill_{uuid4().hex[:10]}"
    skill = {
        "skill_id": skill_id,
        "skill_version_id": f"skillv_{uuid4().hex[:10]}",
        "source": "openops",
        "source_type": "user",
        "display_name": payload.get("display_name", "用户 Skill"),
        "skill_key": payload.get("skill_key", skill_id),
        "version_no": payload.get("version_no", "0.1.0"),
        "status": "enabled",
        "bound_agent_key": "main",
    }
    store.skills[skill_id] = skill
    return envelope(skill)


@app.get("/api/openops/v1/assets/mcps")
def list_mcps() -> dict[str, Any]:
    return envelope(list(store.mcps.values()))


@app.post("/api/openops/v1/assets/mcps")
def register_mcp(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("transport", "http") != "http":
        raise api_error(400, "MCP_TRANSPORT_UNSUPPORTED", "V1 仅支持 HTTP MCP。")
    mcp_id = f"mcp_{uuid4().hex[:10]}"
    mcp = {
        "mcp_id": mcp_id,
        "mcp_version_id": f"mcpv_{uuid4().hex[:10]}",
        "source": "openops",
        "source_type": "user",
        "display_name": payload.get("display_name", "用户 HTTP MCP"),
        "mcp_key": payload.get("mcp_key", mcp_id),
        "transport": "http",
        "endpoint_url": payload.get("endpoint_url", "https://example.internal/mcp"),
        "status": "enabled",
    }
    store.mcps[mcp_id] = mcp
    return envelope(mcp)


@app.post("/api/openops/v1/secrets")
def create_secret(payload: CreateSecretRequest) -> dict[str, Any]:
    secret_ref_id = f"secret_{uuid4().hex[:10]}"
    secret = {
        "secret_ref_id": secret_ref_id,
        "secret_name": payload.secret_name,
        "secret_kind": payload.secret_kind,
        "fingerprint": "****" + payload.secret_value[-4:],
        "status": "active",
    }
    store.secrets[secret_ref_id] = secret
    return envelope(secret)


@app.get("/api/openops/v1/llm-configs")
def list_llm_configs() -> dict[str, Any]:
    return envelope(list(store.llm_configs.values()))


@app.post("/api/openops/v1/llm-configs")
def create_llm_config(payload: CreateLlmConfigRequest) -> dict[str, Any]:
    if not payload.supports_tool_calling:
        raise api_error(400, "MODEL_PROBE_FAILED", "SRE Agent 运行模型必须支持 tool calling。")
    llm_config_id = f"llm_{uuid4().hex[:10]}"
    config = {
        "llm_config_id": llm_config_id,
        "display_name": payload.display_name,
        "provider": "user",
        "base_url": payload.base_url,
        "model_name": payload.model_name,
        "secret_ref_id": payload.secret_ref_id,
        "context_window_tokens": payload.context_window_tokens,
        "max_output_tokens": payload.max_output_tokens,
        "timeout_ms": payload.timeout_ms,
        "max_retries": payload.max_retries,
        "supports_tool_calling": payload.supports_tool_calling,
        "supports_streaming": payload.supports_streaming,
        "supports_json_mode": payload.supports_json_mode,
        "extra_params_json": payload.extra_params_json,
        "status": "active",
    }
    store.llm_configs[llm_config_id] = config
    return envelope(config)


@app.post("/api/openops/v1/agent-runs")
def create_run(payload: CreateRunRequest) -> dict[str, Any]:
    run = store.create_run(store.current_user(), payload.agent_team_instance_id)
    if not run:
        raise api_error(404, "INSTANCE_NOT_FOUND", "AgentTeam 实例不存在。")
    return envelope(run, run["audit_trace_id"])


@app.get("/api/openops/v1/agent-runs/{agent_run_id}")
def get_run(agent_run_id: str) -> dict[str, Any]:
    run = store.runs.get(agent_run_id)
    if not run:
        raise api_error(404, "RUN_NOT_FOUND", "Run 不存在。")
    return envelope(run, run["audit_trace_id"])


@app.get("/api/openops/v1/agent-runs/{agent_run_id}/state")
def get_run_state(agent_run_id: str) -> dict[str, Any]:
    state = store.run_state(agent_run_id)
    if not state:
        raise api_error(404, "RUN_NOT_FOUND", "Run 不存在。")
    return envelope(state, state["run"]["audit_trace_id"])


@app.post("/api/openops/v1/agent-runs/{agent_run_id}/tasks")
def start_task(agent_run_id: str, payload: StartTaskRequest) -> dict[str, Any]:
    task = store.start_task(agent_run_id, payload.model_dump())
    if not task:
        raise api_error(409, "RUN_ALREADY_CLOSED", "Run 不可启动新任务。")
    run = store.runs[agent_run_id]
    return envelope(task, run["audit_trace_id"])


@app.post("/api/openops/v1/agent-runs/{agent_run_id}/ag-ui")
def ag_ui(agent_run_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    state = store.run_state(agent_run_id)
    if not state:
        raise api_error(404, "RUN_NOT_FOUND", "Run 不存在。")
    events = [
        {"type": "openops.run.state", "data": state["runtime"]},
        *[{"type": item["type"], "data": item} for item in state["activity_timeline"][-8:]],
    ]
    return envelope({"events": events}, state["run"]["audit_trace_id"])


@app.post("/api/openops/v1/agent-runs/{agent_run_id}:select-model")
def select_model(agent_run_id: str, payload: SelectModelRequest) -> dict[str, Any]:
    run = store.runs.get(agent_run_id)
    if not run:
        raise api_error(404, "RUN_NOT_FOUND", "Run 不存在。")
    if payload.llm_config_id not in store.llm_configs:
        raise api_error(404, "MODEL_NOT_FOUND", "模型配置不存在。")
    run["selected_model"] = payload.llm_config_id
    store.add_audit("model.selected", {"llm_config_id": payload.llm_config_id}, agent_run_id=agent_run_id)
    return envelope({"agent_run_id": agent_run_id, "selected_model": payload.llm_config_id}, run["audit_trace_id"])


@app.post("/api/openops/v1/tasks/{task_id}:cancel")
def cancel_task(task_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    task = store.cancel_task(task_id, (payload or {}).get("reason"))
    if not task:
        raise api_error(404, "TASK_NOT_RUNNING", "未找到可取消的运行中任务。")
    return envelope(task)


@app.post("/api/openops/v1/agent-runs/{agent_run_id}:close")
def close_run(agent_run_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    run = store.close_run(agent_run_id, (payload or {}).get("reason"))
    if not run:
        raise api_error(404, "RUN_NOT_FOUND", "Run 不存在。")
    return envelope(run, run["audit_trace_id"])


@app.get("/api/openops/v1/agent-runs/{agent_run_id}/approvals")
def run_approvals(agent_run_id: str) -> dict[str, Any]:
    approvals = [a for a in store.approvals.values() if a["agent_run_id"] == agent_run_id and a["decision"] == "pending"]
    return envelope(approvals)


@app.post("/api/openops/v1/approvals/{approval_request_id}:decide")
def decide_approval(approval_request_id: str, payload: DecisionRequest) -> dict[str, Any]:
    approval = store.decide_approval(approval_request_id, payload.model_dump())
    if not approval:
        raise api_error(404, "APPROVAL_NOT_FOUND", "审批请求不存在。")
    return envelope(approval)


@app.get("/api/openops/v1/audit/runs/{agent_run_id}")
def audit_run(agent_run_id: str) -> dict[str, Any]:
    events = [event for event in store.audit_events if event.get("agent_run_id") == agent_run_id]
    return envelope(events)


@app.get("/api/openops/v1/admin/overview")
def admin_overview(
    x_openops_mock_role: str | None = Header(default=None),
    x_openops_mock_whitelist: str | None = Header(default=None),
    x_openops_mock_user: str | None = Header(default=None),
) -> dict[str, Any]:
    user = resolve_user(x_openops_mock_role, x_openops_mock_whitelist, x_openops_mock_user)
    require_admin(user)
    return envelope(
        {
            "templates": len(store.templates),
            "instances": len(store.instances),
            "mcp_tools": len(store.mcp_tools),
            "runtime_config": store.runtime_config,
        }
    )


@app.get("/api/openops/v1/admin/templates")
def admin_templates(x_openops_mock_role: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(resolve_user(x_openops_mock_role))
    return envelope(list(store.templates.values()))


@app.get("/api/openops/v1/admin/mcp-tools")
def admin_mcp_tools(x_openops_mock_role: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(resolve_user(x_openops_mock_role))
    return envelope(store.mcp_tools)


@app.get("/api/openops/v1/admin/sandbox")
def admin_sandbox(x_openops_mock_role: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(resolve_user(x_openops_mock_role))
    return envelope(
        {
            "host": "ec2-openops-demo",
            "active_user_containers": 8,
            "queued_users": 0,
            "config": store.runtime_config,
        }
    )


@app.get("/api/openops/v1/admin/audit")
def admin_audit(x_openops_mock_role: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(resolve_user(x_openops_mock_role))
    return envelope(store.audit_events[-50:])
