/** 7x24 告警接管切片的 API 类型（枚举口径见 docs/plans/alert-takeover-7x24.md 决策 #10）。
 *
 * 与 studio/types.ts 同律：类型随切片走，core 的 lib/api/types.ts 不认识它们。
 * v2 需求对齐：清单页改为**事件视角**（AlertEventRow，上游告警平台的行口径），
 * incident 系列类型保留（api 方法不删，页面暂不消费）。
 */

/** 告警等级四档：致命 / 严重 / 普通 / 提示。 */
export type AlertSeverity = "fatal" | "critical" | "warning" | "info";
/** 告警类型是**开放枚举**（上游告警平台可扩展）：Phase1 模板仅 MySQL/PostgreSQL/Docker 三类（2026-08-09 起对齐内网 moType 词表）。 */
export type AlertCategory = string;
/** 事件六态（DB 即队列）：排队中 → 诊断中 → 已完成/失败；溢出丢弃=已跳过；人工忽略=已忽略。 */
export type IncidentStatus = "queued" | "diagnosing" | "completed" | "failed" | "skipped" | "ignored";
/** Agent 接管结果（incident 口径）：已恢复 / 处理中 / 已升级（转人工）/ 失败。 */
export type AgentResult = "recovered" | "processing" | "escalated" | "failed";
/** 用户对本次接管的评价。 */
export type UserFeedback = "positive" | "negative" | "neutral";

/** 告警平台侧的告警状态三态：已关闭 / 已分派 / 未分派。 */
export type AlertEventStatus = "closed" | "assigned" | "unassigned";
/** Agent 接管状态三态：已完成 / 处理中 / 未接管。 */
export type AlertTakeoverStatus = "done" | "processing" | "none";
/** 事件行的接管结果（「处理中」语义归 takeover_status，故无 processing 档）。 */
export type AlertEventAgentResult = "recovered" | "escalated" | "failed";

/** 告警事件行（清单页主形态，GET /alerts/events 的行口径）。
 *  `duration` 是前端投影列：api 层由 `duration_s` 生成中文文案（<60s「N秒」否则「N分」）。 */
export interface AlertEventRow {
  /** 未接管细分（skip 原因透出，如 stale_consumer_lag=延迟放弃）；null=非 skip。 */
  state_reason?: string | null;
  /** 告警编号（ALM-yyyymmdd-xxxxxx 风格）。 */
  alert_no: string;
  category: AlertCategory;
  /** 告警对象（实例/节点名，如 mysql-prod-03）。 */
  alert_object: string;
  appid: string;
  title: string;
  description: string;
  alert_status: AlertEventStatus;
  severity: AlertSeverity;
  takeover_status: AlertTakeoverStatus;
  agent_result: AlertEventAgentResult | null;
  user_feedback: UserFeedback | null;
  feedback_note: string;
  /** 告警平台详情外链；空串=缺失（编号列退纯文本）。历史预览/清单行由后端按
   *  enterpriseId 分发拼接（32×8→ALARM_OP_URL、32×1→ALARM_KWE_URL，?alarmCode=编号）。 */
  detail_url: string;
  /** 企业 ID（29.10 enterpriseId；预览表展示列 + 跳转分发键）。清单存量行可缺省。 */
  enterprise_id?: string;
  incident_id: string | null;
  /** 诊断会话 id（run_alert_* 前缀）；未接管时为 null。 */
  run_id: string | null;
  /** 会话是否可打开（run 可能已过期清理，此时置 false）。 */
  run_clickable: boolean;
  started_at: string;
  ended_at: string | null;
  duration_s: number | null;
  /** 同指纹重复告警次数。 */
  seen_count: number;
  /** 展示态：duration_s 的中文文案投影（api 投影层补齐，服务端不下发）。 */
  duration: string | null;
}

/** 事件行的线上传输形态（服务端/mock 的原始行，无投影列）。 */
export type AlertEventWire = Omit<AlertEventRow, "duration">;

export interface AlertEventsPage {
  items: AlertEventRow[];
  total: number;
  page: number;
  page_size: number;
}

/** 历史预览数据来源：platform=内网 alarm_list 真历史；local_fallback=平台不可用时的本地落库降级。 */
export type AlertPreviewSource = "platform" | "local_fallback";

/** GET /alerts/history-preview 的返回（规则编辑器第二步）。 */
export interface AlertHistoryPreviewPage extends AlertEventsPage {
  source: AlertPreviewSource;
}

/** 告警事件（聚合后的接管单元）：一条 = 同指纹/同 group_key 的一簇原始告警。 */
export interface AlertIncident {
  incident_id: string;
  instance_id: string;
  instance_name: string;
  category: AlertCategory;
  title: string;
  severity: AlertSeverity;
  status: IncidentStatus;
  agent_result: AgentResult | null;
  user_feedback: UserFeedback | null;
  feedback_note: string;
  app_id: string;
  fingerprint: string;
  /** 聚合的原始告警条数（风暴样本可达上百条）。 */
  alert_count: number;
  matched_rule: { rule_id: string; name: string } | null;
  /** 诊断会话 id（run_alert_* 前缀）；排队中/跳过时为 null。 */
  run_id: string | null;
  queue_position: number | null;
  result_summary: string | null;
  started_at: string;
  ended_at: string | null;
  duration: string | null;
  updated_at: string;
}

export interface AlertIncidentDetail extends AlertIncident {
  labels: Record<string, string>;
  annotations: Record<string, string>;
  /** 关联原始告警（服务端截断下发，全量条数见 alerts_total）。 */
  alerts: {
    alert_id: string;
    title: string;
    status: "firing" | "resolved";
    severity: AlertSeverity;
    started_at: string;
    resolved_at: string | null;
  }[];
  alerts_total: number;
  /** 接管时间线（入队/建会话/收割/失败等关键节点）。 */
  timeline: { at: string; event: string }[];
  error: { code: string; message: string } | null;
}

/** 订阅规则（实例维度）：v2 = 名称 + 单值类型 + 级别多选 + 监控策略勾选清单 + 诊断提示词。 */
export interface AlertRule {
  rule_id: string;
  name: string;
  description: string;
  /** 策略类型（语义单值：一条规则只属于一个类型）。 */
  /** 策略类型多选（2026-08-07 拍板，任一命中即匹配）；后端兼容存量单值规则的读取。 */
  categories: AlertCategory[];
  enabled: boolean;
  source: "template" | "custom";
  severities: AlertSeverity[];
  /** 勾选的监控策略（当前类型下的模板名清单）。 */
  strategies: string[];
  /** 诊断提示词（新建默认取模板 payload 的 default_prompt，可改）。 */
  prompt: string;
  app_ids: string[];
  keywords: string[];
  updated_at: string;
}

/** 规则页视图 = 规则清单（实例总开关随订阅下线 2026-08-15）。 */
export interface AlertRulesConfig {
  rules: AlertRule[];
}

export interface AlertRuleTemplate {
  category: string;
  name: string;
  description: string;
  keywords: string[];
}

/** GET /alerts/rule-templates 的完整 payload：模板 + 可选级别 + 默认提示词。 */
export interface AlertRuleTemplatesPayload {
  templates: AlertRuleTemplate[];
  severities: AlertSeverity[];
  default_prompt: string;
}

export interface AlertSummary {
  queued: number;
  diagnosing: number;
  completed_24h: number;
  attention: number;
  latest_change_seq: number;
}

export interface AlertIncidentsPage {
  items: AlertIncident[];
  total: number;
  page: number;
  page_size: number;
}

/** 单规则创建入参（rule_id/updated_at 服务端生成；enabled 缺省 true）。 */
export interface NewRuleInput {
  name: string;
  categories: AlertCategory[];
  severities: AlertSeverity[];
  /** 缺省/空 = 该类型全部策略（后端 matcher 空数组短路该维度）。两步向导起编辑器不再勾选。 */
  strategies?: string[];
  prompt: string;
  enabled?: boolean;
}

/** :update 部分更新可传的字段面（与后端冻结契约一致）。 */
export type AlertRulePatch = Partial<
  Pick<AlertRule, "name" | "description" | "categories" | "severities" | "strategies" | "prompt" | "enabled">
>;

/** rules:batch 的动作枚举。 */
export type AlertRuleBatchAction = "enable" | "disable" | "delete";
