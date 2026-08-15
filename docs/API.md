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
- `GET /api/openops/v1/whitelist` — **免鉴权开放查询**（外部系统拼外链 `?q=` 前判断用户是否已开通；全量出 `{users:[{user_id,display_name}]}`，`?user_id=` 点查出 `{user_id,whitelisted}`；只读，写仍走 admin 面）
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
- `POST /api/openops/v1/llm-configs` — 可选 `extra_headers`（自定义出站 Header，随每次 LLM 请求附带；禁 `Authorization`/`Host` 等保留头，鉴权走 `secret_ref_id`）；`POST /llm-configs:test-connection` 同参同源携带
- `PATCH /api/openops/v1/llm-configs/{llm_config_id}` — 局部更新自带模型（只改显式提供的键）。改 `base_url`/`model_name`/`secret_ref_id`/`extra_headers` 会强制重探测，不通过则整条不保存（`MODEL_PROBE_FAILED`）；`extra_headers` 传 `{}` 即清空。非本人配置 404
- `DELETE /api/openops/v1/llm-configs/{llm_config_id}` — 软删 + 连带作废其专属 Secret。**不做引用检查**：仍绑着它的 Agent 下次起任务由服务端降级到平台默认模型，并推 `openops.model.user_llm_degraded`（工作台横幅告知用户重新选择）+ 写 `model.user_llm_degraded` 审计。非本人配置 404
  - 绑定期已有 fail-closed 校验：`user_llm_config_id` 绑不存在/非本人/非 active 的配置 → 403 `MODEL_NOT_AUTHORIZED`，所以运行期降级只对应「原本有效、后来被删」这一种情形
  - 用户**主动** `select-model` 选到失效配置仍当场报错（`SECRET_REQUIRED`），不降级
- `POST|PUT /api/openops/v1/admin/model-assets[/{id}]` — 平台模型资产同样支持 `extra_headers`（同一套保留头校验）；`POST /admin/model-assets:test-connection` 同参同源携带
- `POST /api/openops/v1/agent-runs`
- `GET /api/openops/v1/agent-runs`
- `GET /api/openops/v1/agent-runs/{agent_run_id}/state`
- `GET /api/openops/v1/agent-runs/{agent_run_id}/events?before={audit_event_id}&limit=100`
- `POST /api/openops/v1/agent-runs/{agent_run_id}/tasks`
- `POST /api/openops/v1/agent-runs/{agent_run_id}/agui` — CopilotKit 使用的 AG-UI 运行流
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
- `GET /api/openops/v1/admin/users` — 分页 + 搜索 + 标签过滤：`?page=&page_size=`（上限 100）`&q=`（按 user_id/display_name 模糊）`&tag=`（按领域标签精确，与 q 为 AND，均服务端过滤）→ `{items,total,page,page_size}`
- `GET /api/openops/v1/admin/users/tags` — 标签下拉候选：所有未删用户已用领域标签（去重、排序）→ `["财经","研发",…]`
- `POST /api/openops/v1/admin/users/whitelist` — 可选 `tags`（领域标签数组；不传=不动已有标签）
- `POST /api/openops/v1/admin/users/whitelist:revoke`
- `POST /api/openops/v1/admin/users/{user_id}:set-role`
- `POST /api/openops/v1/admin/users/{user_id}:set-tags` — 改领域标签（整体替换，`[]` 清空；写 `user.tags_changed` 审计）
- `DELETE /api/openops/v1/admin/users/{user_id}` — 删除用户（软删 + 连带撤白名单，写 `user.deleted` 审计；不能删自己；被删用户重新加白名单才复活）
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

### 活动恢复与历史分页

- `/state` 返回最新 100 条 `recent_events`（页内按发生时间正序）、`events_next_cursor`、
  `events_has_more`，以及当前 Run 的脱敏 `delegations` 摘要。
- `/events` 的 `limit` 取值为 1..200，默认 100；`before` 使用上一页最老一条
  `audit_event_id`。响应为 `{items, next_cursor, has_more}`，每页仍按时间正序。
- 持久化事件的实时 `event_id` 与审计行 `audit_event_id` 相同；前端可直接合并
  AG-UI CUSTOM、备用 SSE、`/state` 和 `/events`，无需按文案猜测重复事件。

多 Agent 活动事件在 `payload_redacted_json` 中统一携带可用的关联字段：

- `agent_key`、`agent_label`
- `leader_task_id`、`child_task_id`
- `delegation_id`
- `dispatch_batch_id`、`dispatch_batch_no`
- `summary`，以及按事件类型可选的 `task_summary`、`report_summary`、`display_label`
- `argument_summary`、`result_summary`、`error_summary`、`command_summary`、
  `stdout_summary` / `stderr_summary`
- `request_id`、`execution_id`；审计行/envelope 顶层另保留 `external_request_id`

不得通过活动接口返回完整 task/report 正文、原始 prompt/messages、Secret、Cookie、完整工具参数、
完整 MCP 响应或 stdout/stderr。后端按事件类型 deny-by-default 白名单投影：未知 payload 字段不返回；
`approval.required` 仅为用户决策保留限制深度/项数/长度后的脱敏 `args`，`rca.updated` 只保留
`RcaCardData` 既定结构。技术视图只消费这些后端已经脱敏和截断的字段。
