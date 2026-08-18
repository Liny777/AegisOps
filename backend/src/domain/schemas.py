"""请求模型（Pydantic，21 号 API 详设口径）。响应 DTO 由 service 组装 dict。"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


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
    模板不可换不收；main_role_append 服务端保留（overlay 只动模型三键）。"""
    client_request_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    # 模型四态（互斥）：user_llm_config_id=自带 LLM（主=子）；model_template_id=模型模板（主/子槽位）；
    # platform_model_id=平台模型 legacy（主=子）；全 None=平台默认（从 overlay 移除）
    user_llm_config_id: str | None = None
    platform_model_id: str | None = None
    model_template_id: str | None = None


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


class AdminUpdateMcpRequest(BaseModel):
    """管理台编辑**平台级** MCP 配置（29.9 §3.4 mcps/config/update）。

    不含 server_name：display_name 是复合键 `{server}::{tool}` 的左半 + 上游 server_id 兼作主键，
    两头都不许改（改名=断掉全部模板绑定，见 domain/tool_key 头注释）。
    弹窗全量预填后提交 → PUT 语义（空串=清空）；status/offline 下线流程不在此（另有删除）。"""

    client_request_id: str = Field(min_length=1)
    server_url: str = Field(min_length=1)
    description: str = ""
    version: str = ""
    category: str = ""
    tags: list[str] | None = None
    transport: str = Field(default="streamable_http", pattern="^(jsonrpc|sse|streamable_http)$")


class AdminRegisterMcpRequest(BaseModel):
    """管理台注册**平台级** MCP（29.9 §3.1 mcps/register）。与用户面 RegisterMcpRequest 分开：
    字段词汇随上游（server_name 兼作 server_id / server_url / transport），且 `is_system` 恒为
    True、由服务端写死，不由客户端给。"""

    client_request_id: str = Field(min_length=1)
    server_name: str = Field(min_length=1)
    server_url: str = Field(min_length=1)
    description: str = ""
    version: str = "1.0.0"
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    transport: str = Field(default="streamable_http", pattern="^(jsonrpc|sse|streamable_http)$")


class CreateSecretRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    secret_name: str = Field(min_length=1)
    secret_type: str = "api_key"
    provider: str = "openai_compatible"
    secret_value: str = Field(min_length=1)  # 明文只进这一次，落库即密文+指纹


# 自定义出站 Header 校验（用户自带模型高级选项）：
# - Authorization 禁用：API Key 走 user_secret 加密链（SEC-001），不许经 extra_params_json 明文旁路；
# - Host/Content-Length/Transfer-Encoding/Connection 等由 HTTP 栈掌管，用户覆盖会破坏请求或绕过
#   egress 的 base_url 判定（SSRF 辅助头同理拦 X-Forwarded-*）；
# - 值禁 CR/LF（header 注入 fail early，而非等 httpx 抛错）。
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_|~-]{1,64}$")
_FORBIDDEN_HEADERS = frozenset({
    "authorization", "proxy-authorization", "cookie", "content-type", "content-length",
    "host", "transfer-encoding", "connection", "accept-encoding",
    "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip", "forwarded",
})
_MAX_EXTRA_HEADERS = 16


def validate_extra_headers(v: dict[str, str]) -> dict[str, str]:
    if len(v) > _MAX_EXTRA_HEADERS:
        raise ValueError(f"自定义 Header 最多 {_MAX_EXTRA_HEADERS} 条")
    out: dict[str, str] = {}
    for name, value in v.items():
        name = (name or "").strip()
        if not _HEADER_NAME_RE.fullmatch(name):
            raise ValueError(f"非法 Header 名：{name!r}（仅限字母数字与 !#$%&'*+.^_|~- ，≤64 字符）")
        if name.lower() in _FORBIDDEN_HEADERS:
            raise ValueError(f"Header {name} 不允许自定义（鉴权走 API Key 字段，连接类头由系统掌管）")
        value = str(value or "")
        if "\r" in value or "\n" in value or len(value) > 2048:
            raise ValueError(f"Header {name} 的值非法（禁换行，≤2048 字符）")
        out[name] = value
    return out


def validate_extra_headers_opt(v: dict[str, str] | None) -> dict[str, str] | None:
    """PATCH 用：None（未提供）直接放行，{} 与非空字典走同一道校验。"""
    return None if v is None else validate_extra_headers(v)


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
    # 高级选项：随每次 LLM 出站请求附带的自定义 Header（内网网关路由/租户标识等）。
    # 明文存 extra_params_json——UI 已提示勿放密钥；密钥类凭据仍走 secret_ref_id 加密链。
    extra_headers: dict[str, str] = Field(default_factory=dict)

    _v_headers = field_validator("extra_headers")(validate_extra_headers)


class UpdateLlmConfigRequest(BaseModel):
    """更新自带模型（PATCH 语义：只改显式提供的键，服务层用 exclude_unset 取差集）。

    改 base_url / model_name / extra_headers / secret_ref_id 会让上次探测结论失效，服务层
    强制重探测后才落库——与创建同门禁（探测不过不存），否则改出一个跑不通的配置却仍是 active。
    刻意不含 provider（当前只支持 openai_compatible）与 status（禁用/启用不在本迭代范围）。
    """
    client_request_id: str = Field(min_length=1)
    display_name: str | None = Field(default=None, min_length=1)
    base_url: str | None = Field(default=None, min_length=1)
    model_name: str | None = Field(default=None, min_length=1)
    secret_ref_id: str | None = None
    context_window_tokens: int | None = Field(default=None, gt=0)
    # 显式传 {} 表示清空全部自定义 Header；不传则原样保留（exclude_unset 区分二者）
    extra_headers: dict[str, str] | None = None

    _v_headers = field_validator("extra_headers")(validate_extra_headers_opt)


class TestConnectionRequest(BaseModel):
    """用户自带模型「测试连接」（存前探测，不落库）：raw API Key 只在这一次请求内瞬时用于探测。
    extra_headers 与保存后的真实调用同源携带——否则会出现「测通了但跑不通」。"""
    base_url: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    api_key: str = ""
    extra_headers: dict[str, str] = Field(default_factory=dict)

    _v_headers = field_validator("extra_headers")(validate_extra_headers)


class TestModelAssetRequest(BaseModel):
    """平台模型「测试连接」（存前探测，不落库）：raw API Key 只在这一次请求内瞬时用于探测。

    api_key 为空且带 model_asset_id 时，服务端改用该资产已存的密钥解密后探测——编辑态用户
    只改 base_url、不重填 Key 也要能测通。
    extra_headers 与保存后的真实调用同源携带——否则会出现「测通了但跑不通」。"""
    base_url: str = ""
    model_id: str = Field(min_length=1)
    api_key: str = ""
    model_asset_id: str = ""
    extra_headers: dict[str, str] = Field(default_factory=dict)

    _v_headers = field_validator("extra_headers")(validate_extra_headers)


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
    """保存模板草稿版本（B7·二）：全量 content_json（main/sub_agents；
    default_llm 已废弃仅兼容展示——模型改由「模型模板」sre_model_template 编排）。"""
    client_request_id: str = Field(min_length=1)
    content_json: dict[str, Any] = Field(default_factory=dict)


class TemplateVersionActionRequest(BaseModel):
    client_request_id: str = Field(min_length=1)


class RegisterModelAssetRequest(BaseModel):
    """注册模型接口（B7 模型资产）：DTO 白名单字段。
    access_scope 已移除（38.1 授权迁模板维度）；secret_env_var 已移除（2026-08-17 Key 迁密文列，
    旧客户端多传这些键会被 Pydantic 忽略）。"""
    client_request_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    protocol: str = "openai_compatible"
    base_url: str | None = None
    # 明文 Key 只进这一次，服务层立即 Fernet 加密落密文三列，回显只给 fingerprint（SEC-001）。
    # 空串 = 该模型不配 Key（走平台网关，或先建后配）。
    api_key: str = ""
    context_window_tokens: int = 128000
    # 随每次模型请求附带的自定义 Header（内网网关路由/租户标识等），与用户自带模型同口径。
    # 鉴权走 api_key 字段，禁配 Authorization 等保留头。
    extra_headers: dict[str, str] = Field(default_factory=dict)

    _v_headers = field_validator("extra_headers")(validate_extra_headers)


class ModelGrantsRequest(BaseModel):
    """38.1 起服务于模型模板 PUT /admin/model-templates/{id}/grants（原资产 /grants 已移除）。"""
    client_request_id: str = Field(min_length=1)
    access_scope: str = Field(pattern="^(all|restricted)$")
    user_ids: list[str] = Field(default_factory=list)


class ModelStatusRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    status: str = Field(pattern="^(active|disabled)$")


class UpdateModelAssetRequest(BaseModel):
    """更新模型资产连接配置（PATCH 语义：只改显式提供的键，服务层用 exclude_unset 取差集）。

    base_url 显式传 null 表示清空（走平台网关的模型本就不填 base_url），
    所以「没传」和「传 null」必须可区分——靠默认值区分不了，只能靠 exclude_unset。
    api_key 同理三态：不传=密钥原样不动（改 base_url 不必重录 Key），传空串/null=清除密钥
    （三列置 null），传值=加密覆盖。
    刻意不含 model_id：它是实例 overlay.platform_model_id 的绑定键，改了会让已绑定实例静默失配。
    status 亦不含——已有 :status 专用端点；access_scope 与 secret_env_var 已废弃。
    """
    client_request_id: str = Field(min_length=1)
    display_name: str | None = Field(default=None, min_length=1)
    base_url: str | None = None
    api_key: str | None = None
    context_window_tokens: int | None = Field(default=None, gt=0)
    # 显式传 {} 表示清空全部自定义 Header；不传则原样保留（exclude_unset 区分二者）
    extra_headers: dict[str, str] | None = None

    _v_headers = field_validator("extra_headers")(validate_extra_headers_opt)


class CreateModelTemplateRequest(BaseModel):
    """创建模型模板（主/子 Agent 两槽位，槽位引用 sre_model_asset 主键）；
    is_default=true 时服务层先落库（false）再原子切换默认（两步，防撞部分唯一索引）。
    access_scope=restricted 时创建后经 /grants 配白名单（未配前对用户全隐身，fail-closed）。"""
    client_request_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    description: str = ""
    main_model_asset_id: str = Field(min_length=1)
    sub_model_asset_id: str = Field(min_length=1)
    access_scope: str = Field(default="all", pattern="^(all|restricted)$")
    is_default: bool = False


class UpdateModelTemplateRequest(BaseModel):
    """更新模型模板（PATCH 语义：exclude_unset 取差集，对齐 UpdateModelAssetRequest）。
    is_default / status / access_scope 刻意不含——各有 :set-default、:status、/grants 专用端点。"""
    client_request_id: str = Field(min_length=1)
    display_name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    main_model_asset_id: str | None = Field(default=None, min_length=1)
    sub_model_asset_id: str | None = Field(default=None, min_length=1)


class ModelTemplateStatusRequest(BaseModel):
    client_request_id: str = Field(min_length=1)
    status: str = Field(pattern="^(active|disabled)$")


class ModelTemplateActionRequest(BaseModel):
    """:set-default 无字段动作体（对齐 TemplateVersionActionRequest）。"""
    client_request_id: str = Field(min_length=1)


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
    tags: list[str] | None = None  # 领域标签（如 ["财经","研发"]）；None=不动，数组=设置


class SetRoleRequest(BaseModel):
    """管理台改角色（B7·三补链）：升/降级已有用户。role 只收两枚举值。"""
    client_request_id: str = Field(min_length=1)
    role: Literal["user", "platform_admin"]


class SetTagsRequest(BaseModel):
    """管理台改用户领域标签：整体替换语义，[] 清空。"""
    client_request_id: str = Field(min_length=1)
    tags: list[str]
