# OpenOps V1 后续开发块

> 权威计划在 Obsidian《33-OpenOps V1后续开发计划》（B0-B9，2026-07-09 重排：真 AgentScope 提前到 B1）。
> 本文件是 repo 内的进度镜像，块编号与 33 号一致；如有出入以 33 号为准（B4-DOC-001 已对齐）。

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

## 待办（块序同 33 号）

## B7·二 管理台真能力补齐（剩余）

- 模板版本「另存新版本/发布/禁用」写闭环、模板资产「绑定/解绑」写路径（按模板 content_json 过滤展示）。
- 白名单页管理动作、审计 Trace 串联增强。
- 覆盖 ADMIN-* 用例：普通用户 forbidden、管理员修改写审计、标注变化影响 Tool Gateway 运行时判断（已有）。
- 28.7「模板升级 → 已有实例边界自动派生配置版本」（`config.version.derived`/`config.changed_notice`）——B6 只落了标注热读，此项归本块（B6-SCOPE-001）。
- ⚠ 部署注意：升级到 B7·一 需在既有 PG 上执行两新表 DDL（`model_asset`/`model_access_grant`，schema.sql 幂等可直接重放）；旧 `platform_runtime_config` 的 `platform_model` 域已废弃不再读写。

## B8 Docker Sandbox + 用户 Skill 执行

- 用户级容器、task 独立工作目录、资源限制、artifact 回传；执行前校验包 checksum。
- 沙箱不注入 Cookie/Secret/`X-OpenOps-*`/`effective_appids` 明细；Runner 代理只经 Tool Gateway。
- 覆盖 SKILL-* / SANDBOX-* 用例：超时、日志截断、Secret/Cookie 不进沙箱。

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
