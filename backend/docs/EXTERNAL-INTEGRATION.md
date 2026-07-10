# 外部依赖接真开关清单（C3）

所有外部依赖默认走 **mock**（pytest/demo 不依赖真环境）；生产/联调按下表设 env 开关切 `real`。
real 变体已**按权威契约（29.5/29.3/28.2）对齐 HTTP 形状 + 信封解包 + 字段映射**，并有 stub-httpx 单测护栏
（`tests/test_external_real.py`）；联调只需配端点 + 对端真环境/Key。`*_BASE_URL` 均为 **host root**（client 拼各自前缀）。

## 开关总表

| 依赖 | 开关 env | 端点/凭证 env | 默认 | 状态 |
|---|---|---|---|---|
| 平台 LLM（GLM 驱动 RCA） | —（有 Key 即真） | `OPENOPS_PLATFORM_GLM_API_KEY` | 无 Key→stub | **代码就绪**，设 Key 即活 |
| oModel（scope resolve + workspace CRUD） | `OPENOPS_OMODEL=real` | `OPENOPS_OMODEL_BASE_URL` | mock | **已按 29.5 对齐**（resolve 用端点4，见下安全口径） |
| 平台 HTTP MCP（tools/call） | `OPENOPS_MCP=real` | `OPENOPS_MCP_BASE_URL` | mock | **已按 28.2 对齐**（body+header；URL 路由待确认） |
| MCP Registry（discover tools） | `OPENOPS_MCPREGISTRY=real` | `OPENOPS_MCPREGISTRY_BASE_URL` | mock | **已按 29.3 对齐**（`/obsv/agent/management/mcps/proxy`） |
| Skill Hub（list + 下载 ZIP） | `OPENOPS_SKILLHUB=real` | `OPENOPS_SKILLHUB_BASE_URL` | mock | **已按 29.3 对齐**（`/obsv/agent/management/skills/*`） |
| 用户自定义 LLM 探测 | `OPENOPS_LLM_PROBE=real` | 用户配置的 base_url/Key（建配置时提交） | mock | **代码就绪**，real 发真 `chat/completions` 验能力 |
| Secret 加密 key | —（生产必配） | `OPENOPS_ENCRYPTION_KEY`（+`_OLD` 轮换） | 派生 dev key | **代码就绪**（Fernet），生产须配 |
| 真 W3/IAM introspect | 未做（B9） | — | `X-OpenOps-Mock-User` 头 | **未做**（B9 整块） |

其它旋钮：`OPENOPS_RUNTIME=mock|agentscope`、`OPENOPS_SANDBOX=fake|docker`、`OPENOPS_LLM_PROBE=mock|real`、`OPENOPS_LLM_EGRESS_BLOCK_PRIVATE=1`、`OPENOPS_LLM_EGRESS_DENY`。

## 逐项说明

### 平台 LLM（真 GLM）—— 代码就绪
seed 已含平台模型资产 `glm-5.1`（`base_url=https://open.bigmodel.cn/api/paas/v4/...`、`secret_env_var=OPENOPS_PLATFORM_GLM_API_KEY`、scope=all、active）。设 `OPENOPS_PLATFORM_GLM_API_KEY=<真 Key>` 后：`model_gateway.resolve_runtime_model` 解析出 GLM spec → `agentscope_runtime._build_model` 用该 Key 建 `OpenAIChatModel` → 真 GLM 驱动巡检/定界/RCA/结论。Key 只在构建边界从 env 取、即用即弃，绝不落 PG/日志/事件/审计（SEC-001）。**需 `OPENOPS_RUNTIME=agentscope`**（mock 编排器不接模型）。

用户自定义 LLM 同理：Key 存 PG（Fernet 加密），`_build_model` 在构建边界瞬时解密（`_decrypt_user_secret`）；base_url 过 egress SSRF 校验。

**用户 LLM 探测**（`OPENOPS_LLM_PROBE=mock|real`）：建 llm-config 时 `llm_provider_client.probe` 校验能力。mock（默认）按 model_name 启发式（含 `no-tool` 判失败），测试/demo 不打网；real 向 `{base_url}/chat/completions` 发一次最小请求（带 dummy `tools`+`tool_choice`）验**可达+接受 tools 参数**=支持 tool calling，连接错/超时/4xx → `ok=False`（reason 脱敏，不含 Key/url），探测失败不落 active。probe 前已过 `egress.check_llm_egress`（SSRF 安全）。实例默认模型经 `initial_overlay_json.user_llm_config_id`（InitWizard custom 分支）在起任务时装配（`selected_model` 种子）。

### oModel（真 scope resolve + workspace CRUD）—— 已按 29.5 对齐
`OPENOPS_OMODEL=real` + `OPENOPS_OMODEL_BASE_URL`（host root）。`omodel_real`（`/api/v1/workspaces...`）：
- **`resolve_scope`** → 29.5 端点 4 `GET /{ws}/projects`，`effective_appids` = 返回的 `project_id` 列表；
  任何错误/超时/404/空 → fail-closed（`status=failed`/空集，Scope Service 兜 SCOPE_RESOLVE_FAILED/EMPTY_SCOPE，缓存不作失败兜底）。
- **`get/list/create`** → 端点 5/2/1；`WorkspaceMetadata`（无信封）→ OpenOps 词汇映射；`list` 解 `Page.items`；`create` 的 `app_ids` → `config.workspace_ui.scopes[].projectId`。
- `scope_revision` OpenOps 私有派生（范围内容 hash），不映射 umodel 同名列/`resourceVersion`（29.6 §三）；`sync_status` 降级 active→ready（umodel 无动态就绪态/`/status`，29.6 P2-1）。

> 🔴 **安全口径（联调必知）**：29.6 §二 P0-1 明确端点 4 是**静态全量关联项目、不按用户过滤、不含 appid、不鉴权**（「发现 ≠ 授权」）。V1 用它接真 = `effective_appids` 是 **workspace 级发现集，非 per-user 授权集**；**per-user 过滤待 umodel 提供真 `:resolve`**（P0-1）。真 Cookie/IAM 鉴权亦待 umodel（P0-3），当前不发 Cookie。

### 平台 HTTP MCP / MCP Registry / Skill Hub —— 已按 28.2 / 29.3 对齐
real 变体经各自 `*_BASE_URL`（host root）发 HTTP（未配 → raise，不静默降级）：
- **Skill Hub**：`list_skills` → `POST /obsv/agent/management/skills/list/query`，解 `{code,message,data:{items}}` 信封 + 字段映射（`skill_id→skill_key`、`is_system→source_type`、`latest_version→version_no` 等，29.4）；`download` → `GET /obsv/agent/management/skills/download?skill_id=`，按 `X-Checksum-SHA256`（ZIP 原始字节 sha256，C1-CHK-001）校验。**V1 下载 latest（省略 version）**——OpenOps 无 semver，精确 pin 待 repo 穿透。
- **MCP Registry**：`discover_tools(server_url)` → `POST /obsv/agent/management/mcps/proxy`，body `{url,method:"tools/list"}`，解 `data.result.tools`；`server_url` = 平台 MCP 资产 `endpoint_config_json.endpoint`；schema_hash OpenOps 侧自算。
- **平台 MCP**：`call_tool` body `{tool_name, arguments}`（28.2）；Tool Gateway 注入 28.2 平台 header（含 `X-OpenOps-Audit-Trace-Id`、ASK 后 `X-OpenOps-Approval-Request-Id`），`Cookie`/`X-EC2-IP` 由真网关透传；用户自定义 MCP 不透传（28.2 铁律）。**⚠URL 路由 28.2 未 pin（网关直连 vs registry proxy），联调确认**。

### 联调待办（对端未就绪 / 未 pin，代码已按现状对齐）
- 🔴 **per-user `effective_appids` 过滤**：待 umodel 真 `:resolve`（29.6 P0-1）；当前 workspace 级发现集。
- 🔴 **真 Cookie/IAM 鉴权**：待 umodel（29.6 P0-3）；当前 real 不发 Cookie。
- 🟠 **平台 MCP URL 路由**：28.2 未 pin，联调确认网关直连 vs proxy。
- 🟠 **Skill 版本 semver 精确 pin**：需 repo/DDL 穿透 semver；当前下载 latest。

> 联调顺序建议：先 oModel（scope 是主链路前置）→ Skill Hub（资产对账+Skill 执行）→ 平台 MCP（工具调用）→ 平台 GLM Key（真模型驱动）。每项切 real 后先 smoke 校准响应形状再全量联调。
