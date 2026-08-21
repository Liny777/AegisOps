# alerts 垂直切片：7x24 告警接管（v2.1）

> 设计定稿：`docs/plans/alert-takeover-7x24.md`（仓内计划 v2.1）；对外契约：`backend/docs/ALERT-PLATFORM-CONTRACT.md`（Kafka 版）。
> 切片铁律与 studio 相同：core 只准 `import alerts` 调 facade；切片对 core 表只读
> （run/task 写操作走 `infra.repositories.runs / task_states` 公开函数）。

## 数据流

```
告警平台 Kafka topic（key=alert_id, value=AlertDTO JSON）
  --real 档: kafka_source.consume_loop（getmany → ingest_batch → 手动 commit；先落库后提交）-->
  --mock 档: 内存变更流 + 轮询 run_once（OPENOPS_ALERT_PULL_INTERVAL_S，0=关，admin :pull 手动）-->
ingest.ingest_batch()（传输无关入口）
  ① fingerprint 去重（dedup_window_s 内同指纹仅 seen_count++；窗口按 last_seen 滚动——
     持续重发永不过窗，故窗口内对「命中但从未建过单」的实例补路由，2026-08-19 多用户修复）
  ② matcher 内存匹配（白名单+规则开；v2 规则=类型单值 ∧ 级别 ∧ 命中勾选策略集，空集=该类型全部；
     owner_only 维 2026-08-21：labels.alarm_owner token 化大小写不敏感比对规则属主，缺失 fail-closed）
  ③ 附着式聚合（group_key=instance|appid|title 有未完结单 → 附着，severity 取高）
  ④ 组冷却（group_cooldown_s 内同组不建新单）
  ⑤ 有界队列（实例/全局上限 → skipped 留痕；高 severity 可踢队尾低者）
  → sre_alert_incident(queued)  ——DB 即队列，重启天然存活
dispatcher.dispatch_once()（并发闸 alert_max_concurrent_diagnosis，条件 UPDATE 抢占）
  worker: create_run(owner, entry_source='alert', 容器=共享告警沙箱) → start_task(origin='alert',
          提示词=规则 prompt 或 DEFAULT_RULE_PROMPT) → await orchestrator → 收割 → completed/failed
```

## 两张页面的 API 面

- **配置页**（设置页第三 tab）：`GET/POST /alerts/rules`（v2 一弹窗一规则：name/category 单选/
  severities 三档/strategies 勾选集/prompt）、`:update`/`:delete`、**`POST /alerts/rules:batch`**
  （批量启用/禁用/删除）、`GET /alerts/rule-templates`（3 类 9 条 + default_prompt）。
- **接管清单**（全量告警视角，v2 需求 1.3）：`GET /alerts/events`——事件 LEFT JOIN LATERAL 本人最新
  聚合单；普通用户可见=本人接管 ∪（未接管 ∧ appid∈所选 Agent scope 快照）；三值投影
  告警状态（未分派/已分派/已关闭）与接管状态（未接管/处理中/已完成，skipped/ignored 归未接管）；
  `GET /admin/alerts/events?user_id=`（管理员全量；user_id=按接管人过滤+投影，2026-08-19 语义
  修正，原为纯投影不收窄行集）。incidents 系列端点保留（动作与详情）。

## 与 core 的接缝（封闭清单，全部有测试守着）

| 接缝 | 位置 | 守卫测试 |
|---|---|---|
| 并发分池 `TaskState.origin`（HTTP DTO 无此字段） | start_task / task_registry / task_states | test_core_seams + dispatch e2e 双向不挤占 |
| **共享告警沙箱** `TaskState.sandbox_uid` 单一事实源（alert=`sys-alert-sandbox`，追问=本人） | start_task / runtime 5 处寻址 / `_AlertCreateRunReq.sandbox_owner` 缝隙 | test_core_seams（DTO 无 sandbox_owner + 反查维度）+ e2e 双账断言 |
| **双键幂等释放**（close/delete/idle 回收对 alert run 释放 owner+共享键两本账） | run_state_service `_sandbox_release_keys` ×3 调用点 | dispatch e2e：close 后两账归零 |
| 销毁反查 `running_by_sandbox`（告警任务 user_id≠容器键） | sandbox_admin_service 强制销毁 | test_core_seams |
| scope 夜间降级 / 追问自动复开 / 会话历史过滤 entry_source | start_task / list_runs / runs repo | e2e + 联调 |
| **scope 快照兜底 revision 校验**（2026-08-19：范围变更后旧快照 fail-closed；写路径 update_workspace 推进引用实例 revision + 失效缓存） | ingest._instance_scope / scope_service.resolve_from_last_snapshot / workspace_service | test_ingest_scope_filter + test_init 闭环用例 |
| lifespan：converge 恒执行；real=Kafka 消费，mock=轮询 | main.py + dispatcher.start_background | e2e 重启收敛 + test_kafka_source |

## 运行旋钮

- env：`OPENOPS_ALERT=mock|real`。real=Kafka（`pip install -e ".[kafka]"`）：
  `OPENOPS_ALERT_KAFKA_BOOTSTRAP/_TOPIC`（必，缺配 fail-closed）`/_GROUP/_USERNAME/_PASSWORD/_SASL/_SECURITY_PROTOCOL`；
  详情/回写 HTTP：`OPENOPS_ALERT_BASE_URL` + `OPENOPS_ALERT_TOKEN`。mock：`OPENOPS_ALERT_PULL_INTERVAL_S`
  （0=关）、`OPENOPS_ALERT_MOCK_SEED=1` 播种。横幅字段 `alert=/alert_kafka=/alert_pull=/alert_token=`。
- DB 热更新（域 `alert`，admin `/admin/alerts/config:update`，reason 必填）：全量键见 `service.CONFIG_DEFAULTS`（管理台 /admin/alerts 可视化编辑；含排队超时/老化步长）；
  `alert_enabled=false` 全局热停（Kafka 消费暂停不 commit，恢复续传）。
- **并发放大换算式**：模型峰值并发 = `alert_max_concurrent_diagnosis` × (1 + max_children=3)。默认 2 ⇒ 峰值 8，
  上线初期建议设 1。

## 已知限制（发布说明必带）

1. **共享告警沙箱跨用户执行环境**（决策⑥已接受）：全员诊断共容器，跨用户文件系统互见；缓解=
   诊断只读口径 + 空闲回收即定期重建清残留 + deny 前缀。安全评审材料需明示。
2. **追问切回用户容器**（决策⑦）：同 run 诊断期文件在共享沙箱、追问看不到——DEFAULT_RULE_PROMPT
   已要求「关键结论与证据写入对话正文」；追问的 lazy ensure 在容量满时 429 打在追问动作上。
3. **纯 Kafka 无带外对账**：漏消费/offset 事故唯一回补=retention（≥7d 契约硬性）内按时间戳 seek 重放。
4. **后台出站鉴权=纯 IAM 机机态（2026-08-16 定案）**：人对话带用户 cookie（用户态），告警自接管
   等后台链路带 j2c_utils 的 IAM token（#54 六注入点）；对端 omodel/MCP 双鉴权兼容——**有 cookie
   优先 cookie，无 cookie 有 IAM token 也通过**。部署注意：别配静态 OPENOPS_CONSOLE_COOKIE/
   OPENOPS_MCPREGISTRY_COOKIE（会被 cookie 优先策略选中且必过期→1001）。对端双鉴权上线前，
   后台 MCP 发现失败会在日志/活动栏现形（TOOL_DISCOVERY_EMPTY）；scope 侧另有快照兜底
   （omodel_request_id=snapshot-fallback，ctx degraded=true）。
5. **终态通知 v3**（2026-08-19）：completed 与 failed 均 WeLink 通知 owner（`SEND_WELINK_MESSAGE_URL` +
   `OPENOPS_WEB_BASE_URL`；未配=不发）。completed 结论=接管结果+根因结论（只取 rca.conclusion 截 200 字，
   transcript 兜底文本不进通知）；failed 结论=原因中文（reason_text 词表：诊断超时/执行失败等），
   无 run 时链接退化清单深链 /alerts/{incident_id}。**queue_expired/skipped 不通知**（拍板：防
   批量轰炸，清单可见）。结论经 `_notify_brief` 清洗：剥 Markdown 标记+按句边界截断（2026-08-19
   内网样本反馈）。行格式（2026-08-20 验收）：告警标题/告警开始时间/命中策略/接管结果与结论
   **分行**。全链日志：grep `[alerts][notify]`（含 outcome=），logger `openops.welink` 三态。
6. agent_result（已恢复/已升级）=模型在诊断板 update_diagnosis_board 的 **verdict** 提交
   （recovered/escalated，契约枚举校验；2026-08-19 修——此前误读派生 status 恒 NULL），未提交
   显「—」详见会话；converge 补收割同样取 task 快照 rca_json。resolved 告警只落库不驱动状态机；
   `alert_run_idle_ttl_minutes` 旋钮预留未实现（平台 30min idle 回收兜底）。

## 本地全链路演示

```bash
OPENOPS_ALERT=mock OPENOPS_ALERT_MOCK_SEED=1 OPENOPS_ALERT_PULL_INTERVAL_S=15 python run.py
# 设置页(告警接管 tab)建规则(类型 MySQL/PostgreSQL 任选+默认全选策略) → 15s 内清单出现 → 点「查看处理会话」追问
# 手动驱动：POST /api/openops/v1/admin/alerts:pull → :dispatch → GET /alerts/events
# 坑：mock 种子只播一次且游标持久化；重放需拨回 sre_alert_pull_state 游标并热调 alert_dedup_window_s=0
# 单条定位：OPENOPS_ALERT_TRACE=<alarmId或应用ID> 后 grep "[alerts][trace]" 看决策链（消费到→匹配→范围→去向）
# 大流量：批摘要 30s 聚合一行（grep "\[alerts\]\[kafka\]"）；追赶期 stale 计数=陈旧留痕跳过（清单「延迟放弃」）；追不上流量先调批量——
#   部署态 env 钉死 OPENOPS_ALERT_PULL_BATCH_LIMIT=1000（最高优先级），或 admin config:update 运行时热调（env 未配时生效）
```

## 测试

`tests/alerts/`（58 用例）：DDL 守护 / 客户端 mock 语义 / Kafka 消费单测（假 consumer：commit 次序/
坏消息/热停）/ 规则 v2 CRUD+批量 / 匹配矩阵（含 strategies 维度）/ 摄入五层削峰 / 事件清单可见性矩阵 /
core 接缝守卫 / 派发 E2E（提示词注入、双账、双池、重启收敛）。
运行：`cd backend && PYTHONPATH=$PWD/src python3 -m pytest tests/alerts/ -q`（涉库用例需 PG）。
