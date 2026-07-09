---
title: B2 Model Gateway + GLM Smoke Test Result
date: 2026-07-09
tester: Codex
branch: main
commit: a352cba
---

# B2 Model Gateway + GLM Smoke Test Result

## 结论

B2 在当前 `main@a352cba` 上基础回归通过，AgentScope 2.0.3 runtime 的 no-key fallback、错误 Key 受控失败、mock parity、SSE 补发链路均可验证。

真实 GLM 变体因测试环境未配置 `OPENOPS_PLATFORM_GLM_API_KEY`，按计划标记为 skipped。本报告未写入、未打印、未落库任何真实 API Key。

## 测试环境

| 项 | 结果 |
|---|---|
| 测试 worktree | `/tmp/openops-b2-main-test` |
| 分支 | `main` |
| 测试提交 | `a352cba docs: 修正冒烟手册（据 GPT 冒烟反馈）` |
| B2 运行时代码提交 | `ef14bdd Merge B2 into main: Model Gateway + GLM（stub↔GLM 可切换 + model.call.* 事件 + 脱敏）` |
| Python | 3.11 venv |
| AgentScope | 2.0.3 |
| PostgreSQL | 复用已有 `openops-v1-pg`，`localhost:5432`，healthy |
| 后端端口 | 18081 |

备注：隔离 worktree 的 `docker compose up -d` 因 `container_name: openops-v1-pg` 与已有容器冲突未单独启动；本次复用已健康运行的 PG。该冲突属于本地 compose 命名环境问题，不影响 B2 功能判断。

## 基础回归

| 项 | 命令 | 结果 |
|---|---|---|
| 后端测试 | `cd backend && OPENOPS_DATABASE_URL=postgresql://openops:openops@localhost:5432/openops .venv/bin/pytest -q` | PASS，`25 passed, 1 warning` |
| 前端构建 | `cd frontend && npm run build` | PASS |
| DDL 静态检查 | `rg "FOREIGN KEY|REFERENCES|CREATE TRIGGER|CREATE FUNCTION" backend/sql/openops_v1_core.sql` | PASS，无命中 |
| 表数量 | `rg -in "^CREATE TABLE IF NOT EXISTS" backend/sql/openops_v1_core.sql` | 20 张，包含 19 张核心表 + `platform_runtime_config` |
| npm audit | `npm install` 输出 | 2 个依赖审计提示：1 moderate、1 high；本轮未升级依赖 |

## no-key fallback（AgentScope runtime）

启动方式：

```bash
env -u OPENOPS_PLATFORM_GLM_API_KEY \
OPENOPS_DATABASE_URL=postgresql://openops:openops@localhost:5432/openops \
OPENOPS_RUNTIME=agentscope \
OPENOPS_ORCH_DELAY_MS=200 \
.venv/bin/uvicorn main:app --app-dir src --host 0.0.0.0 --port 18081
```

API 闭环：`/me -> /templates/available -> /agent-teams -> /agent-runs -> /tasks -> /approvals -> approve -> /state -> /audit/runs/{run_id}`。

结果：

| 项 | 结果 |
|---|---|
| 用户 | `0026demo01`，`whitelisted=true`，`role=user` |
| 平台模型 | `glm-5.1` active，且只暴露 `secret_env_var` 元数据，不暴露 Key |
| Run | `d0a098b7-fd2c-4339-b465-f0df043665d6` |
| Task | `tsk_685975b2be` |
| 审批 tool | `recover_execute` |
| 审批决策 | approved |
| Task 状态 | completed |
| RCA revision | 3 |
| RCA conclusion 摘要 | `已确认根因 H1（Redis 连接泄漏）：重启 svc-a 后连接回落、P99 恢复 210ms，事件闭环。` |
| model payload | `stub-rca` |
| token usage | fallback stub 返回 `input_tokens=0 / output_tokens=0` |
| recover_execute | 有外部 `request_id`，恢复执行事件存在 |

审计事件序列：

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
openops.tool.call.succeeded
openops.rca.updated
openops.model.call.started
openops.model.call.succeeded
openops.rca.updated
openops.task.completed
```

结论：PASS。无 Key 时按 B2 设计回退 stub，同时产出 `openops.model.call.started/succeeded` 事件。

## 拒绝路径（AgentScope no-key fallback）

结果：

| 项 | 结果 |
|---|---|
| Run | `27a9b8ce-327d-4182-a5cf-7c5f984da333` |
| Task | `tsk_3500c846d7` |
| 审批决策 | rejected |
| Task 状态 | completed |
| RCA revision | 2 |
| recover_execute | 未出现带 `external_request_id` 的恢复执行事件 |

审计事件包含 `approval.rejected`，且未调用恢复执行 tool。

发现问题：

- `B2-RUNTIME-001`：拒绝路径中没有执行恢复工具，但最后一次 `openops.rca.updated` 的 conclusion 被 stub 模型最终文本覆盖为“已确认根因 H1...重启 svc-a 后...事件闭环”。这会让前端/审计回放看起来像已经恢复成功。建议在 rejected / timeout / cancelled 分支后不要继续采纳模型最终文本覆盖 RCA，或为拒绝路径生成明确的“未执行恢复”最终回答。

## wrong-key failure（AgentScope runtime）

启动方式：

```bash
OPENOPS_DATABASE_URL=postgresql://openops:openops@localhost:5432/openops \
OPENOPS_RUNTIME=agentscope \
OPENOPS_ORCH_DELAY_MS=200 \
OPENOPS_PLATFORM_GLM_API_KEY=invalid_test_key \
.venv/bin/uvicorn main:app --app-dir src --host 0.0.0.0 --port 18081
```

结果：

| 项 | 结果 |
|---|---|
| Run | `41131dfb-9050-4b07-b350-8f620ad442ed` |
| Task | `tsk_a54343d61b` |
| Task 状态 | failed |
| 事件链 | `agent_run.created -> task.started -> scope.resolved -> openops.model.call.started -> openops.model.call.failed -> openops.task.failed` |
| failure payload | `Error code: 401 - {'error': {'code': '401', 'message': '令牌已过期或验证不正确'}}` |
| state + audit 敏感词扫描 | 未发现 `invalid_test_key`、`Authorization`、`Bearer`、`Cookie`、`API Key` |

结论：PASS。错误 Key 进入受控失败态，SSE / state / audit 未泄漏凭证。

补充观察：后端控制台会打印 AgentScope/OpenAI 调用栈，抽样日志中未出现 Key 或 Authorization，但生产态建议将这类模型失败降为结构化错误日志，减少堆栈噪声。

## real GLM

状态：skipped。

原因：测试环境未配置 `OPENOPS_PLATFORM_GLM_API_KEY`。按测试计划，不从对话记录或文件读取 Key，不向报告写入 Key。

## mock parity

启动方式：

```bash
OPENOPS_DATABASE_URL=postgresql://openops:openops@localhost:5432/openops \
OPENOPS_RUNTIME=mock \
OPENOPS_ORCH_DELAY_MS=100 \
.venv/bin/uvicorn main:app --app-dir src --host 0.0.0.0 --port 18081
```

结果：

| 项 | 结果 |
|---|---|
| Run | `773a8942-1174-428d-ab16-78c1a68dba92` |
| Task 状态 | completed |
| RCA revision | 3 |
| recover_execute | 有外部 `request_id` |
| model.call 事件 | 无，符合 mock runtime 预期 |

mock 审计事件序列：

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
openops.tool.call.succeeded
openops.rca.updated
openops.task.completed
```

结论：PASS。mock 与 agentscope no-key fallback 的核心生命周期同形；差异是 agentscope runtime 额外插入 B2 的 `openops.model.call.started/succeeded` 事件。

## SSE / state 恢复

验证：

- 对已完成 Run 直接订阅 `/events/stream` 且不传 `Last-Event-ID`，不会回放历史事件，符合当前“实时流 + `/state` 恢复”口径。
- 传 `Last-Event-ID: 10` 可补发序号 11 之后的事件，事件 `id` 为 sequence。

结论：PASS。Last-Event-ID 补发可用。

## 文档校验

`main@a352cba` 已修复 B1 手册中 GPT 冒烟反馈的两处问题：

- 创建实例 jq 路径已改为 `.data.instance.instance_id`。
- approval 事件名前缀已明确区分：审计中的决策事件是 `approval.approved/rejected`，运行活动事件保留 `openops.*`。

仍建议修正：

- `backend/sql/openops_v1_core.sql` 中 `platform_runtime_config.config_domain` 的注释仍写着“V1 使用 sandbox”，但 B2 已同时使用 `platform_model` domain。建议改为“V1 使用 sandbox / platform_model”。

## 发现问题汇总

| ID | 严重度 | 问题 | 建议 |
|---|---|---|---|
| B2-RUNTIME-001 | P1 | 拒绝恢复后，最终 RCA conclusion 被 stub 文本覆盖成“已恢复”语义 | rejected / timeout / cancelled 分支不要使用模型最终文本覆盖 RCA，或生成明确“恢复未执行”的最终文本 |
| B2-DOC-001 | P3 | `platform_runtime_config.config_domain` 注释仍只写 sandbox | 注释补充 `platform_model` |
| B2-OPS-001 | P3 | 本地 compose 使用固定 `container_name`，隔离 worktree 启 compose 会冲突 | 后续可移除固定 container_name，或测试文档注明复用已有 PG |
| B2-LOG-001 | P3 | wrong-key 模型失败时控制台有完整调用栈，虽未泄漏 Key | 生产态建议结构化记录 provider/status/message，debug 才打堆栈 |

