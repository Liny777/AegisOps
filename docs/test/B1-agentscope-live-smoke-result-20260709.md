# B1 AgentScope Live Smoke 测试结果（2026-07-09）

## Summary

- 测试对象：`docs/B1-agentscope-live-smoke.md`
- 工程目录：`/Users/liny/Documents/Code/profession-sre-agent/openOps-Dev-New`
- 分支：`feat/workbench-frontend`
- 当前 HEAD：`3568a85 docs: B1 真 AgentScope 全栈冒烟运行手册（PG+agentscope 后端联调步骤）`
- PostgreSQL：`openops-v1-pg` healthy
- Python：`3.11.7`
- AgentScope：`2.0.3`
- 结论：B1 AgentScope live smoke 主链路通过；`agentscope` 与 `mock` runtime 的批准路径事件序列同形。

> 注意：测试前工作树已有未提交改动，其中包含 `backend/src/runtime/agentscope_runtime.py`。本次测试未修改源码；仅创建本报告。

## Commands

### 安装依赖

```bash
cd /Users/liny/Documents/Code/profession-sre-agent/openOps-Dev-New/backend
python3.11 -m venv .venv
.venv/bin/pip install -e ".[test,agentscope]"
.venv/bin/python -c "import agentscope; print('agentscope', agentscope.__version__)"
```

输出：

```text
agentscope 2.0.3
```

### 基础回归

```bash
cd /Users/liny/Documents/Code/profession-sre-agent/openOps-Dev-New/backend
.venv/bin/pytest -q
```

输出：

```text
25 passed, 1 warning
```

### 前端构建

```bash
cd /Users/liny/Documents/Code/profession-sre-agent/openOps-Dev-New/frontend
npm run build
```

结果：通过。

## AgentScope Runtime 批准路径

启动参数：

```bash
OPENOPS_DATABASE_URL="postgresql://openops:openops@localhost:5432/openops" \
OPENOPS_RUNTIME=agentscope \
OPENOPS_ORCH_DELAY_MS=200 \
.venv/bin/uvicorn main:app --app-dir src --host 0.0.0.0 --port 18081
```

测试链路：

1. `GET /api/openops/v1/me`
2. `GET /api/openops/v1/templates/available`
3. `POST /api/openops/v1/agent-teams`
4. `POST /api/openops/v1/agent-runs`
5. `POST /api/openops/v1/agent-runs/{run_id}/tasks`
6. `GET /api/openops/v1/agent-runs/{run_id}/approvals`
7. `POST /api/openops/v1/approvals/{approval_request_id}:decide`，`decision=approved`
8. `GET /api/openops/v1/agent-runs/{run_id}/state`
9. `GET /api/openops/v1/audit/runs/{run_id}`

结果：

```json
{
  "task": "completed",
  "rca_revision": 3,
  "events": [
    "agent_run.created",
    "task.started",
    "scope.resolved",
    "openops.tool.call.started",
    "openops.tool.call.succeeded",
    "openops.rca.updated",
    "openops.tool.call.started",
    "openops.tool.call.succeeded",
    "openops.rca.updated",
    "openops.approval.required",
    "approval.approved",
    "openops.tool.call.succeeded",
    "openops.rca.updated",
    "openops.task.completed"
  ]
}
```

审计：

```json
{
  "count": 14,
  "events": [
    "agent_run.created",
    "task.started",
    "scope.resolved",
    "openops.tool.call.started",
    "openops.tool.call.succeeded",
    "openops.rca.updated",
    "openops.tool.call.started",
    "openops.tool.call.succeeded",
    "openops.rca.updated",
    "openops.approval.required",
    "approval.approved",
    "openops.tool.call.succeeded",
    "openops.rca.updated",
    "openops.task.completed"
  ]
}
```

## AgentScope Runtime 拒绝路径

测试链路同批准路径，但审批决策为：

```json
{
  "decision": "rejected"
}
```

结果：

```json
{
  "task": "completed",
  "rca_revision": 2,
  "events": [
    "agent_run.created",
    "task.started",
    "scope.resolved",
    "openops.tool.call.started",
    "openops.tool.call.succeeded",
    "openops.rca.updated",
    "openops.tool.call.started",
    "openops.tool.call.succeeded",
    "openops.rca.updated",
    "openops.approval.required",
    "approval.rejected",
    "openops.rca.updated",
    "openops.task.completed"
  ]
}
```

审计：

```json
{
  "count": 13,
  "events": [
    "agent_run.created",
    "task.started",
    "scope.resolved",
    "openops.tool.call.started",
    "openops.tool.call.succeeded",
    "openops.rca.updated",
    "openops.tool.call.started",
    "openops.tool.call.succeeded",
    "openops.rca.updated",
    "openops.approval.required",
    "approval.rejected",
    "openops.rca.updated",
    "openops.task.completed"
  ]
}
```

验证点：

- `approval.rejected` 后没有恢复执行的 `openops.tool.call.succeeded`。
- 拒绝路径仍正常收口为 `task.completed`。

## Mock Runtime Parity

启动参数：

```bash
OPENOPS_DATABASE_URL="postgresql://openops:openops@localhost:5432/openops" \
OPENOPS_RUNTIME=mock \
OPENOPS_ORCH_DELAY_MS=200 \
.venv/bin/uvicorn main:app --app-dir src --host 0.0.0.0 --port 18081
```

批准路径结果：

```json
{
  "task": "completed",
  "rca_revision": 3,
  "events": [
    "agent_run.created",
    "task.started",
    "scope.resolved",
    "openops.tool.call.started",
    "openops.tool.call.succeeded",
    "openops.rca.updated",
    "openops.tool.call.started",
    "openops.tool.call.succeeded",
    "openops.rca.updated",
    "openops.approval.required",
    "approval.approved",
    "openops.tool.call.succeeded",
    "openops.rca.updated",
    "openops.task.completed"
  ]
}
```

审计：

```json
{
  "count": 14,
  "events": [
    "agent_run.created",
    "task.started",
    "scope.resolved",
    "openops.tool.call.started",
    "openops.tool.call.succeeded",
    "openops.rca.updated",
    "openops.tool.call.started",
    "openops.tool.call.succeeded",
    "openops.rca.updated",
    "openops.approval.required",
    "approval.approved",
    "openops.tool.call.succeeded",
    "openops.rca.updated",
    "openops.task.completed"
  ]
}
```

机器比对：

```text
agentscope_events == mock_events -> True
```

## Findings

### 1. Smoke 文档中的实例 ID jq 路径已过期

文档当前写法：

```bash
jq -r '.data.instance.agent_team_instance_id'
```

实际响应字段：

```bash
jq -r '.data.instance.instance_id'
```

影响：

- 按文档原样执行时，`IID=null`。
- 后续 `POST /agent-runs` 会把 `agent_team_instance_id=null` 传给后端，触发 500：

```text
psycopg.errors.InvalidTextRepresentation: invalid input syntax for type uuid: "null"
```

建议：

- 将文档第 4.3 节实例 ID 提取路径改为 `.data.instance.instance_id`。
- 或后端 DTO 同时兼容返回 `agent_team_instance_id`，但当前前端和 API facade 使用的是 `instance_id`，建议优先修文档。

### 2. Smoke 文档中的 approval 事件名与实际事件不一致

文档期望：

```text
openops.approval.approved
```

实际事件：

```text
approval.approved
```

拒绝路径实际事件：

```text
approval.rejected
```

mock runtime 与 agentscope runtime 在该事件名上保持一致，因此这是文档期望或事件命名口径需要统一的问题。

建议：

- 若当前事件命名是最终口径，则更新 smoke 文档期望。
- 若希望所有事件统一 `openops.*` 前缀，则需要同时修改 mock runtime、agentscope runtime、前端投影和相关测试。

## Final Result

- AgentScope live smoke：通过。
- Mock parity：通过。
- 拒绝路径：通过。
- 基础 pytest：通过。
- 前端 build：通过。
- 发现 2 个文档/口径问题，见 Findings。
