# 多 Agent 编排手册（感知快恢模板 · 对齐老项目 D6 效果）

> 编排入口：**管理台 → 模板管理 → 编辑**（TemplateEditorModal）。改模板=保存草稿→发布，
> **零改码零重启**；已实例化用户在下一次任务边界自动派生升级（28.7，保留其角色追加与资产绑定）。
> 老项目（openOps-Dev，roles.yaml+重启）的全部编排能力在本项目由模板 `content_json` 承接。

## 一、机制对照（老 roles.yaml → 新模板 content_json）

| 老字段（37 号 roles.yaml） | 新字段（content_json） | 说明 |
|---|---|---|
| `roles.main.system_prompt` | `main.role` | 模板级 main 提示词；普通用户只能实例级「追加」不可改 |
| `roles.main.mcps`/`sre_tools` | `main.default_tools` | **main 直连平台/MCP 工具白名单（含动态注册表工具）；空=零工具=纯编排派发**（老 D6「main 无观测工具被迫派发」效果） |
| `roles.main.skills` | `main.skills` | main 直连技能白名单；**空/缺省=不限**（沿用 平台 active ∪ 实例绑定），非空=交集收窄。注意与 default_tools「空=零」语义相反 |
| `roles.main.max_children` | `main.max_children`（1..10） | 同时活跃子 Agent 上限，名额随完成释放 |
| （老 delegation_max_spawns env） | `main.delegation_max_spawns`（1..100） | 单 task 累计派发兜底（防失败重派死循环） |
| `roles.<worker>` 每角色一块 | `sub_agents[]` 每角色一项 | `key`（派发角色枚举）/`label`/`role`（提示词） |
| `roles.<worker>.skills` | `sub_agents[].skills` | 子 Agent skill_key 白名单（toolkit 按此裁剪） |
| `roles.<worker>.mcps`（enable/disable_tools） | `sub_agents[].mcp_tools` | 子 Agent 平台/动态工具白名单（工具名粒度，等价老 enable_tools） |
| `roles.<worker>.max_iters` | `sub_agents[].max_iters`（1..200） | ReAct 轮数上限 |
| `roles.<worker>.tool_result_limit` | `sub_agents[].tool_result_limit`（1000..200000） | 单条工具结果保留 token；**不变式 trl < 模型窗口 ≤1/3**（老版 160000 是 128k 窗口事故源，按新模型窗口换算，如 24000） |
| （旧前端本地文案） | `activity_labels.tools`（可选） | 活动栏工具业务名称映射；只影响展示，不改变工具名、授权、调用或审计 |
| `roles.<worker>.system_prompt` 汇报纪律 | 内置 `SUB_REPORT_DISCIPLINE` 自动拼接 | 缺参返 blocker/禁无差别调工具/空结果即完成/一次性汇报——**不用在每个角色重复写** |
| `permission_mode: accept_edits` | Tool 标注 `is_approval_required` | 审批粒度从「角色」改「工具标注」：写类工具标 ASK → 子 Agent 触发时审批卡带子 task_id 弹前端，批准后精确路由回该子继续（E1 桥；审批等待不吃超时预算） |
| `can_spawn: false` | 天然单层 | 子 task 的 `sub_agents` 恒空 → 无二层派发，无需配置 |
| （无 per-role model） | （同样无） | 子 Agent 继承 main 模型；per-role model 两代都不支持 |

运行面等价性：星型派发（`dispatch_subagents` 并行 gather）、delegation 账本、双预算、
per-agent 工具隔离（skills/mcp_tools 白名单裁剪 toolkit）。每次批量派发生成稳定的
`dispatch_batch_id` 和任务内递增 `dispatch_batch_no`；同批共享批次、每个 worker 仍有独立
`delegation_id`/`child_task_id`。右侧活动栏按批次展示轮次、按 delegation 分轨，同一角色重复派发
不会互相污染状态。差异如实标注：老版 park+逐 TeamSay 唤醒 → 新版 gather 等齐一次性返回（V1 口径）。

## 二、复刻 37 号 10 角色的操作步骤

**前置（一次性，资产就位才有东西可绑）**：
1. **Skill 资产**：老 skill bundle（log-query/metric-query/alarm-query/wefix-*/apm-analysis/
   db-analysis/redis-analysis/change-query/grafana-ops…）在新平台是 Skill 资产——设置页上传
   （或平台侧 SkillHub 对账），确认 status=active、记下 **skill_key**。
2. **MCP 工具进目录**：注册表对账（设置页「刷新对账」或后台 reconcile）把
   log-server/metric-server/alarm-server/recover… 各 server 的工具拉进目录。
3. **Tool 标注 allowed**：管理台 → 模板管理 → 资产治理 → Tool 标注——把要绑定的工具标
   `allowed`（读类免审批；**写类/恢复类勾 `is_approval_required`**=老 accept_edits 效果）。
   未标注 allowed 的工具在编辑器勾选不到、发布也会被校验拒绝。

**编排（管理台 → 模板管理 → 编辑）**：
1. main 区：role 提示词（编排者口径：理解任务→拆解→派发→汇总）；`max_children=7`、
   `delegation_max_spawns` 按需（老版兜底 20）；**main skills 留空**（=不限）或按需收窄；
   **main MCP 勾选**只勾 main 需要直连的（如 omodel 类）——观测类工具**不勾**，交给子角色
   （main 拿不到就会派发，这正是老效果）。
2. sub Agent 组：逐个「新增角色」——log/metric/alarm/recovery/apm/db/redis/change/grafana，
   每角色填：key（如 `log`）、label（日志 Agent）、role（该域职责一两句；汇报纪律已内置）、
   `skills`（如 `log-query`）、`mcp_tools`（如 `get_logs_agg, get_logs_histogram, get_logs_list`）、
   `max_iters`（老版 100 偏大，建议 20-40）、`tool_result_limit`（按模型窗口 ≤1/3）。
3. 可选在「活动栏工具名称」为已绑定工具填写业务名称，例如 `query_alarm_list → 查询告警`。
   运行时先匹配完整工具名，再匹配 MCP 内层工具名；留空则回退目录/原始工具名。
4. 保存草稿 → 发布。新实例即用新版本；存量实例下次起任务自动升级。

**验收**：对话下发跨域任务（如「查支付域最近告警并定界」）→ 活动栏自动切到「子 Agent」并出现
派发轮次 → 每个 delegation 独立显示运行/待审批/汇报/异常/超时/取消状态 → 恢复类动作弹审批卡
（带子 task_id）→ 批准后子 Agent 继续并汇报。业务视图只展示脱敏里程碑与汇报摘要；技术视图
展示星型编排和脱敏事件轨迹。审计页按 trace 可回放 `subagent.dispatched/reported` 与
`tool.call.*` 全链。

## 三、活动恢复与事实边界（2026-07-14）

- `sre_agent_delegation` 是子 Agent 终态事实源；前端不按文案或静默时长推断终态。
- `audit_event_id` 同时作为实时 `event_id`。AG-UI CUSTOM、备用 SSE、`/state` 和历史分页按该 ID
  合并；断线恢复不会重复节点。
- `/state` 返回最新 100 条事件、脱敏 delegation 摘要和历史游标；「显示更早」调用
  `GET /agent-runs/{run_id}/events?before=...`，不使用轮询。
- 存量 delegation 没有批次字段时统一进入「历史派发」，不按时间窗口猜轮次。
- `task_summary`、`report_summary` 和事件 `summary` 均由后端脱敏并截断；接口和活动栏禁止展示
  完整任务/汇报正文、原始 prompt、Secret/Cookie、完整工具参数及完整响应。
- 活动 payload 采用事件类型白名单：工具、Skill、沙箱只返回参数/结果/错误/输出摘要；scope 只返回
  snapshot/revision/APPID 数量，不返回 `effective_appids` 明细。审批参数是必要例外，但仍做递归脱敏和
  深度/项数/长度限制；旧审计行在 `/state`、`/events` 和审计 API 出口还会再做一次安全投影。

## 四、升级注意（2026-07-14 编排对称化行为变更）

- **main 的动态注册表工具改为白名单制**（与 sub 一致）：升级后模板 `default_tools` 没勾的
  动态工具对 main 不再注入（此前 main 全量豁免）。存量环境升级后须在模板编辑器把 main 需要
  直连的动态工具勾上（先按上文前置③标注 allowed），或按本手册把观测工具编排给子角色。
- `main.skills` 为新增可选字段：**空/缺省=不限**，存量模板无感；只有显式配了非空列表才收窄
  （composer「/」列表与执行门禁同源过滤，前端展示即后端可执行）。
