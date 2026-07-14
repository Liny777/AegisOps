# 使用 CopilotKit + AG-UI 重构多 Agent 活动栏

> 状态：实现完成，待内网真实 CopilotKit + AG-UI + GLM 链路验收
> 保存日期：2026-07-14
> 参考老项目提交：`216d4dba2739ae9b4c90120267db4dfacd475d58`、`3a481c3833b11cde9f1a04f24fbeb6ef67126462`、`c34227066ed3c9eaf589edfd5cbade86c82f6286`，以及最终右栏架构的必要基线 `dd48e69`。

## 概要

- 以老项目 `c342270` 时点的最终交互为参考，不直接 cherry-pick；旧版缺失的基线提交 `dd48e69` 也纳入参考。
- 从当前干净的本地 `main@89606e5` 新建 `codex/multiagent-activity-rail`，保留该提交中已有的审批卡修复。
- 采用推荐方案：完整融合老版展示、持久化派发批次、技术视图只展示脱敏详情。
- 保留当前官方 `CopilotChat`、工具卡、RCA、HITL 和 320px 活动栏；不引入 assistant-ui，不使用 2.5 秒轮询和时间差推断轮次。

## 后端、数据库与事件契约

- 新增 `migrate-2026-07-14-subagent-activity.sql`，并同步 `openops_v1_core.sql`：
  - `sre_agent_delegation` 增加 `dispatch_batch_id uuid`、`dispatch_batch_no integer` 及字段注释。
  - 增加不超过 30 字符的批次查询索引。
  - 存量行不猜测历史轮次，允许批次字段为空并统一归入“历史派发”；新写入必须携带批次。
  - 迁移幂等、无损，可重复执行。
- 每次 `dispatch_subagents` 生成一个批次 ID 和任务内递增批次号；同批子任务共享批次，但各自保留独立 `delegation_id`、`child_task_id`。
- 所有子 Agent 生命周期、模型、工具、Skill、沙箱和审批事件统一携带：
  - `agent_key`、`agent_label`
  - `leader_task_id`、`child_task_id`
  - `delegation_id`
  - `dispatch_batch_id`、`dispatch_batch_no`
- 新增 `openops.subagent.cancelled`；账本仍是终态权威，UI 不依赖超时或错误文本推断状态。
- 持久化事件使用 `audit_event_id` 作为实时 envelope 的 `event_id`，使 AG-UI、备用 SSE 与审计恢复可直接去重；审计 payload 保存后端生成的脱敏 `summary`。
- 扩展接口：
  - `GET /agent-runs/{run_id}/state` 返回最新 100 条事件、当前任务 delegation 摘要及历史游标。
  - 新增 `GET /agent-runs/{run_id}/events?before=<cursor>&limit=<n>`，按游标向前分页，默认 100、最大 200，返回 `items/next_cursor/has_more`。
  - 修正当前“最早 100/200 条”查询问题，恢复接口始终返回最新事件并按时间正序展示。
- 模板 `content_json` 增加可选 `activity_labels.tools` 映射；业务文案解析顺序为完整工具名、MCP 内层工具名、目录展示名、后端兜底。角色名称继续使用 `sub_agents[].label`，不新增 `roles.yaml`。

## 前端重构

- 实现真正的 `OpenOpsCopilotRuntimeAdapter`/统一 reducer：
  - CopilotKit `useAgent().agent.subscribe(onCustomEvent)` 直接接收当前 AG-UI 流。
  - 被动 SSE 只负责其他客户端触发、重连和补发。
  - `/state`、分页事件、AG-UI CUSTOM、SSE 全部进入同一投影器，按事件 ID 去重、按时间/实时 sequence 排序。
  - 修复 CopilotChat 可发送但右栏尚未订阅造成的早期事件丢失窗口。
- 右栏增加“全部动态 / 子 Agent”双视图：
  - 没有派发时默认“全部动态”；首次派发自动切到“子 Agent”，用户手动切换后不再抢焦点。
  - 全部动态保留当前 Run、Scope、工具、Skill、模型、审批等活动，进行中置顶，并支持“显示更早”。
- 子 Agent 视图按 `dispatch_batch_id` 展示轮次：
  - 最新/运行中轮次默认展开，旧轮次折叠。
  - 轮次头显示批次号、角色、时间、已结束/总数和分色进度。
  - 同一角色重复派发按 delegation 分轨，不再合并成一个永久失败或永久运行的角色组。
  - 状态支持运行中、待审批、已汇报、异常、超时、已取消；混合终态显示“已结束，有异常”。
- 每轮提供：
  - 默认“业务”视图：展示派发、工具/Skill 里程碑、审批和最终汇报；隐藏 thinking/token 文本。
  - “技术”视图：星型编排图 + 每个 delegation 的事件轨迹；展示事件类型、脱敏参数/结果摘要、`reason_code`、request/execution ID。
  - 点击拓扑节点滚动到对应轨迹；业务/技术偏好保存在本地。
- 复用当前 design token、Tabler/Icon 体系和 320px 三栏布局；窄屏由现有开关打开覆盖式活动栏，不挤压主对话。
- 继续保留 CopilotChat 标准工具卡；活动栏展示的是同一调用的运行摘要，不改变聊天、审批或业务执行行为。

## 验证与发布

- 后端测试覆盖：批次递增、同角色并发、全链事件关联字段、取消/超时/审批路由、事件 ID 一致、最新事件查询、分页授权与幂等迁移。
- 前端使用 Node 内置测试 + `tsx` 测 reducer，不新增测试框架；覆盖乱序/重复事件、双通道去重、批次分组、五类终态、存量空批次及历史分页。
- Playwright 覆盖轮次折叠、业务/技术切换、拓扑跳转、同角色多 delegation、状态徽标、“显示更早”和窄屏活动栏。
- 安全回归断言 Secret、token、Authorization、Cookie、完整 prompt、完整参数/响应不进入接口或 DOM。
- 运行后端定向与完整测试、前端 unit/build/e2e，并做一次真实 CopilotKit + AG-UI 多 Agent/恢复审批联调。
- 存量发布顺序：对象名迁移（已执行则无操作）→ 新批次迁移 → core.sql → 后端 → frontend/sidecar；新库只执行 core.sql。
- 同步仓库内 API、编排手册、内网部署和发布检查表；同步 Obsidian 的 19、30.3、30.4、31、35 等权威页，保留现有 frontmatter 与 wikilink。
- 本计划默认只创建功能分支并实施，不自动提交、合并或推送 GitHub。

## 实施结果（2026-07-14）

- Claude Code 评审提出的事件游标、汇报摘要、实时订阅退路、`event_id` 统一及脱敏边界均已落地；后续代码复审发现的旧账本状态、Run 切换串流、审批恢复与历史事件泄漏风险也已修复。
- 数据库批次字段与幂等迁移、后端事件契约、统一前端投影器、双视图活动栏、历史恢复、窄屏交互及模板活动标签已经实现。
- 自动验证通过：后端 `257 passed, 12 skipped`，前端活动投影测试 `11/11`，生产构建通过，Playwright `16/16`；迁移脚本已覆盖无表、重复执行、存量数据保留和索引唯一性场景。
- 仓库内 API、编排、部署和发布检查文档以及 Obsidian 权威页已经同步；Obsidian 内容保留原有 frontmatter 与 wikilink。
- 本地无法替代公司内网的真实 GLM、CopilotKit、AG-UI、审批恢复和 sidecar 环境，因此该项保留为发布前人工验收，不将模拟测试结果冒充真链路验证。

## 默认决策与边界

- 右栏采用“完整融合”，而不是仅增强现有分组或完全替换全局时间线。
- 轮次采用数据库账本、事件和状态接口共同持久化的批次标识，不按时间间隔推断。
- 技术视图只展示后端脱敏、截断后的参数和结果摘要；不展示原始 prompt、完整工具参数、完整响应或推理文本。
- delegation 的 `running/completed/failed_no_report/timeout/cancelled` 为事实状态；前端只负责投影，不通过自然语言或静默时长推断。
- 普通用户仍面对一个 AgentTeam；子 Agent 仅作为运行活动身份展示，不开放给普通用户编辑。

---

## Claude Code 评审校正与实施锚点（2026-07-14）

> 结论：**核心方案全部认同**（批次 ID 持久化取代时间猜轮次、统一投影器、业务/技术双视图、
> 脱敏边界、账本为终态权威）。以下是核实过的精确锚点与三处校正，GPT 可据此直接实施。

### 校正 1 — 「最早 N 条」bug 精确定位（GPT 判对，已核实）
`backend/src/infra/repositories/audit.py:47` `list_by_run` = `order by occurred_at **asc** limit 200`
= 取**最早** 200 条；长会话最新活动（用户最关心的当前进展）被截断。修：改「`desc limit N`
取最新 → 应用层反转为正序返回」，或直接支撑游标分页接口。`list_recent`(:53)/`list_by_trace`(:60)
不受影响不用动。

### 校正 2 — 汇报正文缺口（GPT 计划漏提，必须补，否则业务视图汇报节点空）
`backend/src/runtime/subagent_dispatch.py` 的 `openops.subagent.reported` 当前只发
`payload={"report_chars": len(report)}`，**正文在 `return report`** 进主 agent 工具结果、不进事件。
业务视图「汇报」节点要显摘要 → 该 emit 的 payload 增 `report_summary`（后端脱敏+截断，
复用 `agentscope_runtime._redact` 口径挡 sk-/Bearer；长度另配上限如 300）。

### 校正 3 — useAgent.subscribe 承重假设待运行时验证 + 退路（不 patch 不确定 API）
`useAgent`（`@copilotkit/react-core/v2`）确在用（`CopilotAutoSend.tsx:10,23`），但现码只用其
`addMessage/runAgent`，**GPT 依赖的 `agent.subscribe(CUSTOM)` 订阅 API 存在性未证**。
实施**首步**：在 CopilotAutoSend 同款 `useAgent({agentId})` 上试 `agent.subscribe`/`agent.on`（查
`node_modules/@copilotkit/react-core/dist/*.d.ts` 的 AbstractAgent 导出）。有→用它接 CUSTOM 补
早窗；**无→退回：run 起始即经 SSE 订阅**（`subscribeSse` 在 `send()` 前挂），早窗用「订阅先于
起 task」消除，不引入未证 API。

### event_id 统一 — 精确改法（已核实，改动仅 3 处，风险低）
现状：`emit.py:40` `await audit.insert_event(...)` 的返回值**被丢弃**，但 `insert_event`
（`audit.py`）**本就 `eid = str(uuid4()); ...; return eid`**（签名 `-> str`）；而
`events.envelope`（`events.py:33-47`）里 `"event_id": str(uuid.uuid4())` 硬编码、无入参。
改法：
1. `events.envelope` 加可选参 `event_id: str | None = None`，`"event_id": event_id or str(uuid.uuid4())`。
2. `emit.py:40` 改 `eid = await audit.insert_event(...)`（接住返回值）。
3. `emit.py:46` 改 `events.publish(st.run_id, events.envelope(..., event_id=eid))`。
效果：实时 SSE 事件 `event_id` == 对应审计行 `audit_event_id` → AG-UI/SSE/审计三路同 id，
`Workbench` 的 `seen` 去重集与 refresh 的 `auditToNode` id 天然一致，断线补发不重复
（33 号铁律「断线恢复不重复活动线事件」）。`expire_stale_approvals_and_audit`(:71) 里
approval.timeout 那条独立 insert+publish 也同法接 eid 传 envelope。

### 批次 ID 注入 — 精确锚点
- 表：`sre_agent_delegation`（`backend/sql/openops_v1_core.sql:1101`）现有列
  delegation_id/run_id/leader_task_id/agent_key/task_text/delegation_status，无 batch 列。
  加 `dispatch_batch_id uuid`、`dispatch_batch_no int`（列名≤30 ✅）；新索引名≤30
  （如 `ix_delegation_batch`；现有 `ix_delegation_leader` 在 :1117）。
- 生成点：`subagent_dispatch.py:dispatch()`（:185）入口生成一个 batch_id + task 内递增 batch_no
  （整批共享）；循环里 `delegations.create(...)`（:213，每 role 一次）加 batch 参数落库，
  各行仍独立 delegation_id/child_task_id。
- emit 单点（`emit.py:39` 现只注 agent_key）扩注 agent_label/leader_task_id/child_task_id/
  delegation_id/dispatch_batch_id/dispatch_batch_no——但这些多来自子 TaskState/账本，emit 拿不到
  全部：建议派发链在 child `TaskState` 上挂这些字段（task_registry 加列），emit 从 st 读注。

### DDL / GaussDB 规范（硬约束，勿踩）
- 表 `sre_` 前缀；显式表名/索引名 ≤30 字符（门禁 test_ddl_005 查显式名，但内联 pkey 自动名不查——
  batch 只加列不加约束，安全）；迁移 `IF NOT EXISTS`/`ADD COLUMN IF NOT EXISTS` 幂等无损可重跑；
  避免 `ON CONFLICT` 偏索引 target（GaussDB 兼容坑，用 insert…select where not exists）；
  保留字规避（batch 相关无保留字）。migrate 脚本走 07-14 既有 `existing/new` 两步式部署口径。

### 分阶段与纪律
- **强烈建议按阶段分批提交**（A 后端契约自洽先落先验 → B 前端投影器 → C 渲染 → D 验证），
  不要一次大爆炸 PR。首个可提单元 = 阶段 A。
- 并行会话高发：`events.py`/`emit.py`/`projection.ts`/`Workbench.tsx` 是热点文件，提交前
  `git branch --show-current` + `git status --short` 核对，混改按 hunk 拆；双推
  `feat/workbench-frontend` + `main`，trailer `Co-Authored-By: Claude Fable 5 …`。
- e2e 保留断言字符串 `活动 · 调查时间线`（smoke.spec.ts 三处），或三处同步改。
- 用户决策已定：**保留星型编排图**（viewBox 0 0 320 恰合栏宽）+ **复刻业务|技术双视图切换**
  （localStorage `openops.workerView`）。图标从老项目 lucide 换成本项目 Tabler webfont，
  色板对齐本项目 design token（五色语义不变）。
