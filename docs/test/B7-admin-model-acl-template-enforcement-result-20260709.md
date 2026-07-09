---
title: B7 管理台 IA 重构 + 模型资产白名单 + 模板版本写闭环 + 模板工具集 enforcement + 28.7 升级派生 测试报告
date: 2026-07-09
tester: Claude (Opus 4.8)
branch: feat/workbench-frontend
commit: 7695453
target_commit: 8ef83d1 + 7695453
---

# B7 管理台/模型 ACL/模板写闭环/工具集 enforcement 测试报告

## 结论

B7 主体验证通过。`feat/workbench-frontend@7695453` 中的两个 B7 提交
（`8ef83d1 B7·一 管理台 IA 重构 + 模型资产白名单`、`7695453 B7·二 模板版本写闭环 + 模板工具集 enforcement + 28.7 升级自动派生`）
已具备：模型资产新表 + 按人白名单授权（三处 fail-closed gating：列表过滤 / select-model `MODEL_NOT_AUTHORIZED` / Model Gateway 二次校验）、模板版本草稿 upsert + 发布切 active 不可变（再发布 409）+ 禁用摘指针、`_validate_content` 只许 allowed 标注 tool 绑定、**模板工具集运行时 enforcement（mock 与 agentscope 双 runtime fail-closed）**、28.7 使用时派生（模板升级 → 任务边界自动派生新配置版本，结转 overlay + 资产绑定 + 幂等 + `config.version.derived` 审计 + `config.changed_notice` SSE）。

本轮**没有发现阻断 B7 演示的 P0/P1 问题**。核心安全不变量（未授权模型不可选、模板外平台工具在生产 runtime fail-closed）在自动化用例中成立，且 **B6-RT-001 关注的「生产 runtime 未被 fail-closed 断言覆盖」已被 B7·二 修复** —— `test_template_tools_enforcement` 现在同时跑 `[mock]` 与 `[agentscope]` 并均通过。

记录 **1 个 P3 硬化项**（模板 `default_tools=[]` 时 mock 路径工具门 fail-open，生产 runtime 不受影响，已探针确认）与 4 个 P3 / 观察项（发布期重校验无自动化覆盖、前端主 chunk >500KB、npm audit、部署 DDL 提示）。

## 测试对象与环境

| 项目 | 结果 |
|---|---|
| 测试位置 | 原工程目录（未另开 worktree），分支 `feat/workbench-frontend` |
| 当前 HEAD | `7695453 B7·二 ...`（含 B7·一 `8ef83d1` + B7·二 `7695453`） |
| 被测提交 | `8ef83d1`（B7·一）、`7695453`（B7·二） |
| 后端 Python | 3.11.7（`backend/.venv`） |
| AgentScope | 2.0.3（venv 内可用，用于 fail-closed 双 runtime 断言） |
| PostgreSQL | 复用本机 `openops-v1-pg`，`localhost:5432`，healthy（每用例从 DDL 幂等重放 + TRUNCATE） |
| 后端执行方式 | pytest + FastAPI `TestClient`（进程内，走完整 lifespan / PG） |
| 运行态 | `OPENOPS_RUNTIME=mock`（pytest 默认）+ `agentscope`（fail-closed 类用例参数化双跑） |
| oModel | `OPENOPS_OMODEL=mock` |

未写入、未打印、未保存任何真实 API Key、Authorization、Bearer token、Cookie、完整 endpoint 或 prompt/messages。脱敏扫描见下。

## 基础回归

| 检查项 | 结果 | 备注 |
|---|---:|---|
| 后端单测 | 通过 | `66 passed, 1 warning in 11.15s`（B6 为 49；B7·一 +7 MODEL-ACL、B7·二 +模板用例及 fail-closed 双 runtime 参数化） |
| `backend/tests/test_model_acl.py` | 通过 | 7/7：可见性过滤 / select 403 / Gateway 回退 / 授权撤销 / 审计 churn / 403 / 注册去敏+去重 |
| `backend/tests/test_templates.py` | 通过 | 6 项（含 enforcement `[mock]`+`[agentscope]`）：草稿校验+upsert / 发布切换+不可变 / 禁用下架 / 升级派生 / 双 runtime 工具集 fail-closed |
| 前端 `npx tsc -b` | 通过 | exit 0 |
| 前端 `npm run build` | 通过 | 主 chunk `index-Ca3EKvWy.js 549.65 kB`（>500 KB 警告，见 B7-FE-001） |
| `npm audit` | 观察 | 2 vulnerabilities（1 moderate、1 high），见 B7-DEP-001 |
| DDL 静态检查 | 通过 | `sql/openops_v1_core.sql` 无 `FOREIGN KEY`/`REFERENCES`/`CREATE TRIGGER`/`CREATE FUNCTION`；新表 `model_asset`/`model_access_grant`/`agent_team_template(_version)` 就位 |
| 分层静态检查 | 通过 | routers 无 `from infra`；runtime 无 `from app`（`run_state_service` 内 `from app import ...` 为 app→app 局部导入，合规） |
| 管理台鉴权 | 通过 | 全部 `/admin/*`（模板写面 + 模型资产）均带 `Admin` 依赖 → 非管理员 403（`test_model_acl_006` 已断言 model-assets 面） |

## B7 验收标准逐项

### B7·一 模型资产白名单（模型 ACL，30.6 五 / 18 号 model_asset·model_access_grant）

| 验收 | 结果 | 证据 |
|---|---:|---|
| 列表按授权过滤（scope=all 全员可见 / restricted 仅被授权可见 / disabled 不出现） | 通过 | `test_model_acl_001_visibility_filtered_by_grant` |
| select-model fail-closed（未授权 restricted / 未知模型 → 403 `MODEL_NOT_AUTHORIZED`） | 通过 | `test_model_acl_002_restricted_select_403` |
| 授权后可见可选、撤销后 fail-closed | 通过 | `test_model_acl_004_grant_then_select_ok` |
| Model Gateway 二次校验（选中值被撤销授权 → 忽略选中回退默认 → 无可用回退 stub） | 通过 | `test_model_acl_003_gateway_second_check_fallback`（`resolve_runtime_model(selected, user_id)` 只在用户可用集合内解析） |
| 保存授权写审计 `model_asset.grants_updated` + 软删+插新（不原地改写） | 通过 | `test_model_acl_005_grants_audit_and_churn`；`replace_grants` 软删 active 行→插新集合 |
| 管理端点非管理员 403 | 通过 | `test_model_acl_006_admin_endpoints_forbidden_for_user` |
| 注册走 DTO 白名单字段（`api_key` 天然进不来）+ `model_id` 去重 | 通过 | `test_model_acl_007_register_ignores_sensitive_and_dedups` |
| Secret 不落库/事件/审计（只存 `secret_env_var` 环境变量名） | 通过（补测） | 脱敏探针：注册塞 `api_key`/`token`/`authorization` 后，`audit_event.payload` 与 `model_asset` 全表 0 明文密钥；Gateway spec 仅返回 `{provider,model_id,display_name,base_url,secret_env_var}` |

### B7·二 模板版本写闭环 + 工具集 enforcement + 28.7 升级派生

| 验收 | 结果 | 证据 |
|---|---:|---|
| 草稿 upsert（同稿反复改不涨版本号）+ `_validate_content` 拒未 allowed 标注 tool（400 列坏名） | 通过 | `test_template_draft_validates_allowed_tools`（`no_such_tool` 400；重存草稿 `version_no` 保持 2、`template_version_id` 不变） |
| 发布：draft→active、旧 active→archived、切模板指针、普通用户见新版本；发布后不可再发（不可变） | 通过 | `test_template_publish_switches_active_and_immutable`（再发布 409 `CONFIG_VERSION_INVALID`） |
| 禁用 active → 摘指针、从 `templates/available` 下架 | 通过 | `test_template_disable_active_removes_from_available` |
| **模板工具集 enforcement（模板未绑定的平台工具即使全局 allowed 也 fail-closed，双 runtime）** | 通过 | `test_template_tools_enforcement[mock]` + `[agentscope]` 均 `_assert_recover_blocked(TOOL_BLOCKED)`（mock：gateway 模板门硬失败；agentscope：`_build_toolkit` 剪枝模板外工具） |
| 28.7 使用时派生：模板升级 → 下一次任务边界自动派生新配置版本，结转 overlay + 资产绑定 + 幂等 | 通过 | `test_template_upgrade_derives_instance_config`（指针升级、`change_reason="template upgraded"`、`main_role_append` 结转、绑定结转、`config.version.derived` 审计、`len(derived)==1` 幂等） |

## Runtime 分叉验证（mock vs agentscope）—— B6-RT-001 覆盖修复

B6 报告将「两个热更新 fail-closed 用例是 mock 专属、生产 runtime（agentscope）未被自动化断言覆盖」记为 P2（B6-RT-001）。B7·二 已针对性修复：

- conftest 新增 `runtime_backend` 参数化 fixture（`["mock","agentscope"]`，无 agentscope 环境自动降级只跑 mock）。
- `test_template_tools_enforcement` 挂 `runtime_backend`，本轮在 **agentscope 2.0.3 实装环境**下 `[mock]` 与 `[agentscope]` **均通过**。
- agentscope 侧：`recover_execute` 在 `_build_toolkit` 期按 `st.tool_annotations`（已按 `template_tools` 过滤）被剪枝并记 `pruned=(name, TOOL_BLOCKED)`；`recover_execute` 封装内 `ToolBlocked` 置 `st.tool_blocked=True`（B6-RT-001 的「压回结论」修复亦在位）。

安全不变量「模板外平台工具在两条 runtime 均不执行」成立。

## 脱敏检查

补充脱敏探针（注册模型资产时故意注入 `api_key` / `token` / `authorization: Bearer ...`，保存授权触发 `grants_updated` 审计）后扫描：

| 目标 | 结果 |
|---|---:|
| `audit_event.payload_redacted_json` 全表 | 无明文密钥 / `Authorization` / `Bearer ` |
| `model_asset` 全表（`row_to_json`） | 无明文密钥；仅存 `secret_env_var`（环境变量名，设计允许） |
| `model_gateway.resolve_runtime_model` 返回 | 仅 `provider/model_id/display_name/base_url/secret_env_var`，无 `api_key`、无密钥明文 |

## B1–B6 兼容结果

| 兼容项 | 结果 |
|---|---:|
| B1 AgentScope runtime 主链路 | 通过（enforcement 用例 agentscope 分支正常推进、剪枝、收口） |
| B2 Model Gateway | 通过；B7 将数据源由 `platform_runtime_config(DOMAIN_MODEL)` 迁至 `model_asset` 并叠加按人授权二次校验，`resolve_runtime_model` 签名改为 `(selected, user_id)`，唯一调用点 `run_state_service.start_task` 已对齐 |
| B3 Scope Service | 通过（`scope.resolved` 正常，派生/enforcement 未触及 scope 契约） |
| B4 Tool Gateway | 通过；B7·二 在平台分支加「模板门」，在 B6「每边界取最新标注」之前先判模板集合 |
| B5 AG-UI / SSE | 未在本轮重复联调（B7 未触及 AG-UI/SSE envelope；新增 `config.changed_notice` 走既有 `events.publish`） |
| B6 资产对账 / 配置热更新 / 设置页 | 通过；`test_assets.py` 随 fail-closed 参数化仍全绿，无回归 |
| 全量后端单测 | 66 passed，无回归 |

## 发现的问题与建议

### B7-SEC-001 P3：模板 `default_tools=[]` 时，mock 路径工具门 fail-open（生产 runtime 不受影响）

**探针确认**（mock runtime，管理员发布 `main.default_tools=[]` 的模板版本、实例落其上、跑任务）：

```
openops.tool.call.started    action=recover_execute
openops.tool.call.succeeded  action=recover_execute ext=req_361a83793e   ← 已执行
（无 openops.tool.blocked；且未经 ASK）
```

即空工具集实例上，变更类 `recover_execute` 既未被模板门拦截、也未走审批，直接执行。

根因：
- `tool_gateway.invoke` 的模板门写作 `if st.template_tools and tool_name not in st.template_tools`——`template_tools` 为**空集**时该门被跳过；
- 随后 `_effective_annotation`（28.7 热更新）**回读 DB 最新标注、不按模板集合过滤**，`recover_execute` 全局 allowed → 放行；
- 而 `TaskState.template_tools` 默认即空集（`field(default_factory=set)`），无法区分「非模板流程（无限制）」与「模板显式空工具集（应零平台工具）」。

影响与定级：**生产 runtime（agentscope）不受影响**——其 `_build_toolkit` 按 `st.tool_annotations`（已按 `template_tools` 过滤 → 空）剪枝，`recover_execute` 直接 `TOOL_NOT_ANNOTATED` 不进 toolkit（本轮 `[agentscope]` 用例佐证）。故非 P0/P1。但 `tool_gateway` 顶部 docstring 自述为「唯一受控调用点」，B7·二 明确在此加模板门以做**运行时无关**的 fail-closed；空模板这一退化配置下该保证不成立（mock/脚本编排面），属防御纵深一致性缺口。次要现象：空模板实例上每个全局 allowed 工具会触发一条无意义的 `openops.runtime_plan.updated`（热读 snap=None≠DB-allowed）。

建议：
- 用哨兵区分「无模板流程」（`None`）与「模板空工具集」（`set()`）；后者应零平台工具。
- 或让 `_effective_annotation` 结果与 `st.template_tools` 取交集（模板始终为工具集合上界），使 gateway 模板门成为名副其实的运行时无关 chokepoint。
- `_validate_content` 可评估是否禁止发布 `default_tools=[]`（若产品不允许无工具模板）。

### B7-TEST-001 P3：发布期重校验 + 模板写面 403 无自动化覆盖

`publish()` 会二次 `_validate_content(ver["content_json"])`，以拦截「草稿期 allowed、发布前被禁用/改标注」的 tool——逻辑正确但**无用例**覆盖该状态迁移（草稿绑 allowed tool → 该 tool 被 block → 发布应 400）。另模板写端点 403 依赖共享 `Admin` 依赖（构造上成立），但未像 model-assets 那样显式断言。建议各补一条固化回归护栏。

### B7-FE-001 P3：前端主 chunk 超过 500 KB

`npm run build` 通过，主 chunk `index-Ca3EKvWy.js 549.65 kB`（承 B5-FE-001 / B6-FE-001，管理台真表单接入后自 537.47 略增）。上线前建议按管理台 / 工作台 / AG-UI 依赖做路由级拆包或 `manualChunks`。

### B7-DEP-001 P3：前端依赖 2 个 npm audit 漏洞

`npm audit` 显示 1 moderate、1 high（与 B5/B6-DEP-001 一致，未见新增）。上线前建议补一轮依赖审计。

### B7-OBS-001 观察：部署需重放两新表 DDL

升级到 B7·一 的既有 PG 需执行 `model_asset` / `model_access_grant` 两新表 DDL（`schema.sql` 幂等可直接重放）；旧 `platform_runtime_config` 的 `platform_model` 域已废弃不再读写（ROADMAP/commit 已注明）。pytest 每用例从 DDL 重放，故测试库始终为最新 schema。

## 未覆盖 / 未执行

- **浏览器 E2E 未在本轮重跑**：与 B5/B6 同口径，前端以 `npx tsc -b`、`npm run build`、AdminConsole / TemplateEditorModal / api facade 代码路径核对为验证依据；B7·二 提交自述 playwright(chromium-1217) e2e（编辑→存草稿→发布→badge 升版、治理 drill 绑定/解绑翻转+还原）已过，本轮未复现执行。
- **用户 Skill 运行时执行**：属 B8 沙箱执行面，未接入，不在本轮范围。
- **真 GLM/真模型 live 端到端**：无 Key，走 stub/mock 回退（`resolve_runtime_model` 无可用真模型 → None → stub），未打真网。

## 总体建议

B7 可作为「管理台 IA 重构 + 模型资产白名单 + 模板版本写闭环 + 模板工具集 enforcement + 28.7 升级派生」的 smoke 通过版本合入主线。**B6-RT-001 关注的生产 runtime fail-closed 覆盖缺口已由 B7·二 的双 runtime 参数化用例修复**，核心安全不变量（未授权模型不可选、模板外平台工具不执行）在自动化用例成立，脱敏零泄漏。推进 B8 前建议按成本顺序处理：

1. **B7-SEC-001**：区分「无模板」与「空模板」语义（哨兵或交集），让 `tool_gateway` 模板门在空工具集下也 fail-closed，兑现「唯一受控调用点」的运行时无关保证；顺带抑制空模板实例的无意义 `runtime_plan.updated`。
2. **B7-TEST-001**：补发布期重校验 + 模板写面 403 两条用例。
3. B7-FE-001 / B7-DEP-001 随前端上线批次统一处理（路由级拆包 + 依赖审计）。
