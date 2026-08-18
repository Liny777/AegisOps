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
| oModel(umodel)（scope resolve + workspace CRUD） | `OPENOPS_OMODEL=real` | `OPENOPS_OMODEL_BASE_URL` + `OPENOPS_OMODEL_TENANT_ID`；登录态由当前 IAM 用户透传（静态 Cookie 仅本地调试） | mock | ✅ **内网已通**（写操作需同源 Origin/Referer；安全口径见下） |
| 应用目录（初始化「从应用创建系统范围」选源） | `OPENOPS_APPTREE=real` | `OPENOPS_APPTREE_BASE_URL` + `OPENOPS_APPTREE_COOKIE`（可选，IAM 会话态）；`OPENOPS_APPTREE_ENTERPRISE_ID`/`OPENOPS_APPTREE_PROJECT_ID`（联调默认值）；联调缝 `OPENOPS_APPTREE_USER_ID`（mock 头非 W3 账号时覆盖） | mock | **已按 verification 契约对齐**（`userid_search_appid`；内网待联调） |
| 平台/动态 MCP（tools/call） | `OPENOPS_MCP=real` | `OPENOPS_MCP_ROUTE=direct(默认)|proxy`；direct 直连 server_url、proxy 走 console（另需 legacy `OPENOPS_MCP_BASE_URL` 仅 demo 工具） | mock | ✅ **内网已通**（direct streamable-HTTP；console proxy 上游 404 待对端修） |
| MCP Registry（list servers + discover tools） | `OPENOPS_MCPREGISTRY=real` | `OPENOPS_MCPREGISTRY_BASE_URL` + `OPENOPS_MCPREGISTRY_COOKIE`（console 鉴权，会话态会过期） | mock | ✅ **内网已通**（list/query 走 console；tools/list 按 route 走 direct/proxy） |
| Skill Hub（list + 上传/下载 ZIP + 删除） | `OPENOPS_SKILLHUB=real` | `OPENOPS_SKILLHUB_BASE_URL`（未配回退 `OPENOPS_MCPREGISTRY_BASE_URL`，同 console 网关）+ cookie（专属/共享） | mock | **已按 29.3 + 29.9（命名空间化/delete）对齐**，接真护栏齐（cookie/带体报错/HTML 检测/check-net ⑤） |
| 告警平台（7x24 接管上游，**Kafka 消费**） | `OPENOPS_ALERT=real` | `OPENOPS_ALERT_KAFKA_BOOTSTRAP/_TOPIC`（必）+ `_GROUP/_USERNAME/_PASSWORD/_SASL/_SECURITY_PROTOCOL`（SASL 集群时）——需可选 extra `pip install -e ".[kafka]"`；详情/回写 HTTP 另配 `OPENOPS_ALERT_BASE_URL`（禁 {host}）+ `OPENOPS_ALERT_TOKEN`；mock 档 `OPENOPS_ALERT_PULL_INTERVAL_S`（内存流轮询，默认 0=关，admin `:pull`/`:dispatch` 手动驱动） | mock | **代码就绪**（契约 v2 见 `docs/ALERT-PLATFORM-CONTRACT.md`：topic key=alert_id、retention≥7d 硬性；check-net ⑦=Kafka 连通+详情探测）；历史查询（规则预览主路径）`OPENOPS_ALERT_QUERY_URL`（29.10 完整 URL）+ 可选 `OPENOPS_ALERT_ENTERPRISE_ID`；消息体/查询行映射见 ALERT-PLATFORM-CONTRACT.md v3 |
| 用户自定义 LLM 探测 | `OPENOPS_LLM_PROBE=mock`（反向：关真探测） | 用户配置的 base_url/Key（建配置时提交） | **real** | **已启用**，发真 `chat/completions` 验能力；仅无出网环境退回 mock |
| Secret 加密 key | —（生产必配） | `OPENOPS_ENCRYPTION_KEY`（+`_OLD` 轮换） | 派生 dev key | **代码就绪**（Fernet），生产须配 |
| 真 W3/IAM introspect | 未做（B9） | — | `X-OpenOps-Mock-User` 头 | **未做**（B9 整块） |

其它旋钮：`OPENOPS_RUNTIME=mock|agentscope`、`OPENOPS_SANDBOX=fake|docker`、`OPENOPS_LLM_PROBE=real(默认)|mock`、
`OPENOPS_LLM_EGRESS_BLOCK_PRIVATE=1`、`OPENOPS_LLM_EGRESS_DENY`、联调缝 `OPENOPS_SCOPE_OVERRIDE_APPIDS`（跳过 oModel 用指定 appid 当 scope，切真 oModel 后应删）。
**出站硬化（内网教训，全部 real client 生效）**：`OPENOPS_HTTP_TRUST_ENV=0(默认)|1`（默认不信任环境/Windows 注册表代理——公司 SWG 会劫内网出站）；
TLS 三档 `OPENOPS_TLS_CA_FILE` > `OPENOPS_TLS_INSECURE=1` > certifi（正解：`pip install truststore`，run.py 自动用系统证书库）。
**cookie 三档优先级（2026-07-14 终版）**：`console_cookie()` = **用户登录态透传**（IAM 开启时
deps 每请求写入 contextvar + 按 user_id 缓存——真实环境**唯一正道**：umodel/console 校验的就是
这份，权限=操作者本人，无过期维护）＞ 专属 env（`OPENOPS_{OMODEL,MCPREGISTRY,APPTREE}_COOKIE`）
＞ 共享 `OPENOPS_CONSOLE_COOKIE`。**env 两档仅本地调试缝**（无 IAM 登录态时手工贴浏览器会话联调），
生产环境一律不配（上线核对清单有此条）。
—— 以下共享 env 说明仅适用于本地调试 ——
**共享登录态 `OPENOPS_CONSOLE_COOKIE`**：mcpregistry / omodel / apptree 三面实为同一份 IAM 会话 cookie——设这一个即可
（专属 `OPENOPS_{MCPREGISTRY,OMODEL,APPTREE}_COOKIE` 优先，未设回退共享），过期只换一处。启动横幅每面显示
`SET(len)`（专属）/`shared(len)`（回退共享）/`unset`。

**BASE_URL 的 `{host}` 占位符（2026-07-14）**：MCP Registry、Skill Hub、AppTree（及 AppTree
`_URL`）仍可按请求域名展开。**oModel 例外**：其出站会携带用户 IAM Cookie，
`OPENOPS_OMODEL_BASE_URL` 必须配置固定绝对地址，`{host}` 会 fail-closed，避免客户端可控 Host 将会话带往
非预期域。IAM 登录/登出回跳 URL 的 `{host}` 只用于浏览器导航，不作为 oModel 出站目标。

**端点装配规则（文根全 env 可覆盖——测试/生产文根不同、对端改文根只改 env 不改码）**：
`实际 URL = BASE_URL(host 根) + API_PREFIX(env 可覆盖的文根) + 操作尾段(代码，随契约走)`：
- console 系（mcps 列表/代理、skills 列表/下载同一网关文根）：`OPENOPS_CONSOLE_API_PREFIX`（默认 `/obsv/agent/management`）；
- oModel：`OPENOPS_OMODEL_API_PREFIX`（默认 `/api/v1/workspaces`）；
- apptree（单端点）：`OPENOPS_APPTREE_URL`=**整条端点 URL 原样使用**（最高优先，推荐），或 BASE_URL+内置模板组装。
操作尾段（如 `/mcps/list/query`、`/{ws}/projects`、`userid_search_appid`）不做 env——端点名变了通常请求/响应
契约也变了，必须改码适配；env 管"挂在哪"，代码管"契约是什么"。

## 逐项说明

### 平台 LLM（真 GLM）—— 代码就绪
seed 已含平台模型资产 `glm-5.1`（`base_url=https://open.bigmodel.cn/api/paas/v4/...`、`secret_env_var=OPENOPS_PLATFORM_GLM_API_KEY`、scope=all、active）。设 `OPENOPS_PLATFORM_GLM_API_KEY=<真 Key>` 后：`model_gateway.resolve_runtime_model` 解析出 GLM spec → `agentscope_runtime._build_model` 用该 Key 建 `OpenAIChatModel` → 真 GLM 驱动巡检/定界/RCA/结论。Key 只在构建边界从 env 取、即用即弃，绝不落 PG/日志/事件/审计（SEC-001）。**需 `OPENOPS_RUNTIME=agentscope`**（mock 编排器不接模型）。

用户自定义 LLM 同理：Key 存 PG（Fernet 加密），`_build_model` 在构建边界瞬时解密（`_decrypt_user_secret`）；base_url 过 egress SSRF 校验。

**用户 LLM 探测**（`OPENOPS_LLM_PROBE=real(默认)|mock`）：建 llm-config 与「测试连接」时 `llm_provider_client.probe` 校验能力。real（**默认**，且「非 mock 即 real」——拼错的值往安全方向倒）向 `{base_url}/chat/completions` 发一次最小请求（带 dummy `tools`+`tool_choice`）验**可达+接受 tools 参数**=支持 tool calling，连接错/超时/4xx → `ok=False`（reason 脱敏，不含 Key/url），探测失败不落 active；mock 按 model_name 启发式（含 `no-tool` 判失败）不打网，仅供离线测试/demo（`tests/conftest.py` 用 `setdefault` 钉住）与确无出网的部署。probe 前已过 `egress.check_llm_egress`（SSRF 安全）。

> 默认曾是 mock，而 mock 分支**无视 base_url/api_key**（只匹配模型名），导致「测试连接」对任何 Key 都返回绿勾、被当成真验过。现默认 fail-closed，且 probe 返回 `probe_mode`（"real"|"mock"）一路透传前端：mock 时 UI 显示「未真实探测」警告而非「连接正常」。**改默认值时务必同步 UI 提示，别再让假探测冒充真的。**

实例默认模型经 `initial_overlay_json.user_llm_config_id`（InitWizard custom 分支）在起任务时装配（`selected_model` 种子）。

### oModel(umodel)（真 scope resolve + workspace CRUD）—— 已按 29.7 最终文档对齐
`OPENOPS_OMODEL=real` + `OPENOPS_OMODEL_BASE_URL`（固定 host root，禁止 `{host}`）。`omodel_real`（`/api/v1/workspaces...`）：

- oModel 写请求携带当前 IAM 用户 Cookie、目标域派生的 `Origin/Referer`、浏览器 UA，以及从
  `X-Forwarded-For` 首跳取的 `IAM-Client-Ip`/`X-Forwarded-For`（用户真实浏览器 IP），兼容 IAM
  会话绑定客户端 IP 的网关。**前置**：入口网关须把用户真实 IP 放入 XFF 首段并全链透传。
- **`resolve_scope`** → 29.7「列出 workspace 关联项目」`GET /{ws}/projects`，`effective_appids` = 返回的 `project_id` 列表；
  任何错误/超时/404/空 → fail-closed（`status=failed`/空集，Scope Service 兜 SCOPE_RESOLVE_FAILED/EMPTY_SCOPE，缓存不作失败兜底）。
- **`get/list/create`** → `WorkspaceMetadata`（无信封，29.7 snake_case `updated_at`）→ OpenOps 词汇映射；`list` 解 `Page.items`
  （29.7 仅支持 `include_deleted` 参数）；`create` 请求体**镜像 umodel 新版 UI 实抓包**（2026-07-11 对端部署
  「id 服务端生成」后再抓）：**id 不传**（服务端生成 `{W3}-ws-{hex8}`）；labels/workspace_ui 的 tenantId=
    **当前企业租户 ID**（优先级：`OPENOPS_OMODEL_TENANT_ID` > AppTree 当前企业配置/生效端点 > 默认企业 ID；
    禁止从首个跨租 scope 推导）且**不带
  projectId**（服务端取首个 scope 自回填）；scopes=object[]{projectId, projectCn, tenantId}（per-项目租户，
  apptree 的 tenant_id 经前端一路带下来，缺失省略该键）；status:"running"+owner；scopes 读取兼容 string[] 旧格式。
- 设置页 OModel 菜单 iframe：`GET /api/openops/v1/omodel/console-page` 下发页面前缀
  （`OPENOPS_OMODEL_PAGE_URL` 覆盖 > 由 `OPENOPS_OMODEL_BASE_URL` 域根派生
  `/wesee/omodel/index.html?dataSource=api&workspace=`；未配置返回空串前端空态）。
  iframe 可用性依赖对端 X-Frame-Options/CSP frame-ancestors 与 cookie SameSite——被拒时用
  面板「新窗口打开」兜底，内网实测若拦需同事侧放行或同域反代（二期）。
- 出站硬化同 console 口径（TLS 三档 + trust_env 默认 off）；可选 `OPENOPS_OMODEL_COOKIE`（umodel 部署
  `omodel.iam.validation.enable=true` 时带 session cookie；真实环境使用当前用户 Cookie 透传）。写操作从最终
  固定 oModel base 派生同源 `Origin`/`Referer`，并带浏览器 UA 与 `Sec-Fetch-*`；不转发浏览器传入的
  Origin，避免 CSRF/脚本 UA 校验把 POST 误判为未登录。
- create 上游错误保持结构化：401/403→`OMODEL_AUTH_FAILED`(502，不触发登录跳转)，400/422→
  `VALIDATION_FAILED`(400)，超时→`OMODEL_UPSTREAM`(504)，网络/5xx→`OMODEL_UPSTREAM`(502)；上游
  任意 message 不回显给浏览器，只保留清洗后的 request ID 和状态码用于定位。
- `scope_revision` OpenOps 私有派生（范围内容 hash），不映射 umodel 同名列/`resourceVersion`（29.6 §三）；`sync_status` 降级 active→ready（umodel 无动态就绪态/`/status`，29.6 P2-1）。
- 联调自检：`check-net.py` ③（healthz + list workspaces + 首个 ws 的 /projects）。

> 🔴 **安全口径（联调必知）**：`GET /{ws}/projects` 是**静态全量关联项目、不按用户过滤**（29.6 §二 P0-1，「发现 ≠ 授权」）。
> V1 用它接真 = `effective_appids` 是 **workspace 级发现集，非 per-user 授权集**；per-user 过滤待 umodel 真 `:resolve`
> （29.7 有 `GET /api/v1/wesee/user-projects` 按登录用户搜项目，但需 IAM session，且语义是「用户的项目」非「workspace∩用户」，暂不采用）。

### 应用目录（初始化「从应用创建系统范围」选源）—— 已按 verification 契约对齐
`apptree_client.list_user_apps(user_id)`（`OPENOPS_APPTREE=real` 启用）：`POST {base}/observe/unifieduery/verification/api/v1/{enterprise}/{project}/userid_search_appid`，body `{"uesrId": <W3>}`（⚠path 段 `unifieduery`、body 键 `uesrId` 均为**对端真实拼写**，勿"修正"）；解 `data.datas[]` → 平铺 `{app_id=dimension_code, name=current_name_zh, type=dimension_type}`，**按 app_id 去重**（一人多角色→同 appid 多行）。**失败不静默**：非 2xx 带响应体报错、HTML=登录页/地址错、200+`status`非 OK 也报错（前端对话框直接显示原因）；每次调用打 `[OpenOps][apptree] POST … rows=N` 日志行；自检跑 `check-net.py` ④。当前企业按 `OPENOPS_APPTREE_ENTERPRISE_ID` > 生效完整 URL 的 enterprise 段 > 默认值解析，同时作为未显式配置 oModel tenant 时的 workspace 企业。出站硬化与 console/oModel 同口径（TLS 三档 + trust_env 默认 off + 可选 `OPENOPS_APPTREE_COOKIE`）。**联调缝** `OPENOPS_APPTREE_USER_ID`：mock 登录头里的 user_id 未必是 W3 账号（如 `0026demo01`≠`l00833445`），设它可覆盖发给上游的账号（对齐 `OPENOPS_SCOPE_OVERRIDE_APPIDS` 模式，接真 IAM 后删）。前端 `WorkspaceDialog` 平铺展示（名称/APPID/类型 + 搜索），勾选后 `POST /workspaces` 真落库为范围。此接口**天然按用户过滤**（比 oModel `/{ws}/projects` 的「发现集」更接近授权集），但初始化选源与运行时 scope resolve 仍是两条链（后者的 per-user 化待 umodel P0-1）。

### 平台 HTTP MCP / MCP Registry / Skill Hub —— 已按 28.2 / 29.3 对齐
real 变体经各自 `*_BASE_URL`（host root）发 HTTP（未配 → raise，不静默降级）。console 面
（mcps/skills）出站与 oModel 同款统一装配（`console_client_kwargs`，88a8fc1 同根因链）：用户登录态
Cookie 透传（env cookie 仅本地调试缝）+ 浏览器 UA + `IAM-Client-Ip`/`X-Forwarded-For`（华为 IAM
会话绑客户端 IP，缺头从服务器 IP 出站被判 code=1001 登录态失效）+ base 派生同源头；
`OPENOPS_HTTP_DEBUG=1` 打门控诊断（出站方法/URL/头/体 + 响应状态/体；Cookie 只打长度、客户端 IP
只打在场与否，SEC-001）：
- **Skill Hub**：`list_skills` → `POST /obsv/agent/management/skills/list/query`，解 `{code,message,data:{items}}` 信封 + 字段映射（`skill_id→skill_key`、`is_system→source_type`、`latest_version→version_no` 等，29.4）；`download` → `GET /obsv/agent/management/skills/download?skill_id=`，按 `X-Checksum-SHA256`（ZIP 原始字节 sha256，C1-CHK-001）校验。**V1 下载 latest（省略 version）**——OpenOps 无 semver，精确 pin 待 repo 穿透。
- **Skill Hub 29.9 命名空间化适配（2026-07-27）**：对端将把新上传 skill 的 `skill_id` 命名空间化
  （个人级 `user-{工号}-{name}`、系统级 `system-{name}`，存量裸名不变；`name` 恒为 SKILL.md 原始名）。
  本侧口径：**上传后本地 `skill_key` 取上传响应 `data.skill_id`**（缺失回退 SKILL.md 裸名 = 旧网关兼容），
  展示/斜杠命令用 `display_name`（原始名），执行入口（`/` hint 与 `run_platform_skill` 入参）经
  `domain/skill_alias.resolve_skill_alias` 做「原始名→key」唯一别名解析（精确 key 优先，多义 fail-closed 附候选）。
  SKILL.md name 以 `system-`/`user-` 开头本地预拒（对应上游 1001）。上游业务码 2003/2004（名称冲突）→
  409 `SKILL_NAME_CONFLICT` 透传上游 message；其余仍 502。`delete` → `POST /skills/delete`（29.9 §8.1，
  仅个人级、viewer cookie）：删除个人 skill 先回删上游再本地软删（否则 sync TTL 后复活）；对端接口未上线
  （HTTP 404）降级仅本地删，明确拒绝/不可达则不删本地。`/skills/upgrade`（个人转系统级）**暂不对接**。
  异常统一 `SkillHubError(kind=biz|http|network)`（含 httpx 传输层收口，此前会漏成 500）。
- **缺席即墓碑（2026-07-28）**：上游删除 skill 后其 list 不再返回，此前两条同步路径均 upsert-only →
  本地行永存（插件页仍显示、执行下载 404）。现个人面（`sync_user_skills`）与平台面（`reconcile`）在
  **完整取回上游列表后**对缺席的本地行软删收敛，三护栏防误删：① 上游对应子集为**空**整段跳过
  （list 不要求认证，cookie 失效会 200 但个人子集为空——误当"全删"会清光个人 skill 并丢 mute 关系；
  删到 0 个的收敛由手动删除兜底）；② 只动 `synced_from ∈ upload/skill_hub_user/skill_hub` 的行
  （seed/手造行天然豁免）；③ 行龄须过 `OPENOPS_SKILL_ABSENT_GRACE_S`（默认 600s）——防刚上传的行被
  并发同步的旧列表误删。审计：个人面 `skill.deleted/upstream_absent`，平台面进 `asset.reconciled`
  的 `skills_tombstoned` 计数。`_DELETE_ABSENT_CODES` 已回填 `1002`（owner 校验前置后 1002 几乎必是
  "不存在"；误判也会被下轮 sync 拉回自洽）——上游缺席的僵尸行手动删除同样可清。软删被绑定的 skill 时
  绑定行保留（不可变历史），`asset-bindings` 列表将其 `asset_status` 标为 `deleted`。
  **MCP 侧同款缺口未修**（reconcile 对 MCP 仍 create-if-missing，牵扯工具目录与模板引用清洗，单独排期）。
- **管理台平台级写闭环 + 同名收敛（2026-08-16）**：管理台可上传/删除平台 Skill、注册/删除平台 MCP，
  上游一律 `is_system=true`（29.9 §2.1/§3.1，**需 console 超级管理员**，否则 1003 → 我们回 403 而非 502，
  在管理面这是可操作的授权事实）。落点 `app/asset_admin_service.py`：
  ① 上传**先打上游再写本地**（上游是事实源，未成功不碰本地），本地 `skill_key` 取响应 `data.skill_id`
     （`system-{name}`），行是 `source_type='platform'`、owner NULL、`synced_from='platform_upload'`；
  ② **同名收敛**——上传成功后软删幸存行以外的活平台行：同 `skill_key`（`duplicate_key`）或同
     `display_name` 但不同 key（`same_name_rekeyed`，即 Hub 侧改键留下的孤儿，如存量裸名 `foo` 与
     `system-foo` 并存）。危害不止管理台多一行：`resolve_skill_alias` 精确键优先，两行并存时 `/foo`
     会解析到**过期旧行**并下载老包。护栏只有一条（`synced_from ∈ skill_hub|platform_upload`，seed/手造行
     豁免）——**刻意不套宽限期与 asset_in_use**：此处有正向证据（上传返回了权威 key），套上反而让新
     产生的重复永远清不掉。DB 侧兜底 `ux_skill_asset_platform_key`（部分唯一，`where deleted_at is null`），
     并发落败者撞索引后转「加版本」；存量库先跑 `sql/migrate-2026-08-16-platform-skill-key-unique.sql`。
  ③ MCP 注册命中已有行时**原地更新、不改 display_name、不派生新版本**——tool catalog 挂 `mcp_version_id`、
     模板绑定用 `{display_name}::{tool_name}`，重建会让整套标注失联、工具掉回 fail-closed；
     故 reconcile 的 MCP ingest 判重同步改为 `display_name ∪ manifest.server_id` 双维度（否则本地名与
     上游名合法地不一致时，下一轮会把刚收敛掉的重复又造回来）。
  ④ 删除梯子与用户面同构（http 404 降级仅本地删 / biz 1002 视作已删继续 / 1003 → 403 / network 与其余
     biz **不本地删**），`upstream` 结果进审计使降级可观测；**不做 asset_in_use 拦截**（管理员不该被某个
     用户的陈旧绑定卡住，绑定行走 ghost 降级显示「已删除」）。
  ⑤ 管理面列表走 **Admin 门控的 `GET /admin/mcps`**（非用户面 `/assets/mcps`）：endpoint **不脱敏**
     且不隐藏占位资产 —— 用户面按 30.5 截成前 12 字符（那里 endpoint 可能含用户自填凭据），但平台
     MCP 的地址是管理员自己录的基础设施 URL，对录入者藏他自己填的地址没有意义、反而没法核对排障；
     占位资产要能看见才能删。**用户面脱敏口径不变**（有测试双向锁死）。
  异常类型：`ConsoleError`（基类，在 `mcp_registry_client`）→ `SkillHubError` / `McpRegistryError`。
- **管理面编辑 MCP 配置 + URL 变更全量重审（2026-08-18）**：`PUT /admin/mcps/{id}` 推 Hub §3.4
  `mcps/config/update`（PATCH 语义只带非 None 字段；1002=上游已无此 server → 拒绝并提示先重注册，
  http 404 → 降级只改本地）→ 更新本地 endpoint + version manifest（不动 display_name/mcp_id/
  mcp_version_id 三铁律）→ **URL 真变了**才做工具联动：按新地址 discover → sync（复活分支：被缺席
  清除软删过的同名工具重新出现时复活原行，防撞绝对唯一索引 500）→ 发现成功才 `remove_absent_catalog_tools`
  （软删消失工具的目录+标注）→ **该 server 全部 live 工具写 blocked 标注**（reason=URL_RESET_REASON，
  拍板口径：URL 变了视作换了一台服务器，schema 没变也不免审）。
  为什么写 blocked 行而不是软删标注：动态平台工具对「标注缺失」有合成 allowed 的豁免
  （tool_gateway origin=dynamic），软删=放行；blocked 行经 Gateway 热读**立即**拦（含进行中任务）。
  刻意不调 renormalize_drafts：blocked 是「待重标」非终态，扫掉 draft 引用重标完还得重勾；
  重标（save_annotation 复活/覆写）→ 热读立即放行，模板绑定原样恢复。
  管理台右上角「待标注」邮箱（`GET /admin/annotation-inbox`）：派生状态现算（unannotated ∪
  url_reset 按 server 分组），标完自动消失，无通知表无已读态；与列表「待标注」列共用
  `_pending_by_server()` 单一口径。
- **上游改名/改 URL 的同步（2026-08-17，内网实锤）**：同事在 Hub 控制台上改了某个已存在 server 的
  `server_url` + `server_name`（`server_id` 不变），我们这边管理台仍显示旧名旧址、Agent 侧该 server
  的工具也失效。根因不是查不到，是 **ingest 只有 create-if-missing、没有 update 路径**（唯一的写是
  `create_mcp`）。三处一起修：
  ① **reconcile 补 update**：按 `server_id` 优先、`display_name` 次之定位既有行 → 命中即更新
     `endpoint` 与 manifest，**保住 `mcp_id` / `mcp_version_id`**（换了它 tool catalog 与整套标注失联）。
     endpoint 一更新，同轮的目录同步就打新 URL（此前一直拿本地旧 URL 去 discover，每轮进 `tool_sync_errors`）。
     名字撞但 `server_id` 不同 = 两个 server 重名，**不更新**、记 `mcps_name_collision`。
  ② **`display_name` 刻意不跟随上游改名**：它是复合键 `{display_name}::{tool}` 的左半，跟着改会断掉
     全部模板绑定（`tool_key.py` 头注释早已自承这个代价）。上游现名只存 `manifest.upstream_server_name`
     供管理台「上游现名」列展示。**运行时 `_dynamic_mcp_specs` 同步改为按 `server_id` 取本地
     `display_name`** 作复合键左半（取不到才回退上游名）——这才是让 Agent 工具在上游改名后不中断的关键，
     也让那行「与资产入库锚一致」的注释重新成立。口径与 `asset_admin_service.register_mcp` 一致
     （重注册原地更新、不改名）。
  ③ **机机态身份**（29.9 §1.3 新增三级鉴权：无 Cookie 且无 `user_id` 入参 → 401）：新增
     `OPENOPS_CONSOLE_SERVICE_USER_ID`，`mcps/list/query` 与 `skills/list/query` 在调用方未显式给
     `user_id` 时用它兜底（键名 **snake_case `user_id`**，`userId` 已废）。Skill 面此前收了 `user_id`
     却从不下发，同一个洞。**该服务账号的可见范围决定我们能同步到哪些资产。**
  summary 新增 `mcps_updated` / `mcps_renamed_upstream` / `mcps_name_collision`；启动横幅补打
  `mcpregistry_base` / `console_service_user` / `reconcile_loop`（此前这三项都不打，排障只能猜）。
- **MCP Registry（✅内网已通）**：`list_servers` → `POST /obsv/agent/management/mcps/list/query`（source=openops 翻页，
  鉴权走上述统一装配；本地无 IAM 登录态时可临时配 `OPENOPS_MCPREGISTRY_COOKIE`，会话态会过期）；`discover_tools(server_url)` 按 `OPENOPS_MCP_ROUTE` 走
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

## 运行时治理旋钮（E4；2026-07-12）

两类改法：**env 变量**改后端启动环境（内网 run-backend.bat / 本机 shell），改完须重启进程；
**模板画像字段**走管理台模板编辑器（或 admin API 存草稿+发布），发布后存量实例在下一次任务边界自动派生升级，免重启。

### env（主 Agent / 平台面，重启生效）

| 变量 | 默认 | 作用 |
|---|---|---|
| `OPENOPS_MAIN_MAX_ITERS` | 20 | 主 Agent ReAct 循环上限（ReActConfig.max_iters） |
| `OPENOPS_MAIN_TOOL_RESULT_LIMIT` | 24000 | 主 Agent 单条工具结果进上下文保留 token（ContextConfig.tool_result_limit） |
| `OPENOPS_SUBAGENT_TIMEOUT_S` | 300 | 单个子 Agent 执行预算（监控循环实现；**审批等待期不计入**） |
| `OPENOPS_ASK_TIMEOUT_S` | 300 | 审批（ASK）等待超时，主/子/沙箱 Bash 共用 |
| `OPENOPS_MCP_RESULT_CAP` | 24000 字符 | MCP client 网络层对工具返回文本的字符截断（第一层） |
| `OPENOPS_MCP_TIMEOUT_S` | 30 | MCP tools/call 全程超时 |
| `OPENOPS_SKILL_TIMEOUT_S` / `OPENOPS_SKILL_OUTPUT_MAX_BYTES` | 600 / 2MiB | 沙箱内 Skill/Bash 执行超时与输出上限 |
| `OPENOPS_SANDBOX_SWEEP_INTERVAL_S` | 60 | idle 沙箱容器后台回收间隔（秒）；0=关（懒回收仍在关会话/容量满触发） |
| `OPENOPS_SANDBOX_LABEL_SCOPE` | default | docker 档容器标签 scope，隔离同宿主多环境的孤儿清理范围 |
| `OPENOPS_MODEL_CONNECT_TIMEOUT_S` / `OPENOPS_MODEL_READ_TIMEOUT_S` / `OPENOPS_MODEL_MAX_RETRIES` | 10 / 300 / 0 | 模型客户端（主+子共用 _build_model） |

### 模板画像字段（子 Agent / 派发预算，发布后下个任务生效）

| 字段 | 校验域 | 默认（seed） | 作用 |
|---|---|---|---|
| `sub_agents[].max_iters` | 1..200 | 20（recover=10） | 子 Agent ReAct 循环上限 |
| `sub_agents[].tool_result_limit` | 1000..200000 | 24000 | 子 Agent 单条工具结果保留 token |
| `main.max_children` | 1..10 | 3 | 同时活跃子 Agent 上限（另有单批硬上限 _MAX_BATCH=5） |
| `main.delegation_max_spawns` | 1..100 | 10 | 单 task 累计派发兜底（防失败重派死循环） |
| `activity_labels.tools` | 非空字符串映射 | `{}` | 活动栏工具业务名称；优先匹配完整工具名，其次匹配 MCP 内层工具名，只改展示不改调用 |

每次 `dispatch_subagents` 调用都会为该批生成 `dispatch_batch_id` 和 task 内递增的
`dispatch_batch_no`。同批 worker 共享批次，但各自使用独立 `delegation_id` 与 `child_task_id`；
所有子 Agent 生命周期、模型、工具、Skill、沙箱和审批事件均携带这些关联字段。业务汇报事件只写
后端脱敏、截断后的 `report_summary`，完整正文不会进入活动接口。
工具/Skill/沙箱事件同样使用 deny-by-default 白名单，只保留最长 300～500 字的参数、结果和错误摘要；
完整 arguments、MCP 响应与 stdout/stderr 不进入审计或 AG-UI CUSTOM。审批卡所需 `args` 是唯一例外，
但会递归脱敏并限制深度、项数和总长度。

### 不变式（D7 事故教训，校验不强制、人必须守）

**tool_result_limit < 模型上下文窗口，经验 ≤ 1/3**（GLM 128k 窗口 → ≤ 40000）。
老项目曾配 160000 > 128000：单条工具结果直接撑爆窗口，压缩 fallback 删最老消息把用户问题删掉。
校验上限 200000 是为大窗口模型留的余量，**不是**安全值；换小窗口模型时要同步压低。

双层截断关系：`OPENOPS_MCP_RESULT_CAP`（字符，网络层先砍）→ `tool_result_limit`（token，agentscope 上下文层兜底）。

### LLM egress（S3 安全三件，2026-07-13）

| 变量 | 默认 | 说明 |
|---|---|---|
| `OPENOPS_LLM_EGRESS_BLOCK_PRIVATE` | 0（内网放行 RFC1918） | 公网部署置 1 全锁私网（环回/链路本地/metadata/docker bridge 恒拦不受此开关影响） |
| `OPENOPS_LLM_EGRESS_PIN` | 1 | 解析钉扎：同 host 解析集与首见集完全不相交=疑似 DNS rebinding → 拦；合法迁移重启后端或置 0 |
| `OPENOPS_LLM_EGRESS_PIN_TTL_S` | 86400 | 钉扎有效期 |
| `OPENOPS_LLM_EGRESS_DENY` / `_DENY_HOSTS` | docker bridge / localhost,metadata | 额外 deny 网段/主机 |

残余风险（已知）：check→实连之间的单次 TOCTOU 窗口仍在（消除需自定义 DNS transport，V1 不做）；
用户 LLM 失效（配置被删/禁用）时**降级平台默认**——C2-OBS-003 的口径已从「硬失败」改为
「降级 + 显式告知」：绑定期 fail-closed 校验（绑无效配置直接 403），运行期降级必发
`model.user_llm_degraded` 审计 + `openops.model.user_llm_degraded` SSE，工作台弹横幅告知用户
本次改用了平台默认模型、请重新选择。原硬失败会让绑过被删模型的 Agent 每次起任务都 403 变砖，
代价高于收益；风险改由「用户看得见」来控，故 degraded 不得为空（见 test_sec_s3_user_llm_degrades_loudly）。

## IAM 双步鉴权（B9；2026-07-13，老项目 D4 机制迁移）

| 变量 | 默认 | 说明 |
|---|---|---|
| `OPENOPS_IAM_ENABLED` | false | true=公司 IAM cookie 双步握手替代 X-OpenOps-Mock-*（mock 头即失效） |
| `OPENOPS_IAM_ACCESS_TOKEN_URL` | — | 固定 HTTPS（禁止 `{host}`/userinfo/query/fragment）；步1 GET，headers=Cookie+IAM-Client-Ip（XFF 首跳），判 `code=="201"` 取 access_token/accessToken |
| `OPENOPS_IAM_USERINFO_URL` | — | 固定 HTTPS（禁止 `{host}`/userinfo/query/fragment）；步2 GET，`Authorization: <裸token>`（无 Bearer 前缀） |
| `OPENOPS_IAM_LOGIN_KEY_FIELD` | id | userinfo 取用户标识的点分路径（如 `data.user.id`）；strip+lower 后作 user_id |
| `OPENOPS_IAM_DISPLAY_NAME_FIELD` | name | 展示名字段路径；缺失回退 login_key |
| `OPENOPS_IAM_CACHE_TTL_S` | 300 | 进程内 TokenCache（SHA-256(cookie)→身份，上限 1024 条），TTL 内不重打 IAM |
| `OPENOPS_IAM_LOGIN_URL` | — | 配了则 401 响应体带 `login_url`，前端自动跳登录 |
| `OPENOPS_IAM_SIGNOUT_URL` | — | IAM SSO 登出端点（对称于 login，**只收 POST+CSRF 头**）。`POST /auth/logout` 返回给前端；前端**后台 fetch POST**（带 CSRF 头）调它清 SSO，**不是**浏览器 GET 导航（GET 过去 404 白页） |
| `OPENOPS_IAM_CSRF_COOKIE_NAME` / `..._HEADER_NAME` | IAM-Csrf-Token | 前端从该 cookie 取 CSRF token，放进 signout POST 的该 header |
| `OPENOPS_IAM_LOGOUT_REDIRECT_URL` | — | 登出清 SSO 后浏览器回跳地址（支持 `{host}`；未配回落 `login_url`）。设 `/openops/` 而非 `/` 免进 console 登录流 |

TLS 与代理沿用三档口径（OPENOPS_TLS_CA_FILE ＞ OPENOPS_TLS_INSECURE=1 ＞ certifi；truststore 自动注入见 run.py）。
失败语义：401（会话无效/无用户标识，带 login_url）/ 502 `IAM_UPSTREAM`（IAM 不可达）；白名单 403 与 mock 模式同一条链。
用户行：IAM 首次校验通过即 upsert `sre_openops_user`（角色默认 user，仅建行不授权）；**使用权仍由白名单闸控制**（管理员显式开通）。
