"""请求模型（Pydantic，21 号 API 详设口径）。响应 DTO 由 service 组装 dict。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class WorkspaceAppItem(BaseModel):
    app_id: str = Field(min_length=1)
    name: str | None = None  # 应用中文名 → umodel scopes[].projectCn
    tenant_id: str | None = None  # 应用所属租户 → umodel scopes[].tenantId（不同应用可属不同租户）


class CreateWorkspaceRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    app_ids: list[str] = Field(default_factory=list)
    apps: list[WorkspaceAppItem] | None = None  # 可选：带名称的应用清单（省略则 projectCn=app_id）
    # 管理员代查通道（问题复现）：手动输入、未经权限过滤的 APPID 子集；非空时 reason 必填并写审计
    manual_app_ids: list[str] = Field(default_factory=list)
    reason: str = ""  # 手输时的原因/工单号（进审计 payload，普通创建留空）


class UpdateWorkspaceRequest(BaseModel):
    """编辑系统范围（编辑向导）：改名 + 重选应用范围（对齐 umodel 接口 6）。
    name 必填（编辑含改名）；apps/app_ids 全量覆盖该 workspace 的 scopes。"""
    client_request_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    app_ids: list[str] = Field(default_factory=list)
    apps: list[WorkspaceAppItem] | None = None


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


class UpdateAgentTeamRequest(BaseModel):
    """编辑实例（编辑向导）：改名 / 换 workspace / 换模型。全量语义（向导预填后总有全部值）；
    模板不可换不收；main_role_append 服务端保留（overlay 只动模型两键）。"""
    client_request_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    # 模型三态（互斥）：user_llm_config_id=自带 LLM；platform_model_id=平台模型；二者皆 None=平台默认（从 overlay 移除）
    user_llm_config_id: str | None = None
    platform_model_id: str | None = None


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


class TestConnectionRequest(BaseModel):
    """用户自带模型「测试连接」（存前探测，不落库）：raw API Key 只在这一次请求内瞬时用于探测。"""
    base_url: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    api_key: str = ""


class TestModelAssetRequest(BaseModel):
    """平台模型「测试连接」：Key 由服务器读环境变量（secret_env_var 是变量名），客户端不传 Key。"""
    base_url: str = ""
    model_id: str = Field(min_length=1)
    secret_env_var: str = ""


class CreateRunRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    agent_team_instance_id: str = Field(min_length=1)


class StartTaskRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    input_text: str = Field(min_length=1)
    skill_hint: str | None = None


class RenameRunRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=120)  # service 侧 trim+截 60 字


class SelectModelRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    llm_config_id: str | None = None
    model_source: str = "platform"


class DecisionRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    decision: str = Field(pattern="^(approved|rejected)$")
    reason: str = ""


class SaveTemplateVersionRequest(BaseModel):
    """保存模板草稿版本（B7·二）：全量 content_json（main/sub_agents/default_llm）。"""
    client_request_id: str = Field(min_length=1)
    content_json: dict[str, Any] = Field(default_factory=dict)


class TemplateVersionActionRequest(BaseModel):
    client_request_id: str = Field(min_length=1)


class RegisterModelAssetRequest(BaseModel):
    """注册模型接口（B7 模型资产）：DTO 白名单字段，api_key/token 等敏感键天然进不来。"""
    client_request_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    protocol: str = "openai_compatible"
    base_url: str | None = None
    secret_env_var: str | None = None
    context_window_tokens: int = 128000
    access_scope: str = Field(default="all", pattern="^(all|restricted)$")


class ModelGrantsRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    access_scope: str = Field(pattern="^(all|restricted)$")
    user_ids: list[str] = Field(default_factory=list)


class ModelStatusRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    status: str = Field(pattern="^(active|disabled)$")


class UpdateModelAssetRequest(BaseModel):
    """更新模型资产连接配置（PATCH 语义：只改显式提供的键，服务层用 exclude_unset 取差集）。

    base_url / secret_env_var 显式传 null 表示清空（走平台网关的模型本就不填 base_url），
    所以「没传」和「传 null」必须可区分——靠默认值区分不了，只能靠 exclude_unset。
    刻意不含 model_id：它是实例 overlay.platform_model_id 的绑定键，改了会让已绑定实例静默失配。
    status / access_scope 亦不含——已有 :status 与 /grants 专用端点。
    """
    client_request_id: str = Field(min_length=1)
    display_name: str | None = Field(default=None, min_length=1)
    base_url: str | None = None
    secret_env_var: str | None = None
    context_window_tokens: int | None = Field(default=None, gt=0)


class UpdateRuntimeConfigRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    updates: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1)  # 必填：写审计 runtime_config.updated


class SandboxDestroyRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)  # 必填：销毁运行中容器需 reason（写审计+推用户可见事件）


class WhitelistRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    display_name: str = ""
    role: str = "user"


class SetRoleRequest(BaseModel):
    """管理台改角色（B7·三补链）：升/降级已有用户。role 只收两枚举值。"""
    client_request_id: str = Field(min_length=1)
    role: Literal["user", "platform_admin"]
