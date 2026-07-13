# OpenOps V1 联调接口说明

## 运行模式

V1 新工程先提供 mock 外部依赖与 mock Agent runtime，但前端默认使用 real facade 调用后端 `/api/openops/v1/...`。需要纯 UI 演示时可设置 `VITE_OPENOPS_API_MODE=mock`。

后端启动前先执行：

```bash
docker compose up -d
```

PostgreSQL DDL 来自 `backend/sql/openops_v1_core.sql`，无数据库级表间关联约束、无数据库自动回调机制；后端启动时会幂等 seed demo 用户、模板、平台资产、MCP Tool 标注、沙箱容量配置和平台模型。

## Mock Header

后端支持以下 header 便于演示：

- `X-OpenOps-Mock-User: <login_key>`
- `X-OpenOps-Mock-Name: <urlencoded display name>`

角色与白名单不由 header 声称，事实来自 PG：

- 普通用户：`0026demo01`
- 平台管理员：`admin`
- 未在白名单中的任意新用户：`GET /me` 会返回 `whitelisted=false`，受保护 API 返回 `NOT_WHITELISTED`。

## 核心 API

- `GET /api/openops/v1/me`
- `GET /api/openops/v1/templates/available`
- `POST /api/openops/v1/workspaces`
- `GET /api/openops/v1/workspaces/{workspace_id}/status`
- `GET /api/openops/v1/agent-teams`
- `POST /api/openops/v1/agent-teams`
- `GET /api/openops/v1/agent-teams/{instance_id}`
- `GET /api/openops/v1/agent-teams/{instance_id}/config-versions`
- `POST /api/openops/v1/agent-teams/{instance_id}/config-versions`
- `GET /api/openops/v1/agent-teams/{instance_id}/asset-bindings`
- `POST /api/openops/v1/agent-teams/{instance_id}/asset-bindings`
- `DELETE /api/openops/v1/asset-bindings/{binding_id}`
- `GET /api/openops/v1/assets/skills`
- `POST /api/openops/v1/assets/skills`
- `GET /api/openops/v1/assets/mcps`
- `POST /api/openops/v1/assets/mcps`
- `POST /api/openops/v1/secrets`
- `GET /api/openops/v1/secrets`
- `GET /api/openops/v1/llm-configs`
- `GET /api/openops/v1/models/platform`
- `POST /api/openops/v1/llm-configs`
- `POST /api/openops/v1/agent-runs`
- `GET /api/openops/v1/agent-runs`
- `GET /api/openops/v1/agent-runs/{agent_run_id}/state`
- `POST /api/openops/v1/agent-runs/{agent_run_id}/tasks`
- `POST /api/openops/v1/agent-runs/{agent_run_id}/ag-ui`
- `GET /api/openops/v1/agent-runs/{agent_run_id}/events/stream`
- `POST /api/openops/v1/agent-runs/{agent_run_id}:select-model`
- `POST /api/openops/v1/tasks/{task_id}:cancel`
- `POST /api/openops/v1/agent-runs/{agent_run_id}:close`
- `GET /api/openops/v1/agent-runs/{agent_run_id}/approvals`
- `POST /api/openops/v1/approvals/{approval_request_id}:decide`
- `GET /api/openops/v1/audit/runs/{agent_run_id}`
- `GET /api/openops/v1/audit/traces/{audit_trace_id}`
- `GET /api/openops/v1/admin/templates`
- `GET /api/openops/v1/admin/mcp-tools`
- `PUT /api/openops/v1/admin/mcp-tools/{tool_catalog_id}/annotation`
- `GET /api/openops/v1/admin/users`
- `POST /api/openops/v1/admin/users/whitelist`
- `GET /api/openops/v1/admin/sandbox`
- `PUT /api/openops/v1/admin/sandbox`

- `GET /api/openops/v1/admin/models`
- `POST /api/openops/v1/admin/models`
- `GET /api/openops/v1/admin/audit/recent`

### 创建系统范围的上游错误

`POST /api/openops/v1/workspaces` 的成功信封不变。oModel 创建失败按依赖语义返回：

- `OMODEL_AUTH_FAILED`：HTTP 502、不可重试；表示 oModel Cookie/CSRF 同源校验拒绝，不触发 OpenOps 登录跳转。
- `VALIDATION_FAILED`：HTTP 400、不可重试；表示 oModel 拒绝 workspace 参数。
- `OMODEL_UPSTREAM`：网络或 5xx 为 HTTP 502，超时为 HTTP 504；可重试。

## 事件口径

实时流使用 SSE：

- Endpoint：`GET /api/openops/v1/agent-runs/{agent_run_id}/events/stream`
- `id:` 为事件 sequence，支持 `Last-Event-ID` 补发。
- 心跳为 SSE comment。
- 断线且缓冲不足时发送 `event: resync`，前端应调用 `/state` 恢复。

兼容批量端点 `POST /api/openops/v1/agent-runs/{agent_run_id}/ag-ui` 返回当前内存缓冲内的 `openops.*` 事件。
