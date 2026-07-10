# 外部依赖接真开关清单（C3）

所有外部依赖默认走 **mock**（pytest/demo 不依赖真环境）；生产/联调按下表设 env 开关切 `real`。
每项标注**已验证**（代码就绪 + 本地可切换/已跑通）与**待联调**（需对端真环境/Key，形状按 29.x，联调时校准）。

## 开关总表

| 依赖 | 开关 env | 端点/凭证 env | 默认 | 状态 |
|---|---|---|---|---|
| 平台 LLM（GLM 驱动 RCA） | —（有 Key 即真） | `OPENOPS_PLATFORM_GLM_API_KEY` | 无 Key→stub | **代码就绪**，设 Key 即活 |
| oModel（scope resolve） | `OPENOPS_OMODEL=real` | `OPENOPS_OMODEL_BASE_URL` | mock | 代码就绪，形状按 29.1/29.5 待联调校准 |
| 平台 HTTP MCP（tools/call） | `OPENOPS_MCP=real` | `OPENOPS_MCP_BASE_URL` | mock | 代码就绪，待联调（28.2 出站契约） |
| MCP Registry（discover tools） | `OPENOPS_MCPREGISTRY=real` | `OPENOPS_MCPREGISTRY_BASE_URL` | mock | 代码就绪，待联调（29.3 `/mcps/proxy`） |
| Skill Hub（list + 下载 ZIP） | `OPENOPS_SKILLHUB=real` | `OPENOPS_SKILLHUB_BASE_URL` | mock | 代码就绪，待联调（29.3 `X-Checksum-SHA256`） |
| 用户自定义 LLM 探测 | `OPENOPS_LLM_PROBE=real` | 用户配置的 base_url/Key（建配置时提交） | mock | **代码就绪**，real 发真 `chat/completions` 验能力 |
| Secret 加密 key | —（生产必配） | `OPENOPS_ENCRYPTION_KEY`（+`_OLD` 轮换） | 派生 dev key | **代码就绪**（Fernet），生产须配 |
| 真 W3/IAM introspect | 未做（B9） | — | `X-OpenOps-Mock-User` 头 | **未做**（B9 整块） |

其它旋钮：`OPENOPS_RUNTIME=mock|agentscope`、`OPENOPS_SANDBOX=fake|docker`、`OPENOPS_LLM_PROBE=mock|real`、`OPENOPS_LLM_EGRESS_BLOCK_PRIVATE=1`、`OPENOPS_LLM_EGRESS_DENY`。

## 逐项说明

### 平台 LLM（真 GLM）—— 代码就绪
seed 已含平台模型资产 `glm-5.1`（`base_url=https://open.bigmodel.cn/api/paas/v4/...`、`secret_env_var=OPENOPS_PLATFORM_GLM_API_KEY`、scope=all、active）。设 `OPENOPS_PLATFORM_GLM_API_KEY=<真 Key>` 后：`model_gateway.resolve_runtime_model` 解析出 GLM spec → `agentscope_runtime._build_model` 用该 Key 建 `OpenAIChatModel` → 真 GLM 驱动巡检/定界/RCA/结论。Key 只在构建边界从 env 取、即用即弃，绝不落 PG/日志/事件/审计（SEC-001）。**需 `OPENOPS_RUNTIME=agentscope`**（mock 编排器不接模型）。

用户自定义 LLM 同理：Key 存 PG（Fernet 加密），`_build_model` 在构建边界瞬时解密（`_decrypt_user_secret`）；base_url 过 egress SSRF 校验。

**用户 LLM 探测**（`OPENOPS_LLM_PROBE=mock|real`）：建 llm-config 时 `llm_provider_client.probe` 校验能力。mock（默认）按 model_name 启发式（含 `no-tool` 判失败），测试/demo 不打网；real 向 `{base_url}/chat/completions` 发一次最小请求（带 dummy `tools`+`tool_choice`）验**可达+接受 tools 参数**=支持 tool calling，连接错/超时/4xx → `ok=False`（reason 脱敏，不含 Key/url），探测失败不落 active。probe 前已过 `egress.check_llm_egress`（SSRF 安全）。实例默认模型经 `initial_overlay_json.user_llm_config_id`（InitWizard custom 分支）在起任务时装配（`selected_model` 种子）。

### oModel（真 scope resolve）—— 待联调
`OPENOPS_OMODEL=real` + `OPENOPS_OMODEL_BASE_URL`。`omodel_real` 发 `POST /scope/resolve`、`GET /workspaces/{id}` 等，任何错误/超时/未配 → `status=failed`，Scope Service fail-closed（缓存不作失败兜底）。umodel 侧 `resolve→effective_appids`/per-user 过滤/scope_revision 语义见 29.2/29.6 差距清单，联调时校准 HTTP 形状。

### 平台 HTTP MCP / MCP Registry / Skill Hub —— 待联调
real 变体经各自 `*_BASE_URL` 发 HTTP（未配 → raise，服务不静默降级）。契约见 29.3；schema_hash 由 OpenOps 侧自算（Registry 不做发现）；Skill ZIP 下载按 `X-Checksum-SHA256` 校验传输完整性。Tool Gateway 构建的平台 header（Cookie/X-EC2-IP/X-OpenOps-*/effective_appids）在 real MCP 调用时原样透传，用户自定义 MCP 不透传（28.2 铁律）。

> 联调顺序建议：先 oModel（scope 是主链路前置）→ Skill Hub（资产对账+Skill 执行）→ 平台 MCP（工具调用）→ 平台 GLM Key（真模型驱动）。每项切 real 后先用一次 smoke 校准响应形状再全量联调。
