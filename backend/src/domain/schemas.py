"""请求模型（Pydantic，21 号 API 详设口径）。响应 DTO 由 service 组装 dict。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateWorkspaceRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    app_ids: list[str] = Field(default_factory=list)


class CreateAgentTeamRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    template_version_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    scope_revision: str | None = None
    initial_overlay_json: dict[str, Any] = Field(default_factory=dict)


class SaveConfigRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    base_config_version_id: str | None = None
    overlay_json: dict[str, Any] = Field(default_factory=dict)
    change_reason: str = ""


class AssetBindingRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    asset_type: str = Field(pattern="^(skill|mcp)$")
    skill_id: str | None = None
    skill_version_id: str | None = None
    mcp_id: str | None = None
    mcp_version_id: str | None = None


class UploadSkillRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    manifest_json: dict[str, Any] = Field(default_factory=dict)
    checksum_sha256: str = ""


class RegisterMcpRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    transport: str = "http"
    endpoint: str = ""
    manifest_json: dict[str, Any] = Field(default_factory=dict)


class CreateSecretRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    secret_name: str = Field(min_length=1)
    secret_type: str = "api_key"
    provider: str = "openai_compatible"
    secret_value: str = Field(min_length=1)  # 明文只进这一次，落库即密文+指纹


class CreateLlmConfigRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    provider: str = "openai_compatible"
    base_url: str = ""
    model_name: str = Field(min_length=1)
    secret_ref_id: str | None = None
    context_window_tokens: int = 128000
    max_output_tokens: int = 8192
    timeout_ms: int = 60000
    max_retries: int = 1
    supports_tool_calling: bool = True


class CreateRunRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    agent_team_instance_id: str = Field(min_length=1)


class StartTaskRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    input_text: str = Field(min_length=1)
    skill_hint: str | None = None


class SelectModelRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    llm_config_id: str | None = None
    model_source: str = "platform"


class DecisionRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    decision: str = Field(pattern="^(approved|rejected)$")
    reason: str = ""


class UpdateRuntimeConfigRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    updates: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1)  # 必填：写审计 runtime_config.updated


class WhitelistRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    display_name: str = ""
    role: str = "user"
