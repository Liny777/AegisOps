---
title: B3 Scope Service + oModel Adapter Smoke Test Result
date: 2026-07-09
tester: Codex
branch: origin/main
commit: a3a9380
---

# B3 Scope Service + oModel Adapter Smoke Test Result

## 结论

B3 在当前 `origin/main@a3a9380` 上测试通过：Scope Service 的 mock adapter、real adapter 未配置失败态、30 秒 TTL、fail-closed、scope revision 回写、审计事件和 B1/B2 主链路兼容均可验证。

本次测试对象不是原计划中的 `96d7702`，而是执行时最新 `origin/main@a3a9380`。该提交包含：

- `96d7702 feat(scope): B3 Scope Service + oModel 可切换 adapter（TTL/分态/revision 回写）`
- `a3a9380 fix(runtime): B2 冒烟反馈修复（据 GPT B2 测试报告）`

## 测试环境

| 项 | 结果 |
|---|---|
| 测试 worktree | `/tmp/openops-b3-main-test` |
| 分支 | detached `origin/main` |
| 测试提交 | `a3a9380` |
| B3 提交 | `96d7702` |
| Python | 3.11 venv |
| AgentScope | 2.0.3 |
| PostgreSQL | 复用已有 `openops-v1-pg`，`localhost:5432`，healthy |
| 后端端口 | 18081 |

说明：沿用 B2 测试策略，没有在隔离 worktree 内重新执行 `docker compose up -d`，避免固定 `container_name: openops-v1-pg` 造成容器名冲突。

## 基础回归

| 项 | 命令 | 结果 |
|---|---|---|
| 后端测试 | `cd backend && OPENOPS_DATABASE_URL=postgresql://openops:openops@localhost:5432/openops .venv/bin/pytest -q` | PASS，`31 passed, 1 warning` |
| AgentScope 版本 | `.venv/bin/python -c "import agentscope; print(agentscope.__version__)"` | `2.0.3` |
| 前端构建 | `cd frontend && npm run build` | PASS |
| DDL 禁止词 | `rg "FOREIGN KEY|REFERENCES|CREATE TRIGGER|CREATE FUNCTION" backend/sql/openops_v1_core.sql` | PASS，无命中 |
| 表数量 | `rg -in "^CREATE TABLE IF NOT EXISTS" backend/sql/openops_v1_core.sql` | 20 张，包含 19 张核心表 + `platform_runtime_config` |
| npm audit | `npm install` 输出 | 2 个依赖审计提示：1 moderate、1 high；本轮未升级依赖 |

## B3 Scope 单测覆盖

新增 `backend/tests/test_scope.py` 的 6 条用例均随 `pytest` 通过：

| 用例 | 结果 |
|---|---|
| ready workspace resolve | PASS |
| syncing workspace fail-closed | PASS |
| failed workspace fail-closed | PASS |
| empty effective_appids fail-closed | PASS |
| scope revision changed writeback | PASS |
| 30s TTL cache reuse | PASS |

## API 烟测：mock oModel + mock runtime

启动方式：

```bash
OPENOPS_DATABASE_URL=postgresql://openops:openops@localhost:5432/openops \
OPENOPS_OMODEL=mock \
OPENOPS_RUNTIME=mock \
OPENOPS_ORCH_DELAY_MS=100 \
.venv/bin/uvicorn main:app --app-dir src --host 0.0.0.0 --port 18081
```

链路：`/me -> /templates/available -> /agent-teams -> /agent-runs -> /tasks -> /approvals -> approve -> /state -> /audit/runs/{run_id}`。

结果：

| 项 | 结果 |
|---|---|
| Run | `b4d9af08-2866-4dac-8835-5b7733c862bc` |
| Task | `tsk_67f98b8742` |
| Task 状态 | completed |
| RCA revision | 3 |
| 初始 scope_revision | `rev-20260708-001` |
| scope.resolved | 有 |
| scope payload | `appid_count=3`，`scope_snapshot_id=a0f008ad-1fab-4359-9573-1231429477e6` |
| recover_execute | 有外部 `request_id` |

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
openops.tool.call.succeeded
openops.rca.updated
openops.task.completed
```

结论：PASS，B1 主链路未回归。

## Scope 分态与 TTL 脚本验证

使用同进程 `TestClient` 验证需要调用 `omodel_mock._set_scope(...)` 的场景。成功启动的 task 均在采集结果后立即 cancel，避免测试退出时遗留运行任务。

| 场景 | 结果 |
|---|---|
| ready | `200`，task running，审计包含 `task.started -> scope.resolved`，`appid_count=3` |
| syncing | `400 WORKSPACE_NOT_READY`，审计包含 `scope.blocked` |
| failed | `400 SCOPE_RESOLVE_FAILED`，审计包含 `scope.blocked` |
| empty | `400 EMPTY_SCOPE`，审计包含 `scope.blocked` |
| revision changed | `200`，审计包含 `scope.updated`，`/agent-teams/{id}` 返回 `scope_revision=rev-20260709-777` |
| TTL hit | 第二次 task 在 30s 内复用第一次的 `scope_snapshot_id=ac13fbb0-e531-4d8a-b019-cedb6f3399b0`，即使 mock oModel 随后被改为空范围也未重解 |

revision changed 场景的审计事件：

```text
agent_run.created
scope.updated
task.started
scope.resolved
openops.task.cancelled
```

说明：`scope.updated` 会早于 `task.started`，因为当前实现是在 `start_task` 中先 resolve，再写 task started。该顺序与 fail-closed 设计一致，但前端时间线如默认认为 task.started 永远是 task 相关第一事件，需要兼容该顺序。

## real adapter 未配置 base url

验证方式：

- 先用 `OPENOPS_OMODEL=mock` 创建实例和 Run。
- 切换到 `OPENOPS_OMODEL=real` 且不设置 `OPENOPS_OMODEL_BASE_URL`。
- 启动 task。
- 再尝试创建新实例。

结果：

| 场景 | 结果 |
|---|---|
| 已有实例启动 task | `400 SCOPE_RESOLVE_FAILED`，审计包含 `agent_run.created -> scope.blocked` |
| real adapter 创建实例 | `404 NOT_FOUND`，message=`workspace 不存在` |

结论：PASS。未配置真实 oModel 时不会 silent fallback 到 mock，运行边界 fail-closed。

## B2 兼容回归

启动方式：

```bash
env -u OPENOPS_PLATFORM_GLM_API_KEY \
OPENOPS_DATABASE_URL=postgresql://openops:openops@localhost:5432/openops \
OPENOPS_OMODEL=mock \
OPENOPS_RUNTIME=agentscope \
OPENOPS_ORCH_DELAY_MS=200 \
.venv/bin/uvicorn main:app --app-dir src --host 0.0.0.0 --port 18081
```

批准路径结果：

| 项 | 结果 |
|---|---|
| Run | `b9ae56a2-ec31-4899-9b75-b7cf9cdf087c` |
| Task | `tsk_99e182fadc` |
| Task 状态 | completed |
| RCA revision | 3 |
| model payload | `stub-rca` |
| model.call 事件 | `openops.model.call.started/succeeded` 正常出现 |
| scope payload | `appid_count=3`，有 `scope_snapshot_id` |

拒绝路径结果：

| 项 | 结果 |
|---|---|
| Run | `e69695ec-d01b-4976-a46c-cf49d2eef606` |
| Task 状态 | completed |
| RCA revision | 2 |
| conclusion | `恢复动作被拒绝：保持观察，建议走短期配置优化。` |
| recover_execute | 无外部 `request_id` |

结论：PASS。B2 报告中发现的“拒绝后 conclusion 被覆盖成已恢复”问题在 `a3a9380` 已修复。

## 静态口径检查

| 检查项 | 结果 |
|---|---|
| 无 PG 侧 workspace→APPID 映射表 | PASS |
| `effective_appids` 来自 oModel adapter | PASS，生产代码仅 `scope_service -> omodel_client.resolve_scope` |
| `scope_snapshot` 只用于审计回放 | PASS，DDL 注释明确“不是权限事实源” |
| mock adapter 内存 `app_ids` | 仅用于 demo/pytest 的 oModel mock，不是 OpenOps PG 事实源 |
| real adapter 未配置 base url | PASS，返回失败并由 Scope Service fail-closed |

## 发现问题与建议

| ID | 严重度 | 问题 | 建议 |
|---|---|---|---|
| B3-OBS-001 | P3 | revision changed 时审计顺序为 `scope.updated -> task.started -> scope.resolved`，可能和部分前端“task.started 总是第一条 task 事件”的假设不同 | 前端 projection 对 `scope.updated/scope.blocked` 出现在 `task.started` 前保持兼容；或后续统一事件语义 |
| B3-OPS-001 | P3 | 隔离 worktree 执行 `docker compose up -d` 会因固定 `container_name: openops-v1-pg` 与已有容器冲突 | 后续可移除固定 container_name，或测试文档注明复用已有 PG |
| B3-DEP-001 | P3 | npm install 仍提示 2 个依赖审计项 | 后续单独评估前端依赖升级，不建议混入 B3 |

未发现阻断 B3 合入/演示的 P0/P1 问题。

