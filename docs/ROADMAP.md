# OpenOps V1 后续开发块

> 权威计划在 Obsidian《33-OpenOps V1后续开发计划》（B0-B9，2026-07-09 重排：真 AgentScope 提前到 B1）。
> 本文件是 repo 内的进度镜像，块编号与 33 号一致；如有出入以 33 号为准（B4-DOC-001 已对齐）。
> **差距盘点**（设计承诺 vs 实现现状，四档 + 明确豁免 + 建议顺序）见 Obsidian《34-V1实现差距盘点与剩余开发清单》（2026-07-10）——判断「还剩哪些未开发」以 34 号为权威。

## 已完成

| 块 | 内容 | 关键提交 | 冒烟 |
|---|---|---|---|
| B0 | 最小闭环收口与文档对齐（/agent-runs/:id 按 run 恢复、router→service 分层） | `bdd589b` | — |
| B1 | 真 AgentScope 2.0.3 Runtime 骨架（stub model 复刻 RCA + 原生 permission ASK；mock 留 test-double，`OPENOPS_RUNTIME` 切换） | `249081f` | docs/test/B1-* |
| B2 | Model Gateway + GLM（平台模型元数据 + env Key 构建 `OpenAIChatModel`，无 Key 回退 stub；`model.call.*` 事件；脱敏失败） | `e4feef2` `6512d2b` `a3a9380` | docs/test/B2-* |
| B3 | Scope Service + oModel 可切换 adapter（`OPENOPS_OMODEL`；30s TTL、分态 fail-closed、revision 回写） | `96d7702` | docs/test/B3-* |
| B4 | Tool Gateway + 平台 HTTP MCP 受控调用链（标注/Scope/Secret/`X-OpenOps-*` header/审计；用户 MCP 隔离） | `0cd9898` | docs/test/B4-* |
| B5 | CopilotKit/AG-UI 工作台接管（`POST /agent-runs/{id}/agui` AG-UI 标准事件流 + `@ag-ui/client` HttpAgent 适配器 + 流式对话；`VITE_OPENOPS_TRANSPORT` 可回退 SSE。口径：**headless**——AG-UI 协议 + HttpAgent 客户端，UI 仍为本设计系统自定义组件（30.3/32 号拍板），非 CopilotKit 成品聊天组件，B5-OBS-001） | `fcef0f8` `deca093` | docs/test/B5-* |
| B6 | 资产对账 + 配置热更新 + 设置页写闭环（`derive_config_version` 不可变链结转绑定；reconcile：source=openops/checksum 补版本/schema_hash 变化标注不继承；Gateway 边界热更新 + `runtime_plan.updated`；设置页三 tab 写闭环 + `POST /assets:reconcile`） | `c7f4118` | docs/test/B6-* |
| B7·一 | 管理台 IA 重构 + 模型资产白名单（2026-07-09 原型对齐：导航 6→5、模板 drill「资产治理→Tool 标注」标注全局一份、标注保存接通；`model_asset`/`model_access_grant` 两新表（DDL 20→22）、按人授权、三处 fail-closed gating：列表过滤/select-model `MODEL_NOT_AUTHORIZED`/Gateway 二次校验） | 见 git log | — |
| B7·二 | 模板版本写闭环 + 模板工具集 enforcement + 28.7 升级自动派生（草稿 upsert/发布切 active 不可变（再发布 409）/禁用摘指针；`_validate_content` 只许 allowed 标注 tool；`TaskState.template_tools` 双 runtime fail-closed（gateway 模板门 + agentscope toolkit 剪枝）；任务边界 `_derive_if_template_upgraded` 结转 overlay+绑定、审计 `config.version.derived`、SSE `config.changed_notice`；前端模板编辑器真表单（载入前禁写防空发布）+ 资产治理「绑定/解绑」草稿写路径；playwright e2e 过） | 见 git log | docs/test/B7-* |
| B7·补 | GPT B7 报告修复（B7-SEC-001：`template_tools` 哨兵化 `None`/空集——空模板=零平台工具，mock 门与 agentscope 剪枝双 runtime fail-closed，顺带消掉空模板的无意义 `runtime_plan.updated`；顺修 `_validate_content` 误放行 blocked 标注 tool + tool_blocked 收口在 rca 为 None 时结论丢失；B7-TEST-001：补发布期重校验 400 + 模板写面 403 用例；66/70 双绿） | 见 git log | docs/test/B7-* |
| B8 | Docker Sandbox 执行面（2026-07-09 新口径）：`SandboxExecutor` per-user 容器**会话期常驻**（run 开启边界容量准入 `SANDBOX_CAPACITY_FULL`/strict_ttl 腾位，末 run 关闭→idle→TTL 回收）；`OPENOPS_SANDBOX=fake(默认)\|docker` 双后端（fake=tempdir+subprocess 进程内、docker=aiodocker+agentscope `DockerBackend`，安全基线 非root/只读rootfs/cap_drop=ALL/不注入平台上下文）；`run_skill`（checksum 门+entrypoint shell 执行+超时/2MiB 截断+output.json）；容器内受控 Bash 四层裁决（`command_guard`：平台 deny→agentscope 原生 `Bash.check_permissions` 内置分析→只读放行/非只读 ask→容器隔离，无 agentscope 回退分类器、决策两路径一致）+ `sandbox.command.*` 审计；管理台容器列表(active_run_count)+强制销毁。真容器安全基线（非root/只读rootfs/cap_drop=ALL/不注入上下文/跨用户隔离）实机验证；写盘/run_skill 落盘执行经 B8·补 修复（只读 rootfs 下 tmpfs mode=1777 + exec base64 绕 put_archive）并加实机回归护栏 | 见 git log | docs/test/B8-* |

## 待办（块序同 33 号）

## B7·三 管理台小尾巴（后置）

- 白名单页管理动作、审计 Trace 串联增强、模板「禁用版本」前端入口（后端已具）。
- ⚠ 部署注意：升级到 B7·一 需在既有 PG 上执行两新表 DDL（`model_asset`/`model_access_grant`，schema.sql 幂等可直接重放）；旧 `platform_runtime_config` 的 `platform_model` 域已废弃不再读写。

## B8 剩余（执行面已落，见上表 B8 行）

- **实机 Docker E2E**：`OPENOPS_SANDBOX=docker` 需装 `pip install -e ".[sandbox]"`（aiodocker）+ compose 挂 Docker socket；生产内网镜像离线预构建 + `docker load`。真容器安全基线 + 写盘 + run_skill 执行 + 跨用户隔离已实机验证（`OPENOPS_SANDBOX_DOCKER_TEST=1` 跑 `test_docker_real_run_skill_write_exec_isolation` 回归护栏）；默认 fake 后端覆盖生命周期/checksum/超时/裁决/审计。
- **live agent 驱动 Bash-in-conversation**：`command_guard`/`run_bash` 四层裁决与审计已就位并单测；真 GLM 自主调 Bash 的 HITL 靠 agentscope `RequireUserConfirmEvent` 桥（B1 `_handle_ask`），随真 Key live E2E 一并验（无 Key 期回退 stub/fake）。
- **Skill Hub 真包投递**：`run_skill` 执行原语已就位（fake 注入包字节验证）；真 ZIP 从 Skill Hub 经 29.3 `X-Checksum-SHA256` 下载的装配路径待集成。
- 平台 deny 前缀规则 UI（管理台下发 `bash_deny_prefixes`）、artifact 回传落地、chunk 拆包沿用 ROADMAP 附注。

> 运行开关：`OPENOPS_RUNTIME=mock|agentscope`、`OPENOPS_OMODEL=mock|real`、`OPENOPS_SANDBOX=fake|docker`（默认 fake，pytest 不依赖 Docker）；`OPENOPS_SKILL_TIMEOUT_S`/`OPENOPS_SKILL_OUTPUT_MAX_BYTES` 调 Skill/命令执行限额。

## B9 真实 W3/IAM + E2E + 发布准备

- 用公司 W3/IAM Cookie introspect 替换 `X-OpenOps-Mock-User`；保持白名单/管理员/owner 隔离语义。
- Playwright E2E：准入、初始化、对话+GLM RCA、ASK、取消、关闭、设置页、管理台 forbidden/admin。
- 发布检查：DDL、敏感信息、禁用功能入口、审计串联。

## 附：已知跨块待办

- 真实 GLM Key 的 live 端到端（B2 runbook §9，待有 Key 环境执行）。
- AgentState 跨进程 PG 持久化（现 per-run 内存，B1 备注）。
- `/me` 返回平铺结构（`data.user_id`），联调按当前实现读（B4-OBS-001）。
- 前端 npm audit 2 项依赖提示，单独评估升级（B*-DEP-001）。
- 前端主 chunk >500KB（Vite 提示），后续 code splitting（B5-FE-001/B6-FE-001）。
- lifespan 收口直接遍历 `task_registry._by_run`，可提取为 `task_registry.drain()` 公有入口（B5-BE-001 修复的整洁化跟进）。
- Tool Gateway 每工具边界一次标注 DB 读（读失败回退快照）；高频循环/多实例场景评估短 TTL 内存缓存（B6-PERF-001）。
