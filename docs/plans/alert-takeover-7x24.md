# 7x24 告警接管 — 实施计划

> 状态：设计定稿（2026-07-30），未实施。完整设计叙述（数据模型列级说明、契约字段表、旅程、风险论证）见 Obsidian `学习/OpenOps/39-7x24告警接管设计.md`；本文是仓库侧的实施视角：决策记录 + 改动清单 + 分步验证。UI 规格来源：claude.ai/design 原型「SRE Agent初始化原型设计」。

## 1. 背景与十个地基决策

现状是人拉模式（告警面板拼 `?q=` 外链跳入，见 `frontend/src/lib/autosend.ts` 与 `backend/src/api/routers/identity.py` 白名单端点）；本功能升级为机推模式：轮询接入告警 → 命中实例订阅规则 → 附着聚合 → 有界排队 → 自动建诊断会话。管理层两个硬约束：防告警风暴（上游降噪不可依赖）、模型 API 并发受限必须排队。

| # | 决策 | 结论 |
|---|---|---|
| 1 | 告警源 | 公司已有告警平台，API 契约由我们定义、对方实现（umodel 对接同模式） |
| 2 | 触发语义 | 全自动：命中即入队，排队后自动建 run 诊断 |
| 3 | 规则归属 | OpenOps 自建匹配规则（上游只供原始告警）；Phase1 快速配置=类型×级别×模板，自定义条件归 Phase2 |
| 4 | 量级 | 中档：风暴百条/分钟、模型并发 3~10 → 去重+聚合+有界队列+溢出丢弃 |
| 5 | 接入方式 | Phase1 轮询拉增量（背压天然、无需入站鉴权）；webhook 契约先行、Phase2 实现 |
| 6 | 会话展示 | 复用聊天工作台 `/agent-runs/:runId` 可追问；会话历史按 `entry_source` 过滤告警 run |
| 7 | 夜间身份 | Phase1 降级：scope 用实例最近快照、需登录态工具失败留痕；B9 服务态凭证立项推进 |
| 8 | 结论回流 | ack/annotate/close 回写契约先行、Phase2 实现 |
| 9 | 清单落点 | 侧边栏一级导航「告警清单」全宽表格页（原型形态）；ActivityRail 方案否决（per-run 重挂） |
| 10 | 枚举口径 | severity `fatal/critical/warning/info`；category `MySQL/PGSQL/OpenGauss/Redis/ADS Docker/K8s/Nginx…`；agent_result `recovered/escalated/failed`；用户评价 positive/negative/neutral |

## 2. 改动总览

**新增（切片，零 core 耦合）**

- `backend/src/alerts/`：`__init__.py`(facade: routers/start/stop/ensure_defaults) / `router.py` / `service.py` / `ingest.py` / `matcher.py` / `dispatcher.py` / `repository.py` / `README.md`
- `backend/src/infra/external/alert_platform_{client,real,mock}.py`（`OPENOPS_ALERT=mock|real` 三件套，real 走固定绝对 BASE_URL + Bearer `OPENOPS_ALERT_TOKEN`）
- `backend/sql/slices/alerts.sql`：六表 `sre_alert_rule` / `sre_alert_event` / `sre_alert_incident` / `sre_alert_incident_event` / `sre_alert_subscription` / `sre_alert_pull_state`（DB 即队列：incident_state 驱动，六态 queued/diagnosing/completed/failed/skipped/ignored）
- `backend/sql/migrate-2026-07-30-alert-entry-source.sql`：`sre_agent_run.entry_source`、`sre_task_state.task_origin` 两列（幂等，core.sql 同步改）
- `backend/tests/alerts/`：切片测试
- `frontend/src/alerts/`：`entry.tsx`(lazy+导航常量) / `types.ts` / `api.ts`(real+mock 双实现) / `mockData.ts` / `AlertsPage.tsx`(清单表格+SlideIn 详情抽屉) / `AlertRulesPane.tsx`(「设置 → 告警接管配置」/settings/alerts；配置弹窗为**两步向导**——第一步 名称/类型（**多选**，任一命中即匹配；match_json.categories[]，存量单值兼容读）/级别/提示词（"/" 呼出用户 skill 插入，后端 dispatcher 提取首个 /token 作 skill_hint），第二步 近3/7天命中预览确认；监控策略勾选 UI 已移除，新建落空数组=该类型全部)
- `frontend/e2e/alerts.spec.ts`

**core 侵入点封闭清单（除此零 core 改动）**

1. `app/run_state_service.py` start_task 三分支 ≈20 行：origin 分池闸（alert 不占 per_user=2）、scope 快照降级（仅 origin=alert）、closed 告警 run 定向 reopen（先 ensure_user_container 补 refcount）
2. `runtime/task_registry.py`：`TaskState.origin` + `running_count(uid, origin=None)`
3. `infra/repositories/task_states.py`：落 `task_origin`；`count_running` 排除 alert
4. `infra/repositories/runs.py`：`set_entry_source()` / `reopen_alert_run()`
5. `app/scope_service.py`：`resolve_from_last_snapshot()`（插入新快照行 compute_reason=alert_snapshot_fallback，ctx 带 degraded）
6. `run_state_service.list_runs`：过滤 `entry_source='alert'` 一行
7. `domain/errors.py`：`ALERT_INCIDENT_STATE_INVALID: 409`
8. `main.py`：import + include_router + lifespan start/stop 各一行
9. `pyproject.toml` include 加 `"alerts*"`；`tests/test_ddl.py` 表数 27→33、task_state 列数 54→55
10. 启动横幅加 `alert=… pull=…s`；`check-net.py` 自检段；`backend/docs/EXTERNAL-INTEGRATION.md` 开关表行
11. 前端注册三处：`App.tsx` 展开 `{alertsRoutes}`、`Sidebar.tsx` userNav + activeKey、`settings/SettingsPage.tsx` Tab 加 `"alerts"`（支持 `?tab=alerts` 深链）

**HTTP StartTaskRequest 不加 origin 字段**（防伪造，origin 走内部 dataclass getattr 缝隙，沿 `skill_hint` 先例）——配守卫测试。

## 3. 关键机制速览（实施时按此口径，论证见设计文档 §6–§8）

- **管线**：poller（`OPENOPS_ALERT_PULL_INTERVAL_S=0` 默认关；照 sandbox_admin 循环范式）→ ingest（指纹 upsert 去重 300s 窗 → 内存匹配 enabled 规则 → 同 group_key 附着 / 冷却 900s / 上限判定 → incident(queued) 或 skipped）→ dispatcher（2~5s 短询 + Event kick；并发闸默认 2 热更新；条件 UPDATE 抢占 queued→diagnosing）→ create_run（幂等键 `alert-{incident}-r{n}`）+ start_task(origin=alert) → `await wait_for(shield(st.orchestrator), 900s)` → 收割 result_summary（st.rca.conclusion → transcript 末条 assistant → completed message）→ completed/failed（失败退避 60s 重试 1 次）。
- **按 prompt 分单**（2026-08-19 拍板：提示词不同的命中规则分开处理，支持 A/B 对比）：同实例多规则命中同一告警时，按归一 prompt（空白≡系统默认；`matcher.effective_prompt`）分组，**每组各建一单各自诊断**，归一相同合并一单；`sre_alert_incident.prompt_group`（归一 prompt 哈希前 12 位）参与附着/冷却/窗口补路由判定，NULL=分单上线前存量单按通配兼容（旧未完结单整体附着、旧完结单冷却全组）。规则加载 `list_enabled_rules` 加 ORDER BY（最老优先）消除「哪条生效」的行序漂移。**计数口径变化**：queued/skipped/stale/attached 计数从「按告警×实例」变「按告警×实例×prompt 组」；每多一个 prompt 组=多一次诊断 run+一条完成通知（文案带命中规则名以区分姊妹单），实例上限 10/全局 50 为总量闸被组共享。已明示不做：组级原子 admission（触顶时可能一组入队一组留痕，留痕可 retry）、派发期组间去重（排队期改 prompt 可能出现两组同 prompt 的重复诊断）。
- **重启收敛**：alerts.start() 在 converge_orphan_tasks 之后跑 converge_incidents()：diagnosing+快照 completed→补收割；interrupted 未超龄(3600s)→回 queued 复用同一 run；否则 failed(stale)。queued 零处理。
- **防风暴七层**：拉取背压(200×5批) → 指纹去重 → 附着聚合 → 组冷却 → 有界队列(实例 10/全局 50，低严重度先丢，skipped 留痕) → 并发闸(**峰值模型并发=闸×(1+max_children=3)，闸 2 即 8，上线初期设 1**) → 三级开关(DB alert_enabled ＞ 实例订阅 ＞ 规则)。
- **容器口径**（已核实）：容器按用户一只、refcount 计 run——同 owner 多告警 run 共享，诊断完不 close，30min idle 回收兜底；`alert_run_idle_ttl_minutes`(默认 0=跟随) 可提前关。
- **runtime_config 新域 `alert`** 12 个键（enabled/并发闸/队列上限/实例上限/去重窗/冷却/诊断超时/重试×2/requeue 超龄/idle TTL/批量），切片 ensure_defaults 只补缺失键。

## 4. 内部 API（`/api/openops/v1`，ok(data) 信封、冒号动作、写 DTO 带 client_request_id）

- 用户侧：`GET/POST /alerts/rules`（POST 支持批量）、`/alerts/rules/{id}:update|:delete`、`GET /alerts/rule-templates`、`GET /alerts/subscription` + `:update`、`GET /alerts/incidents`（instance/state/severity 分页筛选）、`GET /alerts/incidents/{id}`、`/alerts/incidents/{id}:ignore|:retry|:feedback`、`GET /alerts/summary`
- 管理侧：`GET /admin/alerts/overview`、`GET /admin/alerts/config` + `:update`（reason 必填）、`POST /admin/alerts:pull`
- Phase2 入站：`POST /alerts:ingest`（HMAC webhook）

## 5. 对外契约（交付告警平台团队；Phase1 仅依赖 Pull 两端点）

- **Pull**：`GET /openapi/alerts/v1/changes?cursor&limit`——不透明游标按 (updated_at, alert_id) 单调、恒返 next_cursor、410+earliest_cursor 重对账、update-log at-least-once、空 cursor 从「现在」开始；字段含 alert_id/fingerprint/status/severity 四档映射/category/app_id(内网 APPID 口径)/labels/annotations/时间戳/source。详情 `GET /alerts/{alert_id}`。
- **Webhook（Phase2）**：HMAC-SHA256 + 时间戳 ±300s + Delivery-Id 幂等、批量 ≤100、退避五次进死信；稳定后 pull 降频 5min 对账。
- **回写（Phase2）**：`:ack` / `:annotate`（结论+会话链接）/ `:close`（fixed|auto_recovered|false_positive），client_request_id 幂等。
- 通用：服务态 Bearer（轮换重叠 ≥7 天）、429 带 Retry-After、≥10rps、数据 ≥30 天、游标回放 ≥7 天、RFC3339。

## 6. 分步实施与验证（每步独立可验证）

**后端**

| 步 | 内容 | 验证 |
|---|---|---|
| B0 | slices/alerts.sql + migrate + test_ddl 基线 33/55 | 切片 DDL 测试（对象名≤30/无外键/全列注释）+ core test_ddl 绿 |
| B1 | alert_platform 三件套（mock 带 `_inject()/_reset()`）+ 横幅 + check-net | mock 注入/翻页/游标单测；real 用 stub-httpx 照 test_external_real |
| B2 | repository + rules/subscription/rule-templates API | CRUD/越权 403/幂等重放/同名 409 |
| B3 | matcher + ingest | 匹配矩阵（severity/category/appid/label/keyword/组合/禁用）；注入→event→incident 创建/附着→overflow skipped |
| B4 | core 四处小改（origin 闸/task_origin/scope fallback/reopen） | 双池互不挤占；scope 降级（omodel_mock 置败+预置快照→alert 成功且 degraded、user 照抛）；test_run_task/test_ask 回归；守卫测试断言 StartTaskRequest 无 origin |
| B5 | dispatcher（worker/收割/超时/重试/converge） | E2E：`_inject → ingest.run_once() → incident(queued) → dispatcher.run_once() → diagnosing+entry_source/task_origin='alert' → 处理 ASK（approve 或免审批专测模板）→ completed+result_summary 非空+GET /agent-runs 不含该 run`；converge 两分支 |
| B6 | incidents/summary/feedback API + list_runs 过滤 + admin | test_incidents_api；/agent-runs 列表不含、/state 可读 |
| B7 | 文档：alerts/README、对外契约文档、EXTERNAL-INTEGRATION、发布说明（夜间降级+并发换算式） | 评审 |

**前端（mock 全链路可先行）**

| 步 | 内容 | 验证 |
|---|---|---|
| F-A | 切片脚手架 + 注册三处 + AlertsPage + AlertRulesPane（全 mock） | `npm run build` 独立 chunk、主 chunk 不涨；mock 下三落点可走通 |
| F-B | 联调：rules CRUD → incidents/summary → 动作 → entry_source 过滤验收 | 真实告警建 run 后侧栏不出现、`/agent-runs/:id` 直开、诊断完成 run 仍 active 可追问 |
| F-C | Playwright `e2e/alerts.spec.ts`（testid：alerts-incident-row 等，文案避开 smoke 精确串） | 三态行/风暴 ×N 徽标/会话跳转 `run_alert_*`/`?tab=alerts` 直达/快速配置预览/conversation-row 仍 3/smoke 全绿 |

**环境坑位**：worktree 跑 pytest 必须钉 `PYTHONPATH`；并行会话按会话建独立库 + `OPENOPS_DATABASE_URL`；前端 mock 必设 `VITE_OPENOPS_API_MODE=mock`。本地全链路演示：`OPENOPS_ALERT=mock OPENOPS_ALERT_PULL_INTERVAL_S=15` 起后端 → 设置页配规则 → 清单看流转 → 点开会话追问。

## 7. 残余风险（上线评审必读）

1. 模型并发预算贴下限：闸 2×扇出 4=峰值 8，初期设 1 观测；跨池全局模型信号量是平台级 Phase2 课题（现状全仓无模型级并发控制）。
2. 夜间降级是已知限制：scope 走快照、需登录态 MCP 工具失败留痕；B9 服务态凭证为撤降级前提，需正式立项。
3. owner 容器常驻：占位数=启用接管的 owner 数（26 上限），admin overview 展示供容量规划。
4. 多实例命中同一告警各自诊断，放大被 per-instance 上限兜住；2026-08-19 按 prompt 分单后放大系数升为「实例数×prompt 组数」，上限仍为总量闸但实际容量被组均摊——上线评审重述。另注意 DEFAULT_RULE_PROMPT 文案升级会使「拷贝默认文案」与「留空」的规则归一值分裂成两组（视为「改了提示词即重测」语义的自然延伸）。
5. 对方排期：Pull 两端点是 Phase1 唯一硬依赖，契约文档先行交付；mock 保证我方不被阻塞。

## 2026-08-09 内网契约切换（决策追加）

1. **上游切内网真实契约**：Kafka 消息体（29.11）经 `infra/external/alert_inet_contract.map_kafka_alarm` 译成内部 AlertDTO（双格式缝：探测 alarmId/alarmCode 键，内部形状照旧透传）；映射词表与 R1–R12 联调清单见 `backend/docs/ALERT-PLATFORM-CONTRACT.md` v3。
2. **app_id ← appIdList**（元素=omodel projectId，混合口径纯串比较）；首元素参与本地匹配，全列表存 annotations.app_id_list。
3. **规则预览切平台历史接口**（29.10 alarm_list，`GET /alerts/history-preview`）：真全量历史（未命中/未接管可见），平台故障自动降级本地库（source 标记）；scope 快照 projectIds 收窄到该 Agent 管的应用。
4. **类别口径改内网 moType**：PGSQL→PostgreSQL、ADS Docker→Docker（存量迁移 `sql/migrate-2026-08-09-alert-category-motype.sql`，含可选 strategies 清空段——metricName 与本地策略名词表不齐，R10）。
