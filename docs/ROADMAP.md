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
| B5 | CopilotKit/AG-UI 工作台接管（`POST /agent-runs/{id}/agui` AG-UI 标准事件流 + `@ag-ui/client` HttpAgent 适配器 + 流式对话；`VITE_OPENOPS_TRANSPORT` 可回退 SSE） | `fcef0f8` | — |

## 待办（块序同 33 号）

## B6 资产对账 + 配置热更新 + 用户设置页写闭环

- 接入真实 Skill Hub / MCP Registry（source=openops、checksum、schema_hash 变化重新标注）。
- 登录对账、配置页 refresh、后台 reconciler；RuntimePlan 边界热更新。
- 用户设置页 main role append、实例级 LLM、用户 Skill/HTTP MCP 上传绑定解绑删除；`ASSET_IN_USE`。

## B7 管理台真能力补齐

- 模板版本保存/发布/禁用、MCP Tool 标注编辑、白名单、沙箱容量（reason 必填）、审计 Trace 串联。
- 覆盖 ADMIN-* 用例：普通用户 forbidden、管理员修改写审计、标注变化影响 Tool Gateway 运行时判断。

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
