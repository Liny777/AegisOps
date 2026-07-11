# 外部依赖接真开关清单（C3；2026-07-11 内网联调后更新）

所有外部依赖默认走 **mock**（pytest/demo 不依赖真环境）；生产/联调按下表设 env 开关切 `real`。
real 变体已**按权威契约（29.7/29.3/28.2）对齐 HTTP 形状 + 信封解包 + 字段映射**，并有 stub-httpx 单测护栏
（`tests/test_external_real.py`）；联调只需配端点 + 对端真环境/Key。`*_BASE_URL` 均为 **host root**（client 拼各自前缀）。
**内网实测经验（2026-07-11，MCP/GLM 已通）**：联调新环境先跑 `check-net.py`（run-backend 末行临时换
`exec "$PY" check-net.py`），再看启动横幅逐字段核对开关——踩坑记录见 Obsidian《35-内网联调进展记录》。

## 开关总表

| 依赖 | 开关 env | 端点/凭证 env | 默认 | 状态 |
|---|---|---|---|---|
| 平台 LLM（GLM 驱动 RCA） | —（有 Key 即真） | `OPENOPS_PLATFORM_GLM_API_KEY`（base_url 在 DB `sre_model_asset`） | 无 Key→stub | ✅ **内网已通**（2026-07-11） |
| oModel(umodel)（scope resolve + workspace CRUD） | `OPENOPS_OMODEL=real` | `OPENOPS_OMODEL_BASE_URL`（可选 `OPENOPS_OMODEL_COOKIE`，IAM 开启时） | mock | **已按 29.7 对齐**（resolve 用「列 workspace 关联项目」，见下安全口径） |
| 平台/动态 MCP（tools/call） | `OPENOPS_MCP=real` | `OPENOPS_MCP_ROUTE=direct(默认)|proxy`；direct 直连 server_url、proxy 走 console（另需 legacy `OPENOPS_MCP_BASE_URL` 仅 demo 工具） | mock | ✅ **内网已通**（direct streamable-HTTP；console proxy 上游 404 待对端修） |
| MCP Registry（list servers + discover tools） | `OPENOPS_MCPREGISTRY=real` | `OPENOPS_MCPREGISTRY_BASE_URL` + `OPENOPS_MCPREGISTRY_COOKIE`（console 鉴权，会话态会过期） | mock | ✅ **内网已通**（list/query 走 console；tools/list 按 route 走 direct/proxy） |
| Skill Hub（list + 下载 ZIP） | `OPENOPS_SKILLHUB=real` | `OPENOPS_SKILLHUB_BASE_URL` | mock | **已按 29.3 对齐**（`/obsv/agent/management/skills/*`） |
| 用户自定义 LLM 探测 | `OPENOPS_LLM_PROBE=real` | 用户配置的 base_url/Key（建配置时提交） | mock | **代码就绪**，real 发真 `chat/completions` 验能力 |
| Secret 加密 key | —（生产必配） | `OPENOPS_ENCRYPTION_KEY`（+`_OLD` 轮换） | 派生 dev key | **代码就绪**（Fernet），生产须配 |
| 真 W3/IAM introspect | 未做（B9） | — | `X-OpenOps-Mock-User` 头 | **未做**（B9 整块） |

其它旋钮：`OPENOPS_RUNTIME=mock|agentscope`、`OPENOPS_SANDBOX=fake|docker`、`OPENOPS_LLM_PROBE=mock|real`、
`OPENOPS_LLM_EGRESS_BLOCK_PRIVATE=1`、`OPENOPS_LLM_EGRESS_DENY`、联调缝 `OPENOPS_SCOPE_OVERRIDE_APPIDS`（跳过 oModel 用指定 appid 当 scope，切真 oModel 后应删）。
**出站硬化（内网教训，全部 real client 生效）**：`OPENOPS_HTTP_TRUST_ENV=0(默认)|1`（默认不信任环境/Windows 注册表代理——公司 SWG 会劫内网出站）；
TLS 三档 `OPENOPS_TLS_CA_FILE` > `OPENOPS_TLS_INSECURE=1` > certifi（正解：`pip install truststore`，run.py 自动用系统证书库）。

## 逐项说明

### 平台 LLM（真 GLM）—— 代码就绪
seed 已含平台模型资产 `glm-5.1`（`base_url=https://open.bigmodel.cn/api/paas/v4/...`、`secret_env_var=OPENOPS_PLATFORM_GLM_API_KEY`、scope=all、active）。设 `OPENOPS_PLATFORM_GLM_API_KEY=<真 Key>` 后：`model_gateway.resolve_runtime_model` 解析出 GLM spec → `agentscope_runtime._build_model` 用该 Key 建 `OpenAIChatModel` → 真 GLM 驱动巡检/定界/RCA/结论。Key 只在构建边界从 env 取、即用即弃，绝不落 PG/日志/事件/审计（SEC-001）。**需 `OPENOPS_RUNTIME=agentscope`**（mock 编排器不接模型）。

用户自定义 LLM 同理：Key 存 PG（Fernet 加密），`_build_model` 在构建边界瞬时解密（`_decrypt_user_secret`）；base_url 过 egress SSRF 校验。

**用户 LLM 探测**（`OPENOPS_LLM_PROBE=mock|real`）：建 llm-config 时 `llm_provider_client.probe` 校验能力。mock（默认）按 model_name 启发式（含 `no-tool` 判失败），测试/demo 不打网；real 向 `{base_url}/chat/completions` 发一次最小请求（带 dummy `tools`+`tool_choice`）验**可达+接受 tools 参数**=支持 tool calling，连接错/超时/4xx → `ok=False`（reason 脱敏，不含 Key/url），探测失败不落 active。probe 前已过 `egress.check_llm_egress`（SSRF 安全）。实例默认模型经 `initial_overlay_json.user_llm_config_id`（InitWizard custom 分支）在起任务时装配（`selected_model` 种子）。

### oModel(umodel)（真 scope resolve + workspace CRUD）—— 已按 29.7 最终文档对齐
`OPENOPS_OMODEL=real` + `OPENOPS_OMODEL_BASE_URL`（host root）。`omodel_real`（`/api/v1/workspaces...`）：
- **`resolve_scope`** → 29.7「列出 workspace 关联项目」`GET /{ws}/projects`，`effective_appids` = 返回的 `project_id` 列表；
  任何错误/超时/404/空 → fail-closed（`status=failed`/空集，Scope Service 兜 SCOPE_RESOLVE_FAILED/EMPTY_SCOPE，缓存不作失败兜底）。
- **`get/list/create`** → `WorkspaceMetadata`（无信封，29.7 snake_case `updated_at`）→ OpenOps 词汇映射；`list` 解 `Page.items`
  （29.7 仅支持 `include_deleted` 参数）；`create` 的 `app_ids` → `config.workspace_ui.scopes[].projectId`（object[] 新格式，
  写入 umodel 项目映射表=resolve 的数据源，链路自洽）；scopes 读取兼容 string[] 旧格式。
- 出站硬化同 console 口径（TLS 三档 + trust_env 默认 off）；可选 `OPENOPS_OMODEL_COOKIE`（umodel 部署
  `omodel.iam.validation.enable=true` 时带 session cookie；29.7 显示 workspace 端点匿名可用「未登录=system」，默认不带）。
- `scope_revision` OpenOps 私有派生（范围内容 hash），不映射 umodel 同名列/`resourceVersion`（29.6 §三）；`sync_status` 降级 active→ready（umodel 无动态就绪态/`/status`，29.6 P2-1）。
- 联调自检：`check-net.py` ③（healthz + list workspaces + 首个 ws 的 /projects）。

> 🔴 **安全口径（联调必知）**：`GET /{ws}/projects` 是**静态全量关联项目、不按用户过滤**（29.6 §二 P0-1，「发现 ≠ 授权」）。
> V1 用它接真 = `effective_appids` 是 **workspace 级发现集，非 per-user 授权集**；per-user 过滤待 umodel 真 `:resolve`
> （29.7 有 `GET /api/v1/wesee/user-projects` 按登录用户搜项目，但需 IAM session，且语义是「用户的项目」非「workspace∩用户」，暂不采用）。

### 平台 HTTP MCP / MCP Registry / Skill Hub —— 已按 28.2 / 29.3 对齐
real 变体经各自 `*_BASE_URL`（host root）发 HTTP（未配 → raise，不静默降级）：
- **Skill Hub**：`list_skills` → `POST /obsv/agent/management/skills/list/query`，解 `{code,message,data:{items}}` 信封 + 字段映射（`skill_id→skill_key`、`is_system→source_type`、`latest_version→version_no` 等，29.4）；`download` → `GET /obsv/agent/management/skills/download?skill_id=`，按 `X-Checksum-SHA256`（ZIP 原始字节 sha256，C1-CHK-001）校验。**V1 下载 latest（省略 version）**——OpenOps 无 semver，精确 pin 待 repo 穿透。
- **MCP Registry（✅内网已通）**：`list_servers` → `POST /obsv/agent/management/mcps/list/query`（source=openops 翻页，
  **需 `OPENOPS_MCPREGISTRY_COOKIE`**，会话态会过期）；`discover_tools(server_url)` 按 `OPENOPS_MCP_ROUTE` 走
  direct（默认，标准 MCP streamable-HTTP 直连 server_url：JSON-RPC `tools/list` + SSE 解析，无需 cookie）或
  proxy（console `mcps/proxy`，**其上游转发 404 待对端修**）；占位 endpoint `http://mock`（seed demo 资产）不外发。
- **动态 MCP 工具装配（✅内网已通）**：`OPENOPS_MCPREGISTRY=real` 时 run 起点从注册表发现全部 server 工具 →
  动态注册为 agent 工具（`readOnlyHint→免审批`、写→ASK；inputSchema 有 `project_id/appid`→scope required；
  scope 恰一个 appid 时自动补参）→ 每次调用穿 Tool Gateway（scope/审批/审计/28.2 头）。
- **平台 MCP tools/call（✅内网已通）**：direct 路由按 JSON-RPC `tools/call` 直连 server_url（fastmcp
  `structuredContent.result`/`content[0].text` 抽取，上限 `OPENOPS_MCP_RESULT_CAP` 默认 24k；mcpgateway `Trace-Id`
  作外部请求号）；28.2 平台 header 照带（含 `X-OpenOps-Audit-Trace-Id`、ASK 后 `X-OpenOps-Approval-Request-Id`），
  console cookie **不**外泄给 mcpgateway；用户自定义 MCP 不透传（28.2 铁律）。legacy `OPENOPS_MCP_BASE_URL`
  单网关路径仅剩 demo 工具（query_resource/recover_execute）在用。

### 联调待办（对端未就绪 / 未 pin，代码已按现状对齐）
- 🔴 **per-user `effective_appids` 过滤**：待 umodel 真 `:resolve`（29.6 P0-1）；当前 workspace 级发现集。
- 🟠 **console `mcps/proxy` 上游转发 404**：对端待修（复现 curl 见 35 号）；修好后 `OPENOPS_MCP_ROUTE=proxy` 可切回（生产 IAM 收口路径）。
- 🟠 **console cookie 会话态**：`OPENOPS_MCPREGISTRY_COOKIE` 过期需手动换；长期方案待 IAM 服务态凭证（B9）。
- 🟠 **Skill 版本 semver 精确 pin**：需 repo/DDL 穿透 semver；当前下载 latest。

> 联调实际顺序（2026-07-11 实践）：真 DB → 真 GLM（模型）→ 真 MCP（注册表+告警工具全链路）→ oModel（进行中）→ Skill Hub → Sandbox。每项切 real 后先跑 `check-net.py` 校准连通与响应形状，再全量联调；过程与坑见 Obsidian《35-内网联调进展记录》。
