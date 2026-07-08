# OpenOps V1 联调接口说明

## 运行模式

V1 新工程先提供 mock 外部依赖与 mock Agent runtime。前端默认走本地 mock facade；设置 `VITE_OPENOPS_API_MODE=real` 后调用后端 `/api/openops/v1/...`。

## Mock Header

后端支持以下 header 便于演示：

- `X-OpenOps-Mock-Role: user | platform_admin`
- `X-OpenOps-Mock-Whitelist: true | false`
- `X-OpenOps-Mock-User: <login_key>`

## 核心 API

- `GET /api/openops/v1/me`
- `GET /api/openops/v1/templates/available`
- `POST /api/openops/v1/workspaces`
- `GET /api/openops/v1/workspaces/{workspace_id}/status`
- `GET /api/openops/v1/agent-teams`
- `POST /api/openops/v1/agent-teams`
- `GET /api/openops/v1/agent-teams/{instance_id}`
- `POST /api/openops/v1/agent-teams/{instance_id}/config-versions`
- `POST /api/openops/v1/agent-teams/{instance_id}/asset-bindings`
- `GET /api/openops/v1/assets/skills`
- `GET /api/openops/v1/assets/mcps`
- `POST /api/openops/v1/secrets`
- `GET /api/openops/v1/llm-configs`
- `POST /api/openops/v1/llm-configs`
- `POST /api/openops/v1/agent-runs`
- `GET /api/openops/v1/agent-runs/{agent_run_id}/state`
- `POST /api/openops/v1/agent-runs/{agent_run_id}/tasks`
- `POST /api/openops/v1/agent-runs/{agent_run_id}/ag-ui`
- `POST /api/openops/v1/agent-runs/{agent_run_id}:select-model`
- `POST /api/openops/v1/tasks/{task_id}:cancel`
- `POST /api/openops/v1/agent-runs/{agent_run_id}:close`
- `GET /api/openops/v1/agent-runs/{agent_run_id}/approvals`
- `POST /api/openops/v1/approvals/{approval_request_id}:decide`
- `GET /api/openops/v1/audit/runs/{agent_run_id}`
- `GET /api/openops/v1/admin/overview`

## 事件口径

AG-UI mock endpoint 返回 `openops.*` 事件，覆盖 task、scope、runtime_plan、model、tool、skill、sandbox、approval、audit 和 run closed。实时流协议在真实 runtime 接入时可替换为 SSE/streaming。
