# OpenOps V1 后续开发块

本仓库当前完成最小闭环：PG + OpenOps REST + SSE + mock runtime + mock 外部依赖。后续按块替换 mock 能力，每块都应独立开发、独立验收。

## B1 Scope Service + oModel 契约

- 接入真实 oModel workspace/status/resolve。
- `effective_appids` 只从 oModel resolve 获取，OpenOps 不维护 workspace 到 APPID 映射。
- 覆盖 SCOPE-* 用例：ready、syncing、empty scope、revision 回写、resolve fail-closed。

## B2 Tool Gateway + MCP Tool 标注运行时

- 把 mock MCP 调用替换为 Tool Gateway。
- 平台 HTTP MCP 必须经过 `mcp_tool_annotation`、scope 校验、ASK、Secret、审计。
- 覆盖 MCP-* 用例：未标注 block、appid 越界、恢复类 tool 走普通 MCP 链路。

## B3 Secret 加密 + Model Gateway

- 将当前占位加密替换为正式密钥管理与 key version。
- 接入 OpenAI-compatible 模型探测、tool calling 校验、上下文预算和 streaming。
- 覆盖 MODEL-* / SEC-* 用例：Secret 不回显、探测失败不可 active、上下文超限收口。

## B4 资产对账与 RuntimePlan 热更新

- 接入真实 Skill Hub / MCP Registry。
- 完成登录对账、后台 reconciler、schema_hash 变化重新标注。
- 覆盖 ASSET-* 用例：source=openops、历史绑定不原地改写、禁用后运行时 fail-closed。

## B5 管理台真 API 全量

- 完成模板版本发布、MCP Tool 标注编辑、资产治理、白名单、审计 Trace 查询。
- 覆盖 ADMIN-* 用例：普通用户 forbidden、管理员修改写审计、沙箱容量配置生效。

## B6 Docker 沙箱执行器

- 实现用户级容器、task 独立工作目录、资源限制、artifact 回传。
- 用户 Skill 必须进沙箱，用户 HTTP MCP 仍保持 V1 HTTP-only。
- 覆盖 SKILL-* / SANDBOX-* 用例：checksum、超时、日志截断、Secret/Cookie 不进沙箱。

## B7 AgentScope 2.0.3 Runtime

- 用真实 AgentScope session/team/toolkit/permission/event 替换 mock orchestrator。
- 不修改 AgentScope，不修改 Redis StorageBase。
- 保持 OpenOps PG 为配置、审批、审计事实源。

## B8 真实 W3/IAM

- 用公司 W3/IAM Cookie 替换 mock header。
- 保持 `/me` 分流、白名单、平台管理员角色和 owner 隔离语义不变。

## B9 Playwright E2E 与安全扫描

- 增加浏览器端 E2E：初始化、工作台 SSE、ASK、取消、关闭、管理台。
- 增加静态扫描：旧口径、敏感字段、禁用功能入口、前端路由守卫。
