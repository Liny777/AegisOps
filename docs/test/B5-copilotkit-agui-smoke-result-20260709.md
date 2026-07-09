---
title: B5 CopilotKit + AG-UI 工作台接管测试报告
date: 2026-07-09
tester: Codex
branch: origin/main
commit: 3e75fb5
---

# B5 CopilotKit + AG-UI 工作台接管测试报告

## 结论

B5 主体验证通过。`origin/main@3e75fb5` 中的 B5 提交 `fcef0f8 feat(agui): B5 CopilotKit/AG-UI 工作台接管` 已具备后端 AG-UI 事件流、前端 `@ag-ui/client HttpAgent` headless 接入、OpenOps 自定义事件投影、ASK/HITL、SSE fallback、`GET /state` 恢复和 B1-B4 主链路兼容能力。

本轮没有发现阻断 B5 演示的 P0/P1 问题。记录 5 个观察项，主要是产品表述、前端包体、依赖漏洞、AgentScope ASK 中断后的清理鲁棒性和审计查询体验。

## 测试对象与环境

| 项目 | 结果 |
|---|---|
| 测试 worktree | `/tmp/openops-b5-main-test` |
| 测试分支 | `origin/main` detached |
| 测试 commit | `3e75fb5` |
| B5 提交 | `fcef0f8` |
| 后端 Python | Python 3.11 venv |
| AgentScope | 2.0.3 |
| PostgreSQL | 复用本机 `openops-v1-pg`，`localhost:5432`，healthy |
| 后端测试端口 | `18082` |
| 运行态 | `OPENOPS_RUNTIME=mock`、`OPENOPS_RUNTIME=agentscope` 均验证 |
| oModel | `OPENOPS_OMODEL=mock` |

未写入、未打印、未保存任何真实 API Key、Authorization、Bearer token、Cookie 或完整 prompt/messages。

## 基础回归

| 检查项 | 结果 | 备注 |
|---|---:|---|
| 后端单测 | 通过 | `43 passed, 1 warning in 6.30s` |
| `backend/tests/test_agui.py` | 通过 | 覆盖 AG-UI 基础协议、owner、closed、validation 等路径 |
| 前端构建 | 通过 | `npm ci && npm run build` |
| DDL 静态检查 | 通过 | 未发现 `FOREIGN KEY`、`REFERENCES`、`CREATE TRIGGER`、`CREATE FUNCTION` |
| 旧口径静态检查 | 通过 | 未发现可用入口 `/agui/run`、`riskLevel`、`自动审批低风险`、Knowledge 实功能、对象图、`@ 提及` |

前端构建提示：

- `npm ci` 后报告 2 个依赖漏洞：1 moderate、1 high。
- Vite 提示主 chunk 超过 500 KB：`index-B3cZ8oET.js 531.83 kB`，后续可考虑 code splitting。

## 后端 AG-UI 协议验证

### Mock Runtime: 无 ASK 路径

运行配置：`OPENOPS_RUNTIME=mock OPENOPS_OMODEL=mock OPENOPS_ORCH_DELAY_MS=20`

结果：

- `POST /api/openops/v1/agent-runs/{run_id}/agui` 返回 `text/event-stream`。
- 事件流包含 `RUN_STARTED` 与 `RUN_FINISHED`。
- 标准事件包含 `TEXT_MESSAGE_START`、`TEXT_MESSAGE_CONTENT`、`TEXT_MESSAGE_END`。
- 工具事件包含 `TOOL_CALL_START`、`TOOL_CALL_END`、`TOOL_CALL_RESULT`，且 `toolCallId` 成对出现。
- OpenOps 自定义事件通过 `CUSTOM(name=openops.*)` 透传。
- 关键自定义事件顺序：
  - `openops.task.started`
  - `openops.scope.resolved`
  - `openops.tool.call.started`
  - `openops.tool.call.succeeded`
  - `openops.rca.updated`
  - `openops.task.completed`
- `GET /state` 显示 active task `completed`。

样例 run：

- `855e4f7f-469c-4d57-9d13-40fa3b2857d8`

### Mock Runtime: ASK 批准路径

样例 run：

- `99bf0477-e6b2-4fc2-8d5f-b975ea51a720`

结果：

- 收到 `CUSTOM openops.approval.required`。
- 通过 `POST /approvals/{approval_id}:decide` 批准后，同一 AG-UI stream 继续推进。
- 事件流包含 `openops.approval.approved` 与 `openops.task.completed`。
- 审计中存在 `approval.approved`。
- 批准前未出现恢复执行外部 request；批准后出现恢复执行外部 request。

### Mock Runtime: ASK 拒绝路径

样例 run：

- `86dfc6f8-87f9-4eb3-8273-30b18b78fbc8`

结果：

- 收到 `CUSTOM openops.approval.required`。
- 拒绝后事件流包含 `openops.approval.rejected`。
- 未调用恢复 MCP，不存在 `recover_execute` 的外部 request。
- AG-UI stream 受控收口到 `RUN_FINISHED`，task 状态为 completed。

### Mock Runtime: Blocked 标注路径

样例 run：

- `bf33ba06-3fd7-4313-acce-4551876a6899`

结果：

- 管理员将 `recover_execute` 标注为 blocked 后，AG-UI stream 返回 `RUN_ERROR`。
- 自定义事件包含 `openops.tool.blocked`、`openops.task.failed`。
- 审计中 `openops.tool.blocked.reason_code = TOOL_BLOCKED`。
- 未出现 `openops.approval.required`。

### Guard / 错误路径

| 场景 | 结果 |
|---|---:|
| 他人访问 `/agui` | 403 |
| closed run 启动 AG-UI task | 409 |
| 空 messages / 无用户文本 | 400 |

## SSE Fallback 验证

运行配置：`OPENOPS_RUNTIME=mock`

样例 run：

- `98131448-1bba-4bb1-aa8c-0da177b6c631`

结果：

- `GET /events/stream` 可订阅。
- `POST /tasks` 后 SSE 推进到 `openops.task.completed`。
- `GET /state` 返回 `last_event_seq = 12`，active task `completed`。
- 与 AG-UI mock 路径生命周期同形，可作为 B5 演示兜底通道。

## AgentScope Runtime 兼容验证

运行配置：`OPENOPS_RUNTIME=agentscope OPENOPS_OMODEL=mock`，未设置真实 GLM Key，走 no-key fallback。

样例 run：

- `e7877687-d104-45be-a33b-fae0b5993640`

结果：

- AG-UI stream 启动成功并返回 `RUN_STARTED`。
- 事件流包含 `openops.scope.updated`、`openops.task.started`、`openops.scope.resolved`。
- 多轮模型调用事件存在：
  - `openops.model.call.started`
  - `openops.model.call.succeeded`
- ASK 链路成功：
  - `openops.approval.required`
  - `POST /approvals/{approval_id}:decide` 返回 200
  - `openops.approval.approved`
- 平台工具链路成功：
  - `TOOL_CALL_START`
  - `TOOL_CALL_END`
  - `TOOL_CALL_RESULT`
  - `openops.tool.call.started`
  - `openops.tool.call.succeeded`
- AG-UI stream 终态为 `RUN_FINISHED`。
- `GET /state` 返回 active task `completed`。
- 审计中存在 `approval.approved`。
- 审计中存在恢复执行成功外部 request：`openops.tool.call.succeeded.external_request_id = req_995b0aea7b`，payload 包含 `execution_id = exec_5fdf0eaf`。

脱敏检查通过。以下字符串未出现在 AG-UI 事件与审计序列中：

- `Authorization`
- `Bearer `
- `Cookie`
- 已知 API Key 前缀
- `API Key`

## 前端 Workbench 集成检查

代码路径检查结果：

- `frontend/src/lib/runtime/agui.ts` 默认 `VITE_OPENOPS_TRANSPORT` 为 `agui`。
- AG-UI endpoint 为 `/api/openops/v1/agent-runs/{runId}/agui`。
- `frontend/src/workbench/Workbench.tsx` 在 `TRANSPORT === "agui"` 时调用 `runAguiTask(...)`。
- SSE fallback 仍保留：`subscribeSse('/api/openops/v1/agent-runs/{runId}/events/stream')`。
- `GET /state` 恢复路径存在：`api.getRunState(...)`。
- `aguiActive` 保护存在，用于避免 AG-UI 标准文本和 SSE `task.completed` 双写。
- ASK 卡仍调用 OpenOps REST 审批 API，不只改前端本地状态。

本轮没有引入浏览器 E2E 框架，因此前端行为以 `npm run build`、代码路径检查、HTTP/AG-UI/SSE 联调结果作为验证依据。

## B1-B4 兼容结果

| 兼容项 | 结果 |
|---|---:|
| B1 最小闭环 Run/Task/RCA/ASK | 通过 |
| B2 Model Gateway 事件 | 通过，AgentScope fallback 中出现 `model.call.*` |
| B3 Scope Service | 通过，`scope.resolved` / `scope.updated` 可进入 AG-UI 自定义事件 |
| B4 Tool Gateway | 通过，平台 MCP 标注 allowed / blocked / ASK 路径均受控 |
| SSE 旧通道 | 通过，作为 fallback 可用 |

## 发现的问题与建议

### B5-OBS-001 P3：B5 当前是 Headless HttpAgent + OpenOps 自定义 UI

当前前端已接入 `@ag-ui/client HttpAgent`，但页面仍是 OpenOps 自定义 Workbench UI，不是直接嵌入 `<CopilotChat>`。这与当前验收假设一致，但对外表述建议写清楚：B5 完成的是 CopilotKit/AG-UI 运行协议接管和 headless adapter 接入。

### B5-FE-001 P3：前端主 chunk 超过 500 KB

`npm run build` 通过，但 Vite 提示 `index-B3cZ8oET.js` 超过 500 KB。后续如果要上线，可考虑按管理台、工作台、AG-UI 依赖做路由级拆包或 `manualChunks`。

### B5-DEP-001 P3：前端依赖存在 2 个 npm audit 漏洞

`npm ci` 后显示 1 moderate、1 high。未阻断本轮 smoke，但上线前建议补一轮依赖审计。

### B5-BE-001 P2：AgentScope ASK 流被客户端中断后，shutdown 阶段可能出现 PoolClosed 日志

在测试脚本因缺少 `client_request_id` 导致审批 422、随后手工中断 AG-UI stream 的场景下，重启后端时出现后台 task 尝试写 `task.cancelled`，但 PG pool 已关闭，日志报 `psycopg_pool.PoolClosed`。

影响：

- 未影响正常 AG-UI ASK 批准路径。
- 未影响 mock runtime。
- 这是异常中断/测试脚本错误场景，建议后续让 shutdown 先 cancel runtime tasks 并等待审计收口，或在 pool close 后降级记录。

### B5-OBS-002 P3：恢复成功审计事件里 tool 名与 external_request_id 分布在相邻事件

AgentScope 成功路径中：

- `openops.tool.call.started` payload 记录 `tool = recover_execute`。
- 后续 `openops.tool.call.succeeded` 记录 `external_request_id` 与 `execution_id`，但 payload 不再带 tool 名。

这不影响链路回放，但后续为了审计查询体验，建议成功/失败事件也带上脱敏后的 `tool` 字段。

## 总体建议

B5 可以合入当前主线作为 AG-UI 工作台接管的 smoke 通过版本。下一步建议按计划继续推进 B6/B7 前，先处理两个低成本改善：

1. 成功/失败工具审计事件补充 `tool` 字段，方便审计回放查询。
2. AgentScope runtime shutdown 时对 pending ASK task 做更稳的取消收口，减少异常中断日志噪声。
