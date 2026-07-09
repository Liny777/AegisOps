---
title: B4 Tool Gateway + HTTP MCP Smoke Test Result
date: 2026-07-09
tester: Codex
branch: origin/main
commit: e7ab3df
---

# B4 Tool Gateway + HTTP MCP Smoke Test Result

## 结论

B4 在当前 `origin/main@e7ab3df` 上测试通过：平台 HTTP MCP 调用链的标注校验、Scope / APPID 校验、ASK 后调用、Secret 顺序、平台 header 注入、用户 MCP header 隔离、审计和 B1/B2/B3 主链路兼容均可验证。

本次测试对象不是单独的 B4 提交，而是执行时最新 `origin/main@e7ab3df`。该提交包含：

- `0cd9898 feat(runtime): B4 Tool Gateway — 平台 HTTP MCP 受控调用链（标注/Scope/Secret/header/审计）`
- `e7ab3df fix(fe): 补齐活动线事件投影（据 GPT B3 报告 B3-OBS-001）`

未发现阻断 B4 合入 / 演示的 P0/P1 问题。

## 测试环境

| 项 | 结果 |
|---|---|
| 测试 worktree | `/tmp/openops-b4-main-test` |
| 分支 | detached `origin/main` |
| 测试提交 | `e7ab3df` |
| B4 提交 | `0cd9898` |
| Python | 3.11 venv |
| AgentScope | 2.0.3 |
| PostgreSQL | 复用已有 `openops-v1-pg`，`localhost:5432`，healthy |
| 后端端口 | 18081 |

说明：沿用 B2/B3 测试策略，没有在隔离 worktree 内重新执行 `docker compose up -d`，避免固定 `container_name: openops-v1-pg` 造成容器名冲突。

## 基础回归

| 项 | 命令 | 结果 |
|---|---|---|
| 后端测试 | `cd backend && OPENOPS_DATABASE_URL=postgresql://openops:openops@localhost:5432/openops .venv/bin/pytest -q` | PASS，`38 passed, 1 warning` |
| AgentScope 版本 | `.venv/bin/python -c "import agentscope; print(agentscope.__version__)"` | `2.0.3` |
| 前端构建 | `cd frontend && npm ci && npm run build` | PASS |
| DDL 禁止词 | `rg "FOREIGN KEY|REFERENCES|CREATE TRIGGER|CREATE FUNCTION" backend/sql/openops_v1_core.sql` | PASS，无命中 |
| 旧口径静态检查 | `rg "scope_id|user_entitlement_cache|risk_level|resource_type|operation_type|/agui/run|自动审批低风险|riskLevel" backend frontend docs` | PASS，仅命中测试中的“不得出现”断言 |
| 敏感信息静态检查 | 搜索真实 GLM Key、Bearer、Cookie 等 | PASS，未命中真实密钥；仅命中测试假 token 和文档占位符 |
| npm audit | `npm ci` 输出 | 2 个依赖审计提示：1 moderate、1 high；本轮未升级依赖 |

## B4 单测覆盖

新增 / 更新的 `backend/tests/test_tool_gateway.py` 7 条用例均随 `pytest` 通过：

| 用例 | 覆盖点 | 结果 |
|---|---|---|
| `test_tool_001_platform_headers_injected` | 平台 MCP 注入 `X-OpenOps-*`、`effective_appids`、`scope_snapshot_id` | PASS |
| `test_tool_002_blocked_annotation_direct_fail_closed` | 管理员标注 `status=blocked` 后不 ASK、直接 fail-closed | PASS |
| `test_tool_003_appid_out_of_scope_fail_closed` | `scope_mode=required` 且 APPID 越界返回 `APPID_OUT_OF_SCOPE` | PASS |
| `test_tool_004_unannotated_fail_closed` | 未标注平台工具返回 `TOOL_NOT_ANNOTATED` | PASS |
| `test_tool_005_user_mcp_no_platform_headers` | 用户 MCP 不携带 Cookie、Authorization、`X-OpenOps-*` | PASS |
| `test_tool_006_secret_required_order_and_no_leak` | Secret 缺失先阻断；有 Secret 时只在调用边界注入且不进事件 | PASS |
| `test_tool_007_scope_optional_mode` | `scope_mode=optional` 不传 APPID 放行，传越界 APPID 阻断 | PASS |

## API 烟测：mock runtime + B4 Tool Gateway

启动方式：

```bash
OPENOPS_DATABASE_URL=postgresql://openops:openops@localhost:5432/openops \
OPENOPS_RUNTIME=mock \
OPENOPS_OMODEL=mock \
OPENOPS_ORCH_DELAY_MS=20 \
.venv/bin/uvicorn main:app --app-dir src --host 127.0.0.1 --port 18081
```

链路一：`/me -> /templates/available -> /agent-teams -> /agent-runs -> /tasks -> /approvals -> approve -> /state -> /audit/runs/{run_id}`。

结果：

| 项 | 结果 |
|---|---|
| Run | `81b8f122-f145-4a89-af50-fff6c8312d91` |
| Task | `tsk_7cad81d0e6` |
| Task 状态 | completed |
| ASK 前恢复 MCP 调用 | PASS，无 `recover_execute` 外部 `request_id` |
| ASK 批准后恢复 MCP 调用 | PASS，有 `recover_execute` 外部 `request_id` |
| 审计链 | PASS，包含 `openops.approval.required`、`approval.approved`、`openops.tool.call.started/succeeded`、`openops.task.completed` |

事件序列：

```text
agent_run.created
task.started
scope.resolved
openops.tool.call.started
openops.tool.call.succeeded
openops.rca.updated
openops.tool.call.started
openops.tool.call.succeeded
openops.rca.updated
openops.approval.required
approval.approved
openops.tool.call.started
openops.tool.call.succeeded
openops.rca.updated
openops.task.completed
```

链路二：管理员将 `recover_execute` 标注为 `blocked` 后启动任务。

结果：

| 项 | 结果 |
|---|---|
| Run | `ba6a715a-48d8-4734-95b8-c231fb949401` |
| Task 状态 | failed |
| 阻断事件 | PASS，包含 `openops.tool.blocked`，`reason_code=TOOL_BLOCKED` |
| ASK | PASS，不产生 `openops.approval.required` |
| MCP 调用 | PASS，无 `recover_execute` 外部 `request_id` |
| 标注恢复 | PASS，烟测结束后已恢复 `recover_execute` 为 `allowed` |

事件序列：

```text
agent_run.created
task.started
scope.resolved
openops.tool.call.started
openops.tool.call.succeeded
openops.rca.updated
openops.tool.call.started
openops.tool.call.succeeded
openops.rca.updated
openops.tool.blocked
openops.task.failed
```

## API 烟测：agentscope runtime 兼容

启动方式：

```bash
OPENOPS_DATABASE_URL=postgresql://openops:openops@localhost:5432/openops \
OPENOPS_RUNTIME=agentscope \
OPENOPS_OMODEL=mock \
OPENOPS_ORCH_DELAY_MS=20 \
.venv/bin/uvicorn main:app --app-dir src --host 127.0.0.1 --port 18081
```

链路：同批准路径。

结果：

| 项 | 结果 |
|---|---|
| Run | `d26248c0-640b-406b-981f-5c64002625df` |
| Task | `tsk_4c51824723` |
| Task 状态 | completed |
| AgentScope runtime | PASS，事件链包含 `openops.model.call.*` |
| Tool Gateway | PASS，事件链包含 `openops.tool.call.started/succeeded` |
| ASK 顺序 | PASS，批准前无恢复 MCP 外部请求，批准后执行 `recover_execute` |
| 审计链 | PASS，包含 `approval.approved`、恢复 `external_request_id`、`openops.task.completed` |

事件序列：

```text
agent_run.created
task.started
scope.resolved
openops.model.call.started
openops.model.call.succeeded
openops.tool.call.started
openops.tool.call.succeeded
openops.rca.updated
openops.model.call.started
openops.model.call.succeeded
openops.tool.call.started
openops.tool.call.succeeded
openops.rca.updated
openops.model.call.started
openops.model.call.succeeded
openops.approval.required
approval.approved
openops.tool.call.started
openops.tool.call.succeeded
openops.rca.updated
openops.model.call.started
openops.model.call.succeeded
openops.rca.updated
openops.task.completed
```

## 静态口径检查

| 检查项 | 结果 |
|---|---|
| V1 MCP only HTTP | PASS，当前 B4 只实现 HTTP MCP mock client |
| 平台 MCP 标注是运行时事实 | PASS，`start_task` 读取 `mcp_tool_annotation` 后挂入 `TaskState.tool_annotations`，Tool Gateway 调用前二次校验 |
| 用户 MCP 不进平台标注体系 | PASS，`source_type=user` 分支不检查平台 annotation，也不注入平台 header |
| 恢复能力不是独立 Recovery 平台 | PASS，`recover_execute` 是普通平台 MCP tool |
| Scope 仍来自 B3 Scope Service | PASS，Tool Gateway 只消费 `TaskState.scope_ctx`，不自建 workspace → APPID 映射 |
| Secret 明文不进事件 / 审计 | PASS，测试假 token 只在 `http_mcp_client.last_call.headers.Authorization` 中出现，事件捕获中未出现 |

## 发现问题与建议

| ID | 严重度 | 问题 | 建议 |
|---|---|---|---|
| B4-OBS-001 | P3 | `GET /api/openops/v1/me` 当前返回 `data.user_id / whitelisted` 平铺结构，而部分设计草稿或测试脚本容易按 `data.user.user_id` 读取 | 以当前实现为准同步 API 文档或前端 facade，避免后续联调误读 |
| B4-OBS-002 | P3 | FastAPI 访问日志中 `:decide` 会显示为 `%3Adecide`，但实际接口返回 200，不影响功能 | 无需立即修复；若日志检索依赖路径文本，可在运维文档中说明 URL 编码表现 |
| B4-DEP-001 | P3 | `npm ci` 仍提示 2 个依赖审计项 | 后续单独评估前端依赖升级，不建议混入 B4 |
| B4-DOC-001 | P2 | `docs/ROADMAP.md` 仍使用较旧编号，把 Tool Gateway 写成 B2、资产对账写成 B4，与 Obsidian 33 号 B1-B9 当前计划不一致 | 建议后续文档对齐，避免 Claude Code / Codex 按不同编号沟通 |

## 总体判断

B4 的功能闭环是成立的：

- 平台 MCP 未标注 / blocked / APPID 越界均 fail-closed。
- 恢复类平台 MCP tool 仍是普通 MCP tool，由 `is_approval_required` 决定 ASK。
- ASK 拒绝或未批准前不会调用恢复 MCP。
- 用户 MCP 不携带平台 Cookie、Authorization 或 `X-OpenOps-*`。
- mock runtime 与 agentscope runtime 均能通过 Tool Gateway 完成工具调用链。

可以继续推进下一块：CopilotKit + AG-UI 工作台接管，或先按 B4-DOC-001 对 repo `docs/ROADMAP.md` 做编号对齐。
