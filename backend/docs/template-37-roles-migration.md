# 老版 roles.yaml（37 号）→ 感知快恢模板迁移手册（2026-07-12）

老项目 roles.yaml 的角色画像在新平台的权威载体是**模板的 `content_json`**（`sre_agent_team_tpl_version` 表）。
不再有 yaml 文件；改角色 = 发一个新模板版本。本文给出字段映射、前置条件、可直接使用的 content_json 与两条落库路径。

## 一、字段映射（老 → 新）

| 老 roles.yaml 字段 | 新 content_json 字段 | 说明 |
|---|---|---|
| `display_name` | `sub_agents[].label` | 前端活动栏组头/派发工具描述用 |
| `system_prompt` | `sub_agents[].role` | **只保留角色专属内容**。通用纪律（缺参数 blocker/禁无差别调用/空结果=完成/一次性汇报/只报结果）已由 `SUB_REPORT_DISCIPLINE` 运行时自动拼接（seed.py:31），不要重复；`TeamSay`、`read_offloaded` 在新架构不存在，删除相关句子（汇报经工具返回值回主 Agent） |
| `skills` | `sub_agents[].skills` | skill_key 白名单。**发布时不校验**，运行时按实例可用技能过滤——名字写错会静默变空，须与 Skill Hub 的 skill_key 完全一致 |
| `mcps` + `enable_tools` | `sub_agents[].mcp_tools` | 扁平**工具名**白名单（不再按 server 引用）。server `{}`（全量）要展开成具体工具名，从管理台 → 资产治理 → Tool 标注页抄。动态真工具在子 Agent 面同样按此白名单裁剪（e11aa5b） |
| `max_iters` | `sub_agents[].max_iters` | 校验 1..200。老值 100 合法，但子 Agent 执行预算默认 300s（`OPENOPS_SUBAGENT_TIMEOUT_S`），真模型 100 轮跑不完会先撞超时——本手册取 40；要跑长任务同步调大超时 env |
| `tool_result_limit` | `sub_agents[].tool_result_limit` | 校验 1000..200000。⚠**老值 160000 禁止照抄**：D7 事故=160000 > GLM 128k 窗口，单条工具结果撑爆上下文、压缩删掉用户问题。不变式：< 模型窗口，经验 ≤1/3（128k → ≤40000）。本手册统一 24000 |
| `can_spawn` | （删） | 新架构子 Agent 恒禁二层派发，代码硬保证 |
| `permission_mode` | （删） | 新架构由工具标注推导：只读（readOnlyHint/标注免审批）自动放行，写类工具触发审批卡（E1 审批桥：审批带子 task_id，批准后路由回该子 Agent 续跑） |
| `sre_tools` | （暂无对应） | 老内置本体/知识工具未迁移，V1 差距项（34 号盘点） |
| `main.max_children` | `main.max_children` | 校验 1..10（老值 7 合法）；另有单批硬上限 5 |
| —— | `main.delegation_max_spawns` | 新增兜底：单 task 累计派发上限（老项目经验 8→20） |

## 二、前置条件（不做发布会被校验拒绝）

发布校验要求 `main.default_tools` ∪ 全部 `sub_agents[].mcp_tools` 里的每个工具都已 **status=allowed 标注**
（template_service.py:68-73）。内网动态真工具经对账进目录（`sre_mcp_tool_catalog`）但**标注为空**——运行时能用
（origin=dynamic 注入），**绑进模板必须先由管理员显式标注**：

1. 触发对账：登录管理台自动 kick，或 `POST /api/openops/v1/assets:reconcile`；
2. 管理台 → 资产治理 → Tool 标注：把要绑的工具逐个标 `allowed`（写类勾「需人工审批」）；
   或 `POST /api/openops/v1/admin/mcp-tools/{tool_catalog_id}/annotation`；
3. 确认要用的 skill_key 在 Skill Hub 已 active。

## 三、迁移后的 content_json（占位符替换后可直接用）

`__FILL:*__` 处必须替换为管理台目录里的真实工具名（并已标注 allowed），或先删掉该角色，否则发布被拒。

```json
{
  "main": {
    "role": "理解用户任务，调度巡检/定界/恢复能力，工具调用前遵守平台安全策略。互不依赖的查询用 dispatch_subagents 一次派一批并行执行。",
    "default_tools": ["query_resource", "recover_execute"],
    "max_children": 7,
    "delegation_max_spawns": 20
  },
  "sub_agents": [
    {"key": "alarm", "label": "告警", "role": "你是 SRE 告警查询子 Agent（只读）。必须加载 alarm-query 技能，严格按照技能中的执行路由和约束执行。",
     "skills": ["alarm-query"], "mcp_tools": ["query_alarm_list", "query_alarm_detail"],
     "max_iters": 40, "tool_result_limit": 24000},
    {"key": "log", "label": "日志", "role": "你是 SRE 日志查询子 Agent（只读）。必须加载 log-query 技能，严格按照技能中的执行路由和约束执行。",
     "skills": ["log-query"], "mcp_tools": ["get_logs_agg", "get_logs_histogram", "get_logs_list"],
     "max_iters": 40, "tool_result_limit": 24000},
    {"key": "metric", "label": "指标", "role": "你是 SRE 指标查询子 Agent（只读）。必须加载 metric-query 技能，严格按照技能中的执行路由和约束执行。禁止自行追加任务未指定的指标——只查任务要求的。",
     "skills": ["metric-query"], "mcp_tools": ["__FILL:metric-server 工具名__"],
     "max_iters": 40, "tool_result_limit": 24000},
    {"key": "recover", "label": "恢复", "role": "你是 SRE 恢复执行子 Agent。必须根据任务加载相应技能，严格按技能执行路由和约束执行；技能不满足任务时直接汇报不支持，严禁直接执行。恢复类工具需人工批准后执行。",
     "skills": ["wefix-ads-cluster-scaling", "wefix-ads-docker-cluster-instance-reboot", "wefix-ads-docker-cluster-reboot", "wefix-alb-router-limit", "wefix-alb-router-ulimit"],
     "mcp_tools": ["__FILL:recover server 工具名__"],
     "max_iters": 20, "tool_result_limit": 24000},
    {"key": "apm", "label": "APM", "role": "你是 SRE 微服务资源排查子 Agent。必须加载 apm-analysis 技能，严格按照技能中的执行路由和约束执行。",
     "skills": ["apm-analysis"], "mcp_tools": ["__FILL:apm-server 工具名__"],
     "max_iters": 40, "tool_result_limit": 24000},
    {"key": "db", "label": "数据库", "role": "你是 SRE 数据库资源排查子 Agent。必须加载 db-analysis 技能，严格按照技能中的执行路由和约束执行。",
     "skills": ["db-analysis"], "mcp_tools": ["__FILL:db-server 工具名__"],
     "max_iters": 40, "tool_result_limit": 24000},
    {"key": "redis", "label": "Redis", "role": "你是 SRE Redis 资源排查子 Agent。必须加载 redis-analysis 技能，严格按照技能中的执行路由和约束执行。",
     "skills": ["redis-analysis"], "mcp_tools": ["__FILL:redis-server 工具名__"],
     "max_iters": 40, "tool_result_limit": 24000},
    {"key": "change", "label": "变更", "role": "你是 SRE 变更查询子 Agent（只读）。必须加载 change-query 技能，严格按照技能中的执行路由和约束执行。变更信息仅作为参考条件，可能获取不到具体变更内容，不对变更是否为根因做判断。",
     "skills": ["change-query"], "mcp_tools": ["__FILL:change-server 工具名__"],
     "max_iters": 40, "tool_result_limit": 24000},
    {"key": "grafana", "label": "Grafana 看板", "role": "你是 SRE Grafana 看板管理子 Agent。必须加载 grafana-ops 技能，严格按照技能中的执行路由和约束执行。必须严格限制 SQL 的查询数据量和性能。严禁删除看板和配置。创建看板：构建完整面板 JSON（数据源/SQL/面板类型）后调 grafana_create_dashboard 一次性完成，禁止先建空白再补面板。更新看板：先 grafana_get_dashboard 确认修改点再 grafana_update_dashboard。同名看板已存在（412）时向用户确认是否覆盖，禁止自行决定。完成后汇报看板访问链接和关键信息。",
     "skills": ["grafana-ops"], "mcp_tools": ["__FILL:grafana-server 工具名__"],
     "max_iters": 40, "tool_result_limit": 24000}
  ],
  "default_llm": {"provider": "platform", "model": "glm-5.1"}
}
```

## 四、落库路径

### 路径 A：管理台（适合小改，不能加/删角色）

模板编辑器现支持逐角色改 `role / skills / mcp_tools / max_iters / tool_result_limit`（67714e7），
但**没有新增/删除角色入口**，也改不了 `main.max_children`——整套 9 角色迁移用路径 B。

### 路径 B：admin API 三步（推荐，完整替换 content_json）

把第三节 JSON 存成 `content.json`，外面包一层 `{"content_json": {...}}` 存为 `body.json`，然后（内网把
`127.0.0.1:18082` 换成后端地址，管理员身份按内网 IAM 头）：

```bash
H='-H "Content-Type: application/json" -H "X-OpenOps-Mock-User: admin" -H "X-OpenOps-Mock-Name: Admin"'

# 1. 拿 template_id（找 template_key=sensai_fast_recovery 那行）
curl -s -H "X-OpenOps-Mock-User: admin" -H "X-OpenOps-Mock-Name: Admin" \
  http://127.0.0.1:18082/api/openops/v1/admin/templates

# 2. 存草稿（返回 template_version_id；校验失败会点名哪些工具未 allowed）
curl -s -X POST -H "Content-Type: application/json" \
  -H "X-OpenOps-Mock-User: admin" -H "X-OpenOps-Mock-Name: Admin" \
  http://127.0.0.1:18082/api/openops/v1/admin/templates/<template_id>/versions \
  -d @body.json

# 3. 发布
curl -s -X POST -H "X-OpenOps-Mock-User: admin" -H "X-OpenOps-Mock-Name: Admin" \
  "http://127.0.0.1:18082/api/openops/v1/admin/template-versions/<template_version_id>:publish"
```

发布后**存量实例不用重建**：下一次任务边界自动检测模板升级并派生新配置版本（结转用户 overlay 与资产绑定，
`config.version.derived` 审计 + changed_notice 提示），新画像即刻生效。

### 不建议的路径

- **seed.py**：只在空库首次启动播种（seed.py:68 以模板存在为标志整体跳过），对已播种的内网库无效；
  且 seed 里的工具名是环境无关的 demo 工具，不要把内网专属工具名硬编码进去。
- **直接 UPDATE 版本行 SQL**：发布后的版本不可变是审计口径（版本链可追溯），直改绕过校验与审计事件；
  真要 DBA 手工操作也应是"INSERT 新版本行 + 更新模板 active 指针"，而这正是路径 B 两个接口做的事，还带校验。
