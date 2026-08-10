-- OpenOps 垂直切片 DDL：7x24 告警接管（alerts）。
--
-- 发布顺序：migrate-2026-07-30-alert-entry-source.sql（core 两列）-> 本文件 -> 更新后的
-- openops_v1_core.sql -> 后端 -> 前端。幂等、无损、可重复执行。
-- `OPENOPS_PG_SCHEMA` 用户先切 search_path 再执行，与其余 migrate 文件同规矩。
--
-- 设计要点（详见 docs/plans/alert-takeover-7x24.md 与 Obsidian 39 号设计文档）：
-- - sre_alert_incident 即队列（DB-as-queue）：incident_state 驱动 dispatcher 消费，重启天然存活；
-- - sre_alert_event 为原始告警去重仓，(source_key, fingerprint) 唯一，30 天 expire_at 硬删；
-- - 规则匹配在应用层内存执行，match_json 只存声明，不做 SQL 表达式反查；
-- - 六表均无外键（与 core 同规矩），跨表一致性由应用层保证。

-- 1) 匹配规则：用户在 Agent 实例设置页定义（Phase1 快速配置按模板批量生成）
CREATE TABLE IF NOT EXISTS sre_alert_rule (
  alert_rule_id uuid NOT NULL PRIMARY KEY,
  agent_team_instance_id uuid NOT NULL,
  owner_user_id text NOT NULL,
  rule_name text NOT NULL,
  description text NOT NULL DEFAULT '',
  rule_source text NOT NULL DEFAULT 'custom',
  match_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  prompt text NOT NULL DEFAULT '',
  enabled boolean NOT NULL DEFAULT true,
  priority integer NOT NULL DEFAULT 0,
  creation_date timestamptz NOT NULL DEFAULT now(),
  last_update_date timestamptz NOT NULL DEFAULT now(),
  created_by text NOT NULL DEFAULT 'system',
  last_updated_by text NOT NULL DEFAULT 'system',
  deleted_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_rule_inst_name
  ON sre_alert_rule (agent_team_instance_id, rule_name) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_alert_rule_inst
  ON sre_alert_rule (agent_team_instance_id) WHERE deleted_at IS NULL;

COMMENT ON TABLE sre_alert_rule IS '告警接管匹配规则（实例维度）：Phase1 快速配置按内置模板批量生成，匹配在应用层内存执行';
COMMENT ON COLUMN sre_alert_rule.alert_rule_id IS '规则主键';
COMMENT ON COLUMN sre_alert_rule.agent_team_instance_id IS '所属 AgentTeam 实例 ID（规则归属实例）';
COMMENT ON COLUMN sre_alert_rule.owner_user_id IS '实例 owner 工号（冗余，匹配反查免 join；owner 变更不追溯）';
COMMENT ON COLUMN sre_alert_rule.rule_name IS '规则名（实例内唯一，未删除范围）';
COMMENT ON COLUMN sre_alert_rule.description IS '规则描述（模板生成时抄模板描述）';
COMMENT ON COLUMN sre_alert_rule.rule_source IS 'template=快速配置模板生成 / custom=自定义（Phase2 条件编辑器）';
COMMENT ON COLUMN sre_alert_rule.match_json IS '匹配声明：{categories:["MySQL",...](多选,任一命中;存量单值 category 兼容读),severities:[],strategies:[监控策略名],appids:[],label_selectors:{k:v},keywords:[],keyword_mode:any|all}；维度间 AND、维度内 OR，空数组/缺省=该维度不限（strategies 空=该类型全部策略）';
COMMENT ON COLUMN sre_alert_rule.prompt IS '诊断提示词（配置弹窗可编辑；空=用系统默认 DEFAULT_RULE_PROMPT），派发建 task 时注入「要求」段';
COMMENT ON COLUMN sre_alert_rule.enabled IS '规则启停（实例订阅总开关之下的第三级开关）';
COMMENT ON COLUMN sre_alert_rule.priority IS '预留：同实例多规则命中时的展示排序，当前不参与调度';
COMMENT ON COLUMN sre_alert_rule.creation_date IS '创建时间';
COMMENT ON COLUMN sre_alert_rule.last_update_date IS '最后更新时间';
COMMENT ON COLUMN sre_alert_rule.created_by IS '创建人工号';
COMMENT ON COLUMN sre_alert_rule.last_updated_by IS '最后更新人工号';
COMMENT ON COLUMN sre_alert_rule.deleted_at IS '软删除时间，NULL 表示未删除';

-- 2) 原始告警事件：fingerprint 去重仓（30 天到期硬删，对齐审计保留策略）
CREATE TABLE IF NOT EXISTS sre_alert_event (
  alert_event_id uuid NOT NULL PRIMARY KEY,
  source_key text NOT NULL DEFAULT 'default',
  external_alert_id text,
  fingerprint text NOT NULL,
  alert_status text NOT NULL DEFAULT 'firing',
  severity text NOT NULL DEFAULT 'warning',
  category text NOT NULL DEFAULT '',
  alert_name text NOT NULL DEFAULT '',
  alert_object text NOT NULL DEFAULT '',
  strategy_name text NOT NULL DEFAULT '',
  appid text,
  labels_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  annotations_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  starts_at timestamptz,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  seen_count integer NOT NULL DEFAULT 1,
  payload_json jsonb,
  creation_date timestamptz NOT NULL DEFAULT now(),
  last_update_date timestamptz NOT NULL DEFAULT now(),
  created_by text NOT NULL DEFAULT 'system',
  last_updated_by text NOT NULL DEFAULT 'system',
  expire_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_event_fp
  ON sre_alert_event (source_key, fingerprint);
CREATE INDEX IF NOT EXISTS ix_alert_event_seen
  ON sre_alert_event (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS ix_alert_event_expire
  ON sre_alert_event (expire_at);

COMMENT ON TABLE sre_alert_event IS '原始告警事件（去重仓）：(source_key,fingerprint) 唯一 upsert；去重窗口内同指纹仅 seen_count++；到期硬删（expire_at，默认 30 天）';
COMMENT ON COLUMN sre_alert_event.alert_event_id IS '事件主键';
COMMENT ON COLUMN sre_alert_event.source_key IS '告警源标识（多源预留，Phase1 恒 default）';
COMMENT ON COLUMN sre_alert_event.external_alert_id IS '对端告警 ID（Phase2 结论回写用）';
COMMENT ON COLUMN sre_alert_event.fingerprint IS '同类事件指纹：对端提供；缺省时 ingest 按 sha256(source+alert_name+规范化labels) 计算';
COMMENT ON COLUMN sre_alert_event.alert_status IS 'firing / resolved（resolved Phase1 只落库不驱动状态机）';
COMMENT ON COLUMN sre_alert_event.severity IS '严重度：fatal(致命)/critical(严重)/warning(普通)/info(提示)，对端映射到此四档';
COMMENT ON COLUMN sre_alert_event.category IS '告警类型（开放枚举：MySQL/PostgreSQL/OpenGauss/Redis/Docker/K8s/Nginx…），一级匹配与展示维度';
COMMENT ON COLUMN sre_alert_event.alert_name IS '告警名称/标题（截断至 512B）';
COMMENT ON COLUMN sre_alert_event.alert_object IS '告警对象（实例/主机/集群标识，如 mysql-prod-03）——清单列与搜索维度';
COMMENT ON COLUMN sre_alert_event.strategy_name IS '触发本告警的监控策略名（对端字段）——规则 strategies 维度的匹配键';
COMMENT ON COLUMN sre_alert_event.appid IS '归属应用（内网 APPID 口径；对端无法归属时为空）';
COMMENT ON COLUMN sre_alert_event.labels_json IS '标签键值（service/idc/env 等，含对端原始 severity 备查）';
COMMENT ON COLUMN sre_alert_event.annotations_json IS '注解（description/runbook_url/dashboard_url 等大文本，截断）';
COMMENT ON COLUMN sre_alert_event.starts_at IS '对端首次触发时间';
COMMENT ON COLUMN sre_alert_event.first_seen_at IS 'OpenOps 首次接收时间';
COMMENT ON COLUMN sre_alert_event.last_seen_at IS 'OpenOps 最近接收时间（去重窗口判定基准）';
COMMENT ON COLUMN sre_alert_event.seen_count IS '累计接收次数（去重窗口内重复只加计数）';
COMMENT ON COLUMN sre_alert_event.payload_json IS '对端原文快照（脱敏截断后）';
COMMENT ON COLUMN sre_alert_event.creation_date IS '创建时间';
COMMENT ON COLUMN sre_alert_event.last_update_date IS '最后更新时间';
COMMENT ON COLUMN sre_alert_event.created_by IS '创建人（恒 system，poller 写入）';
COMMENT ON COLUMN sre_alert_event.last_updated_by IS '最后更新人（恒 system）';
COMMENT ON COLUMN sre_alert_event.expire_at IS '过期时间（写入时 now()+30d；dispatcher 循环搭车硬删）';

-- 3) 聚合单 = 队列实体（DB 即队列：incident_state 驱动消费，重启天然存活）
CREATE TABLE IF NOT EXISTS sre_alert_incident (
  alert_incident_id uuid NOT NULL PRIMARY KEY,
  agent_team_instance_id uuid NOT NULL,
  owner_user_id text NOT NULL,
  alert_rule_id uuid,
  matched_rules_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  group_key text NOT NULL,
  title text NOT NULL DEFAULT '',
  severity text NOT NULL DEFAULT 'warning',
  category text NOT NULL DEFAULT '',
  incident_state text NOT NULL DEFAULT 'queued',
  state_reason text,
  agent_run_id uuid,
  diagnosis_task_id text,
  result_summary text,
  agent_result text,
  user_feedback text,
  feedback_note text,
  alert_count integer NOT NULL DEFAULT 1,
  first_alert_at timestamptz,
  last_alert_at timestamptz,
  queued_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  ended_at timestamptz,
  retry_count integer NOT NULL DEFAULT 0,
  creation_date timestamptz NOT NULL DEFAULT now(),
  last_update_date timestamptz NOT NULL DEFAULT now(),
  created_by text NOT NULL DEFAULT 'system',
  last_updated_by text NOT NULL DEFAULT 'system',
  deleted_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_alert_inc_state_q
  ON sre_alert_incident (incident_state, queued_at);
CREATE INDEX IF NOT EXISTS ix_alert_inc_inst_time
  ON sre_alert_incident (agent_team_instance_id, creation_date DESC);
CREATE INDEX IF NOT EXISTS ix_alert_inc_owner_time
  ON sre_alert_incident (owner_user_id, creation_date DESC);
CREATE INDEX IF NOT EXISTS ix_alert_inc_group
  ON sre_alert_incident (group_key, incident_state);
CREATE INDEX IF NOT EXISTS ix_alert_inc_run
  ON sre_alert_incident (agent_run_id);

COMMENT ON TABLE sre_alert_incident IS '告警聚合单（接管清单行，兼队列实体）：queued 态即待诊队列，dispatcher 条件 UPDATE 抢占消费；附着式聚合——窗口内同 group_key 新告警附着到未完结单';
COMMENT ON COLUMN sre_alert_incident.alert_incident_id IS '聚合单主键';
COMMENT ON COLUMN sre_alert_incident.agent_team_instance_id IS '接管方 AgentTeam 实例 ID（每实例各自成单）';
COMMENT ON COLUMN sre_alert_incident.owner_user_id IS '实例 owner 工号（诊断 run 以此身份创建）';
COMMENT ON COLUMN sre_alert_incident.alert_rule_id IS '首个命中规则 ID（全部命中见 matched_rules_json）';
COMMENT ON COLUMN sre_alert_incident.matched_rules_json IS '全部命中规则 [{rule_id,rule_name}] 快照';
COMMENT ON COLUMN sre_alert_incident.group_key IS '聚合键：instance_id|appid|alert_name；附着与组冷却按此判定';
COMMENT ON COLUMN sre_alert_incident.title IS '展示标题（取首条告警名，附着聚合时追加计数）';
COMMENT ON COLUMN sre_alert_incident.severity IS '组内最高严重度（附着时可升级）：fatal/critical/warning/info';
COMMENT ON COLUMN sre_alert_incident.category IS '告警类型（取首条告警 category）';
COMMENT ON COLUMN sre_alert_incident.incident_state IS 'queued(排队)/diagnosing(诊断中)/completed(完成)/failed(失败)/skipped(溢出或冷却丢弃留痕)/ignored(人工忽略)';
COMMENT ON COLUMN sre_alert_incident.state_reason IS '状态补充原因：overflow/cooldown/timeout/interrupted_by_restart/stale/no_scope/disabled…';
COMMENT ON COLUMN sre_alert_incident.agent_run_id IS '诊断会话 run ID（entry_source=alert；前端跳 /agent-runs/{id} 可追问）';
COMMENT ON COLUMN sre_alert_incident.diagnosis_task_id IS '诊断任务 ID（task_origin=alert）';
COMMENT ON COLUMN sre_alert_incident.result_summary IS '诊断结论摘要（≤500 字：rca 结论或 transcript 末条 assistant 截取）';
COMMENT ON COLUMN sre_alert_incident.agent_result IS '接管结果：recovered(已恢复)/escalated(已升级)/failed(失败)；可结构化提取时回填，NULL 时 UI 显「已完成」';
COMMENT ON COLUMN sre_alert_incident.user_feedback IS '用户评价：positive/negative/neutral，NULL=未评价';
COMMENT ON COLUMN sre_alert_incident.feedback_note IS '用户评价备注';
COMMENT ON COLUMN sre_alert_incident.alert_count IS '关联原始告警累计条数（含去重窗口 seen_count 增量；风暴 ×N 徽标）';
COMMENT ON COLUMN sre_alert_incident.first_alert_at IS '首条告警时间';
COMMENT ON COLUMN sre_alert_incident.last_alert_at IS '最近一条告警时间（重启 requeue 陈旧判定基准）';
COMMENT ON COLUMN sre_alert_incident.queued_at IS '入队时间（队列 FIFO 排序键，severity 之后）';
COMMENT ON COLUMN sre_alert_incident.started_at IS '诊断开始时间（queued→diagnosing）';
COMMENT ON COLUMN sre_alert_incident.ended_at IS '终态时间（completed/failed/skipped/ignored）';
COMMENT ON COLUMN sre_alert_incident.retry_count IS '重试次数（起 run 失败退避重试与重启 requeue 共用计数）';
COMMENT ON COLUMN sre_alert_incident.creation_date IS '创建时间';
COMMENT ON COLUMN sre_alert_incident.last_update_date IS '最后更新时间';
COMMENT ON COLUMN sre_alert_incident.created_by IS '创建人（恒 system）';
COMMENT ON COLUMN sre_alert_incident.last_updated_by IS '最后更新人（system 或人工动作者工号）';
COMMENT ON COLUMN sre_alert_incident.deleted_at IS '软删除时间，NULL 表示未删除';

-- 4) incident ↔ event 关联（多对多：一条告警可命中多实例规则、进多个 incident）
CREATE TABLE IF NOT EXISTS sre_alert_incident_event (
  link_id uuid NOT NULL PRIMARY KEY,
  alert_incident_id uuid NOT NULL,
  alert_event_id uuid NOT NULL,
  matched_rule_ids_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  creation_date timestamptz NOT NULL DEFAULT now(),
  last_update_date timestamptz NOT NULL DEFAULT now(),
  created_by text NOT NULL DEFAULT 'system',
  last_updated_by text NOT NULL DEFAULT 'system',
  deleted_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_inc_event
  ON sre_alert_incident_event (alert_incident_id, alert_event_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_alert_inc_event_evt
  ON sre_alert_incident_event (alert_event_id);

COMMENT ON TABLE sre_alert_incident_event IS 'incident 与原始告警事件的关联行（附着式聚合的成员账本）';
COMMENT ON COLUMN sre_alert_incident_event.link_id IS '关联行主键';
COMMENT ON COLUMN sre_alert_incident_event.alert_incident_id IS '聚合单 ID';
COMMENT ON COLUMN sre_alert_incident_event.alert_event_id IS '原始告警事件 ID';
COMMENT ON COLUMN sre_alert_incident_event.matched_rule_ids_json IS '该事件命中的规则 ID 数组（附着时点快照）';
COMMENT ON COLUMN sre_alert_incident_event.creation_date IS '创建时间（附着时间）';
COMMENT ON COLUMN sre_alert_incident_event.last_update_date IS '最后更新时间';
COMMENT ON COLUMN sre_alert_incident_event.created_by IS '创建人（恒 system）';
COMMENT ON COLUMN sre_alert_incident_event.last_updated_by IS '最后更新人（恒 system）';
COMMENT ON COLUMN sre_alert_incident_event.deleted_at IS '软删除时间，NULL 表示未删除';

-- 5) 实例级订阅设置（一实例一行）：总开关 + 限额覆盖 + Phase2 通知偏好预留
CREATE TABLE IF NOT EXISTS sre_alert_subscription (
  subscription_id uuid NOT NULL PRIMARY KEY,
  agent_team_instance_id uuid NOT NULL,
  owner_user_id text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  max_open_incidents integer,
  settings_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  creation_date timestamptz NOT NULL DEFAULT now(),
  last_update_date timestamptz NOT NULL DEFAULT now(),
  created_by text NOT NULL DEFAULT 'system',
  last_updated_by text NOT NULL DEFAULT 'system',
  deleted_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_sub_inst
  ON sre_alert_subscription (agent_team_instance_id) WHERE deleted_at IS NULL;

COMMENT ON TABLE sre_alert_subscription IS '实例级告警接管订阅（一实例一行）：总开关一键暂停但保留规则；无行=未开通接管';
COMMENT ON COLUMN sre_alert_subscription.subscription_id IS '订阅主键';
COMMENT ON COLUMN sre_alert_subscription.agent_team_instance_id IS 'AgentTeam 实例 ID（唯一）';
COMMENT ON COLUMN sre_alert_subscription.owner_user_id IS '实例 owner 工号';
COMMENT ON COLUMN sre_alert_subscription.enabled IS '实例级总开关（三级开关第二级：全局 alert_enabled ＞ 本开关 ＞ 规则 enabled）';
COMMENT ON COLUMN sre_alert_subscription.max_open_incidents IS '实例未完结聚合单上限覆盖；NULL=用全局 alert_max_open_incidents_per_instance';
COMMENT ON COLUMN sre_alert_subscription.settings_json IS 'Phase2 预留：通知渠道/风暴策略等实例级覆盖';
COMMENT ON COLUMN sre_alert_subscription.creation_date IS '创建时间';
COMMENT ON COLUMN sre_alert_subscription.last_update_date IS '最后更新时间';
COMMENT ON COLUMN sre_alert_subscription.created_by IS '创建人工号';
COMMENT ON COLUMN sre_alert_subscription.last_updated_by IS '最后更新人工号';
COMMENT ON COLUMN sre_alert_subscription.deleted_at IS '软删除时间，NULL 表示未删除';

-- 6) 拉取游标（运行态，一源一行；刻意不入 sre_platform_runtime_config——后者有 reason+审计语义）
CREATE TABLE IF NOT EXISTS sre_alert_pull_state (
  pull_state_id uuid NOT NULL PRIMARY KEY,
  source_key text NOT NULL,
  cursor_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_pull_at timestamptz,
  last_ok_at timestamptz,
  last_status text,
  last_error text,
  creation_date timestamptz NOT NULL DEFAULT now(),
  last_update_date timestamptz NOT NULL DEFAULT now(),
  created_by text NOT NULL DEFAULT 'system',
  last_updated_by text NOT NULL DEFAULT 'system',
  deleted_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_pull_source
  ON sre_alert_pull_state (source_key) WHERE deleted_at IS NULL;

COMMENT ON TABLE sre_alert_pull_state IS '告警平台增量拉取游标（运行态，一源一行）：对端不透明游标，契约保证可重放（at-least-once），我侧 fingerprint upsert 幂等';
COMMENT ON COLUMN sre_alert_pull_state.pull_state_id IS '游标行主键';
COMMENT ON COLUMN sre_alert_pull_state.source_key IS '告警源标识（与 sre_alert_event.source_key 同口径）';
COMMENT ON COLUMN sre_alert_pull_state.cursor_json IS '{cursor:"对端不透明游标"}；空对象=从「现在」开始（首启不回灌历史）';
COMMENT ON COLUMN sre_alert_pull_state.last_pull_at IS '最近一次拉取尝试时间';
COMMENT ON COLUMN sre_alert_pull_state.last_ok_at IS '最近一次成功拉取时间';
COMMENT ON COLUMN sre_alert_pull_state.last_status IS 'ok / failed（admin overview 展示）';
COMMENT ON COLUMN sre_alert_pull_state.last_error IS '最近一次失败摘要（截断）';
COMMENT ON COLUMN sre_alert_pull_state.creation_date IS '创建时间';
COMMENT ON COLUMN sre_alert_pull_state.last_update_date IS '最后更新时间';
COMMENT ON COLUMN sre_alert_pull_state.created_by IS '创建人（恒 system）';
COMMENT ON COLUMN sre_alert_pull_state.last_updated_by IS '最后更新人（恒 system）';
COMMENT ON COLUMN sre_alert_pull_state.deleted_at IS '软删除时间，NULL 表示未删除';

-- 2026-08-09 算力保护：告警接管按管理员白名单开放（fail-closed：名单外用户配置面 403、
-- 匹配面不加载其规则、派发面置 skipped；platform_admin 天然豁免）。
CREATE TABLE IF NOT EXISTS sre_alert_user_grant (
  user_id text NOT NULL PRIMARY KEY,
  granted_by text NOT NULL DEFAULT 'system',
  creation_date timestamptz NOT NULL DEFAULT now(),
  last_update_date timestamptz NOT NULL DEFAULT now(),
  created_by text NOT NULL DEFAULT 'system',
  last_updated_by text NOT NULL DEFAULT 'system',
  deleted_at timestamptz
);

COMMENT ON TABLE sre_alert_user_grant IS '7x24 告警接管功能白名单（算力有限按需开通）：在名单=可配置订阅/规则并参与匹配派发；revoke 用软删（deleted_at），重开通复活同行保留 granted_by 审计链';
COMMENT ON COLUMN sre_alert_user_grant.user_id IS '被授权用户 id（一行一人）';
COMMENT ON COLUMN sre_alert_user_grant.granted_by IS '最近一次开通操作的管理员 id';
COMMENT ON COLUMN sre_alert_user_grant.creation_date IS '创建时间';
COMMENT ON COLUMN sre_alert_user_grant.last_update_date IS '最后更新时间';
COMMENT ON COLUMN sre_alert_user_grant.created_by IS '创建人';
COMMENT ON COLUMN sre_alert_user_grant.last_updated_by IS '最后更新人';
COMMENT ON COLUMN sre_alert_user_grant.deleted_at IS '软删除时间（=已关闭开通），NULL 表示在名单';
