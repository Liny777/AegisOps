/** 7x24 告警接管切片的 mock 数据（mock 模式驱动全链路演示）。
 *
 * 实例 id 复用 core mockData 的两只 mock Agent（agt_pay_fast_recovery / agt_gateway_watch），
 * 与会话/活动数据同源同调。诊断会话 id 一律 `run_alert_` 前缀——镜像后端语义：这些 run 被
 * GET /agent-runs 默认过滤（entry_source='alert'），**绝不**加进 core 的 mockConversations。
 *
 * 写操作直接改本模块内存数组；invalidateAlerts 的触发在 api.ts 的 mock 实现里（本文件不
 * 回引 api.ts，避免模块环）。
 */
import { ApiError } from "../lib/api/client";
import { SEVERITY_ORDER } from "./constants";
import type {
  AlertEventWire,
  AlertEventStatus,
  AlertIncidentDetail,
  AlertIncidentsPage,
  AlertRule,
  AlertRuleBatchAction,
  AlertRulePatch,
  AlertRulesConfig,
  AlertRuleTemplatesPayload,
  AlertSeverity,
  AlertSummary,
  AlertTakeoverStatus,
  EnsureRuleInput,
  EnsureRuleResult,
  IncidentStatus,
  NewRuleInput,
  UserFeedback,
} from "./types";

/* ------------------- 规则模板字典（3 类 × 3 条 + EKS 1 条） ------------------- */
// EKS 只有模板、不预置规则——留给 rules:ensure 的 created 路径演示（其余三类各有存量规则）。
export const mockRuleTemplates = [
  { category: "MySQL", name: "资源饱和标准监控", description: "CPU / 内存 / 磁盘 IO 等资源饱和类告警，自动接管并定位瓶颈来源。", keywords: ["cpu_usage", "disk_io"] },
  { category: "MySQL", name: "主从延迟监控", description: "主从复制延迟超阈值告警，自动核对复制链路与大事务。", keywords: ["replication_lag"] },
  { category: "MySQL", name: "慢查询监控", description: "慢查询数量突增告警，自动聚类慢日志并给出索引建议。", keywords: ["slow_query"] },
  { category: "PostgreSQL", name: "事务锁等待监控", description: "事务锁等待超阈值告警，自动定位持锁会话与阻塞链。", keywords: ["lock_wait"] },
  { category: "PostgreSQL", name: "连接池耗尽监控", description: "连接池耗尽告警，自动排查连接泄漏与慢事务占用。", keywords: ["connection_pool"] },
  { category: "PostgreSQL", name: "死锁检测", description: "死锁告警，自动还原死锁环并给出改写建议。", keywords: ["deadlock"] },
  { category: "Docker", name: "容器重启监控", description: "容器反复重启告警，自动拉取退出码与最近变更。", keywords: ["restart_count"] },
  { category: "Docker", name: "资源限制监控", description: "容器触及 CPU/内存限额告警，自动核对限额与实际用量。", keywords: ["oom_killed", "cpu_throttle"] },
  { category: "Docker", name: "健康检查监控", description: "健康检查连续失败告警，自动探测端口与依赖服务。", keywords: ["healthcheck_fail"] },
  { category: "EKS", name: "EKS 节点资源压力监控", description: "节点 CPU / 内存 / 磁盘压力告警，自动定位高负载 Pod 与驱逐风险。", keywords: ["node_pressure", "pod_evicted"] },
];

/** 新建规则默认预填的诊断提示词（五步法口径，用户可在编辑器里改）。 */
export const DEFAULT_PROMPT =
  "请按五步法诊断：1) 确认告警与影响面；2) 采集实例指标、日志与近期变更；3) 定位根因并给出证据链；4) 评估可自动执行的恢复动作，高危操作转人工审批；5) 输出结论与后续建议。关键结论与证据写入对话。";

/** 模板 payload 可选级别只有三档（info 不进接管口径）。 */
const TEMPLATE_SEVERITIES: AlertSeverity[] = ["fatal", "critical", "warning"];

export const mockGetRuleTemplates = (): AlertRuleTemplatesPayload => ({
  templates: mockRuleTemplates.map((t) => ({ ...t, keywords: [...t.keywords] })),
  severities: [...TEMPLATE_SEVERITIES],
  default_prompt: DEFAULT_PROMPT,
});

/* ----------------------------- 事件清单（内存态） ----------------------------- */
// 两只实例沿用 core mockData 的真实 id：agt_pay_fast_recovery（支付域感知快恢）/
// agt_gateway_watch（网关健康守护）。`instance_id` 是 mock 内部的归属字段（行契约里没有，
// 仅用于 instance_id= 筛选；随浅拷贝带出无害）。

/** 距今 daysAgo 天、当日 hms（+08:00 口径）的 ISO 串。
 *  事件日期用相对生成而非字面量：规则弹窗预览按 since_days 过滤（近3天/近7天），
 *  写死日期会随时间老化出窗（2026-08 实测：07-30 的样本已查不出来）。alert_no 是编号
 *  非日期语义，保持字面量不动。分布设计：D1/D2 落 3 天窗、D5/D6 只落 7 天窗——
 *  切换窗口有计数差可演示可断言。时刻取早晨段，给 "+08:00" 字面量与 runner 本地
 *  时区之间留 ≥8h 漂移余量（勿在 D6 用 20:00 之后的时刻）。 */
const iso = (daysAgo: number, hms: string): string => {
  const d = new Date(Date.now() - daysAgo * 86_400_000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${hms}+08:00`;
};
interface MockEventRow extends AlertEventWire {
  /** 接管该事件的实例；未接管行为 null。 */
  instance_id: string | null;
}

const PAY_ID = "agt_pay_fast_recovery";
const GW_ID = "agt_gateway_watch";

export const mockEvents: MockEventRow[] = [
  {
    alert_no: "ALM-20260730-000110",
    category: "Docker",
    alert_object: "ads-node-12",
    appid: "00000000000000000000000000000601",
    title: "ADS 容器内存限额触顶",
    description: "ads-node-12 上 pay-batch-3 容器触及内存限额（1.9G/2G），存在 OOM 风险。",
    alert_status: "assigned",
    severity: "warning",
    takeover_status: "processing",
    agent_result: null,
    user_feedback: null,
    feedback_note: "",
    detail_url: "https://alert.example.com/alerts/ALM-20260730-000110",
    incident_id: "inc_evt_110",
    run_id: "run_alert_evt_110",
    run_clickable: true,
    started_at: iso(1, "10:40:21"),
    ended_at: null,
    duration_s: null,
    seen_count: 4,
    instance_id: PAY_ID,
  },
  {
    alert_no: "ALM-20260730-000106",
    category: "PostgreSQL",
    alert_object: "pg-prod-02",
    appid: "00000000000000000000000000000602",
    title: "PostgreSQL 事务锁等待超阈值",
    description: "pg-prod-02 出现事务锁等待超过 30s 的会话，疑似批处理与在线事务互锁。",
    alert_status: "unassigned",
    severity: "warning",
    takeover_status: "none",
    state_reason: "stale_consumer_lag", // 演示样本：延迟放弃（切到网关 Agent 可见；e2e 主桶保持「未接管」形态）
    agent_result: null,
    user_feedback: null,
    feedback_note: "",
    detail_url: "", // 缺失样本：编号列退纯文本
    incident_id: null,
    run_id: null,
    run_clickable: false,
    started_at: iso(1, "10:31:47"),
    ended_at: null,
    duration_s: null,
    seen_count: 1,
    instance_id: null,
  },
  {
    alert_no: "ALM-20260730-000105",
    category: "MySQL",
    alert_object: "mysql-prod-07",
    appid: "00000000000000000000000000000601",
    title: "MySQL 磁盘 IO 饱和",
    description: "mysql-prod-07 磁盘 IO util 连续 5 分钟超过 95%，写延迟上升。",
    alert_status: "unassigned",
    severity: "critical",
    takeover_status: "none",
    agent_result: null,
    user_feedback: null,
    feedback_note: "",
    detail_url: "https://alert.example.com/alerts/ALM-20260730-000105",
    incident_id: null,
    run_id: null,
    run_clickable: false,
    started_at: iso(1, "10:22:09"),
    ended_at: null,
    duration_s: null,
    seen_count: 3,
    instance_id: null,
  },
  {
    alert_no: "ALM-20260730-000102",
    category: "PostgreSQL",
    alert_object: "pg-prod-01",
    appid: "00000000000000000000000000000602",
    title: "PostgreSQL 连接池耗尽",
    description: "pg-prod-01 连接池 active 200/200，等待队列 84，订单写入受阻。",
    alert_status: "assigned",
    severity: "fatal",
    takeover_status: "processing",
    agent_result: null,
    user_feedback: null,
    feedback_note: "",
    detail_url: "https://alert.example.com/alerts/ALM-20260730-000102",
    incident_id: "inc_evt_102",
    run_id: "run_alert_evt_102",
    run_clickable: true,
    started_at: iso(1, "10:02:33"),
    ended_at: null,
    duration_s: null,
    seen_count: 6,
    instance_id: PAY_ID,
  },
  {
    alert_no: "ALM-20260730-000112",
    category: "PostgreSQL",
    alert_object: "pg-gw-02",
    appid: "00000000000000000000000000000425",
    title: "PostgreSQL 死锁频发",
    description: "pg-gw-02 十分钟内出现 7 次死锁回滚，涉及网关配置表。",
    alert_status: "unassigned",
    severity: "fatal",
    takeover_status: "processing",
    agent_result: null,
    user_feedback: null,
    feedback_note: "",
    detail_url: "https://alert.example.com/alerts/ALM-20260730-000112",
    incident_id: "inc_evt_112",
    run_id: "run_alert_evt_112",
    run_clickable: true,
    started_at: iso(2, "09:52:12"),
    ended_at: null,
    duration_s: null,
    seen_count: 2,
    instance_id: GW_ID,
  },
  {
    alert_no: "ALM-20260730-000101",
    category: "MySQL",
    alert_object: "mysql-prod-03",
    appid: "00000000000000000000000000000601",
    title: "MySQL 主从延迟超过 120s",
    description: "mysql-prod-03 复制延迟持续攀升至 189s，命中主从延迟监控策略，已自动接管诊断。",
    alert_status: "closed",
    severity: "critical",
    takeover_status: "done",
    agent_result: "recovered",
    user_feedback: "positive",
    feedback_note: "定位很准，恢复动作也稳",
    detail_url: "https://alert.example.com/alerts/ALM-20260730-000101",
    incident_id: "inc_evt_101",
    run_id: "run_alert_evt_101",
    run_clickable: true,
    started_at: iso(2, "09:12:04"),
    ended_at: iso(2, "09:26:16"),
    duration_s: 852,
    seen_count: 137,
    instance_id: PAY_ID,
  },
  {
    alert_no: "ALM-20260730-000104",
    category: "MySQL",
    alert_object: "mysql-prod-03",
    appid: "00000000000000000000000000000601",
    title: "MySQL 活跃连接突增",
    description: "mysql-prod-03 活跃连接从 80 突增至 640，超出自动恢复白名单，已升级人工。",
    alert_status: "closed",
    severity: "warning",
    takeover_status: "done",
    agent_result: "escalated",
    user_feedback: null,
    feedback_note: "",
    detail_url: "https://alert.example.com/alerts/ALM-20260730-000104",
    incident_id: "inc_evt_104",
    run_id: "run_alert_evt_104",
    run_clickable: true,
    started_at: iso(2, "08:12:44"),
    ended_at: iso(2, "08:35:30"),
    duration_s: 1366,
    seen_count: 12,
    instance_id: PAY_ID,
  },
  {
    alert_no: "ALM-20260730-000107",
    category: "Docker",
    alert_object: "ads-node-7",
    appid: "00000000000000000000000000000423",
    title: "ADS 容器健康检查抖动",
    description: "ads-node-7 上 edge-cache 容器健康检查单次失败后自愈，55 秒内恢复。（队列溢出丢弃留痕，可人工重试）",
    alert_status: "closed",
    severity: "warning",
    takeover_status: "none",
    agent_result: null,
    user_feedback: null,
    feedback_note: "",
    detail_url: "https://alert.example.com/alerts/ALM-20260730-000107",
    incident_id: "inc_evt_skip01",
    run_id: null,
    run_clickable: false,
    started_at: iso(5, "07:39:05"),
    ended_at: iso(5, "07:40:00"),
    duration_s: 55,
    seen_count: 2,
    instance_id: PAY_ID,
  },
  {
    alert_no: "ALM-20260730-000109",
    category: "PostgreSQL",
    alert_object: "pg-prod-01",
    appid: "00000000000000000000000000000602",
    title: "PostgreSQL 慢事务堆积",
    description: "pg-prod-01 出现 12 个超过 60s 的长事务，来自订单批处理任务未释放游标，已受控恢复。",
    alert_status: "closed",
    severity: "critical",
    takeover_status: "done",
    agent_result: "recovered",
    user_feedback: null,
    feedback_note: "",
    detail_url: "https://alert.example.com/alerts/ALM-20260730-000109",
    incident_id: "inc_evt_109",
    run_id: "run_alert_evt_109",
    run_clickable: true,
    started_at: iso(5, "07:20:11"),
    ended_at: iso(5, "07:26:23"),
    duration_s: 372,
    seen_count: 6,
    instance_id: PAY_ID,
  },
  {
    alert_no: "ALM-20260730-000103",
    category: "Docker",
    alert_object: "ads-node-7",
    appid: "00000000000000000000000000000423",
    title: "ADS 容器反复重启",
    description: "ads-node-7 上 gateway-edge-2 容器 10 分钟内重启 5 次（exit 137），诊断超时失败。",
    alert_status: "assigned",
    severity: "critical",
    takeover_status: "done",
    agent_result: "failed",
    user_feedback: "negative",
    feedback_note: "没定位到根因，还得人工来",
    detail_url: "https://alert.example.com/alerts/ALM-20260730-000103",
    incident_id: "inc_evt_103",
    run_id: "run_alert_evt_103", // run 已过期清理 → 不可点（run_clickable=false 样本）
    run_clickable: false,
    started_at: iso(6, "03:05:20"),
    ended_at: null,
    duration_s: null,
    seen_count: 5,
    instance_id: GW_ID,
  },
  {
    alert_no: "ALM-20260730-000108",
    category: "MySQL",
    alert_object: "mysql-prod-05",
    appid: "00000000000000000000000000000601",
    title: "MySQL 备份任务超时",
    description: "mysql-prod-05 夜间备份任务超过 45 分钟未完成，已由值班人工处理关闭。",
    alert_status: "closed",
    severity: "fatal",
    takeover_status: "none",
    agent_result: null,
    user_feedback: null,
    feedback_note: "",
    detail_url: "https://alert.example.com/alerts/ALM-20260730-000108",
    incident_id: null,
    run_id: null,
    run_clickable: false,
    started_at: iso(6, "02:11:40"),
    ended_at: iso(6, "02:58:40"),
    duration_s: 2820,
    seen_count: 9,
    instance_id: null,
  },
  {
    alert_no: "ALM-20260729-000111",
    category: "MySQL",
    alert_object: "mysql-gw-01",
    appid: "00000000000000000000000000000425",
    title: "MySQL 慢查询数量上升",
    description: "mysql-gw-01 慢查询 12/min，聚类后定位为夜间批量任务缺索引，已给出建议并恢复。",
    alert_status: "closed",
    severity: "warning",
    takeover_status: "done",
    agent_result: "recovered",
    user_feedback: null,
    feedback_note: "",
    detail_url: "https://alert.example.com/alerts/ALM-20260729-000111",
    incident_id: "inc_evt_111",
    run_id: "run_alert_evt_111",
    run_clickable: true,
    started_at: iso(6, "01:40:00"),
    ended_at: iso(6, "02:10:00"),
    duration_s: 1800,
    seen_count: 4,
    instance_id: GW_ID,
  },
];

export interface MockListEventsParams {
  instanceId?: string;
  alertStatus?: AlertEventStatus;
  severity?: AlertSeverity | AlertSeverity[];
  takeover?: AlertTakeoverStatus;
  category?: string;
  search?: string;
  page?: number;
  pageSize?: number;
  /** 只看最近 N 天（配置弹窗预览）。⚠ mock 行日期为静态样本，久置会自然老化出窗。 */
  sinceDays?: number;
  /** admin 变体透传；mock 不区分用户，仅保留参数语义。 */
}

/** mock 档的筛选/搜索/分页都在本地完成（real 档同名参数下沉服务端）。
 *  ⚠ mock 简化：未接管行（takeover=none）在**任意实例视图**都出现——真实端由服务端按
 *  用户 scope 可见性下发候选告警，这里不建模 scope，只保证演示上「可接管的告警看得见」。 */
export const mockListEvents = (params: MockListEventsParams = {}): { items: AlertEventWire[]; total: number; page: number; page_size: number } => {
  const page = Math.max(1, params.page ?? 1);
  const pageSize = Math.max(1, params.pageSize ?? 20);
  const q = (params.search ?? "").trim().toLowerCase();
  const rows = mockEvents.filter((it) => {
    if (params.instanceId && it.takeover_status !== "none" && it.instance_id !== params.instanceId) return false;
    // 2026-07-31 拍板：未命中规则的告警丢弃不进用户清单——无单的 none 行仅弹窗预览（sinceDays）可见；
    // 清单里的「未接管」= 溢出丢弃/人工忽略的留痕单（有 incident_id）。真实端同口径由服务端下发。
    if (!params.sinceDays && it.takeover_status === "none" && !it.incident_id) return false;
    if (params.alertStatus && it.alert_status !== params.alertStatus) return false;
    if (params.severity) {
      const sevs = Array.isArray(params.severity) ? params.severity : [params.severity];
      if (sevs.length && !sevs.includes(it.severity)) return false;
    }
    if (params.takeover && it.takeover_status !== params.takeover) return false;
    if (params.category && it.category !== params.category) return false;
    if (params.sinceDays &&
        new Date(it.started_at).getTime() < Date.now() - params.sinceDays * 86_400_000) return false;
    // 搜索口径与 placeholder 一致：编号 / 类型 / 对象
    if (q && !(
      it.alert_no.toLowerCase().includes(q) ||
      it.category.toLowerCase().includes(q) ||
      it.alert_object.toLowerCase().includes(q)
    )) return false;
    return true;
  });
  const sorted = [...rows].sort((a, b) => (a.started_at < b.started_at ? 1 : -1));
  return {
    // 浅拷贝斩断消费端与内存态的引用共享（先例：core mockData 的 listConversations）
    items: sorted.slice((page - 1) * pageSize, page * pageSize).map((it) => ({ ...it })),
    total: sorted.length,
    page,
    page_size: pageSize,
  };
};

/* ----------------------------- 事件清单（incident 内存态，api 保留） ----------------------------- */
const PAY = { instance_id: PAY_ID, instance_name: "支付域感知快恢" };
const GW = { instance_id: GW_ID, instance_name: "网关健康守护" };

export const mockIncidents: AlertIncidentDetail[] = [
  {
    incident_id: "inc_mysql_lag_137",
    ...PAY,
    category: "MySQL",
    title: "MySQL 主从延迟超过 120s（聚合 137 条）",
    severity: "critical",
    status: "diagnosing",
    agent_result: "processing",
    user_feedback: null,
    feedback_note: "",
    app_id: "00000000000000000000000000000601",
    fingerprint: "fp_9f21c4aa7d31",
    alert_count: 137,
    matched_rule: { rule_id: "rule_pay_mysql_core", name: "主从延迟监控" },
    run_id: "run_alert_mysql_lag_01",
    queue_position: null,
    result_summary: null,
    started_at: "2026-07-30T09:12:04+08:00",
    ended_at: null,
    duration: null,
    updated_at: "2026-07-30T09:13:40+08:00",
    labels: { instance: "mysql-pay-03", cluster: "pay-core", env: "prod" },
    annotations: { runbook: "wiki/mysql-replication-lag", threshold: "120s" },
    alerts: [
      { alert_id: "alt_137_001", title: "mysql-pay-03 复制延迟 128s", status: "firing", severity: "critical", started_at: "2026-07-30T09:12:04+08:00", resolved_at: null },
      { alert_id: "alt_137_002", title: "mysql-pay-03 复制延迟 141s", status: "firing", severity: "critical", started_at: "2026-07-30T09:12:34+08:00", resolved_at: null },
      { alert_id: "alt_137_003", title: "mysql-pay-03 复制延迟 156s", status: "firing", severity: "critical", started_at: "2026-07-30T09:13:04+08:00", resolved_at: null },
    ],
    alerts_total: 137,
    timeline: [
      { at: "2026-07-30T09:12:05+08:00", event: "命中规则「主从延迟监控」，创建告警事件" },
      { at: "2026-07-30T09:12:40+08:00", event: "创建诊断会话 run_alert_mysql_lag_01，开始诊断" },
      { at: "2026-07-30T09:13:40+08:00", event: "风暴聚合中：同指纹告警已附着 137 条" },
    ],
    error: null,
  },
  {
    incident_id: "inc_pg_pool_exhausted",
    ...PAY,
    category: "PostgreSQL",
    title: "PostgreSQL 连接池耗尽（active 200/200）",
    severity: "critical",
    status: "completed",
    agent_result: "recovered",
    user_feedback: "positive",
    feedback_note: "定位很准，恢复动作也稳",
    app_id: "00000000000000000000000000000602",
    fingerprint: "fp_e3d8a1f6b904",
    alert_count: 6,
    matched_rule: { rule_id: "rule_pay_pg_order", name: "连接池耗尽监控" },
    run_id: "run_alert_pg_pool_02",
    queue_position: null,
    result_summary: "确认连接泄漏来自订单批处理任务未释放游标；已受控重启连接池并恢复（active 回落 38/200）。建议为批处理补 idle 超时回收配置。",
    started_at: "2026-07-30T07:20:11+08:00",
    ended_at: "2026-07-30T07:26:23+08:00",
    duration: "6m12s",
    updated_at: "2026-07-30T08:02:00+08:00",
    labels: { instance: "pg-order-01", cluster: "order", env: "prod" },
    annotations: { runbook: "wiki/pg-connection-pool" },
    alerts: [
      { alert_id: "alt_pool_001", title: "pg-order-01 连接池 active 200/200", status: "resolved", severity: "critical", started_at: "2026-07-30T07:20:11+08:00", resolved_at: "2026-07-30T07:26:02+08:00" },
    ],
    alerts_total: 6,
    timeline: [
      { at: "2026-07-30T07:20:12+08:00", event: "命中规则「连接池耗尽监控」，创建告警事件" },
      { at: "2026-07-30T07:20:30+08:00", event: "创建诊断会话 run_alert_pg_pool_02，开始诊断" },
      { at: "2026-07-30T07:26:23+08:00", event: "诊断完成：已恢复（结论已收割）" },
    ],
    error: null,
  },
  {
    incident_id: "inc_docker_restart_loop",
    ...GW,
    category: "Docker",
    title: "容器 gateway-edge-2 反复重启（5 次/10min）",
    severity: "critical",
    status: "failed",
    agent_result: "failed",
    user_feedback: null,
    feedback_note: "",
    app_id: "00000000000000000000000000000423",
    fingerprint: "fp_2a6d5e88f130",
    alert_count: 5,
    matched_rule: { rule_id: "rule_gw_docker", name: "容器重启监控" },
    run_id: "run_alert_docker_restart_04",
    queue_position: null,
    result_summary: null,
    started_at: "2026-07-30T03:05:20+08:00",
    ended_at: "2026-07-30T03:21:02+08:00",
    duration: "15m42s",
    updated_at: "2026-07-30T03:21:02+08:00",
    labels: { container: "gateway-edge-2", host: "node-17", env: "prod" },
    annotations: { exit_code: "137" },
    alerts: [
      { alert_id: "alt_rst_001", title: "gateway-edge-2 restart_count +5", status: "firing", severity: "critical", started_at: "2026-07-30T03:05:20+08:00", resolved_at: null },
    ],
    alerts_total: 5,
    timeline: [
      { at: "2026-07-30T03:05:21+08:00", event: "命中规则「容器重启监控」，创建告警事件" },
      { at: "2026-07-30T03:05:40+08:00", event: "创建诊断会话 run_alert_docker_restart_04，开始诊断" },
      { at: "2026-07-30T03:21:02+08:00", event: "诊断失败：指标源连续超时，已退避重试 1 次仍失败" },
    ],
    error: { code: "DIAGNOSE_TIMEOUT", message: "诊断超时（900s）：指标 MCP 连续超时，退避重试 1 次后仍失败。可点击「重试」重新入队。" },
  },
  {
    incident_id: "inc_redis_conn_skip",
    ...PAY,
    category: "Redis",
    title: "Redis 连接数突增（1200/2000）",
    severity: "info",
    status: "skipped",
    agent_result: null,
    user_feedback: null,
    feedback_note: "",
    app_id: "00000000000000000000000000000601",
    fingerprint: "fp_88e01c4f7ad2",
    alert_count: 2,
    matched_rule: { rule_id: "rule_pay_redis_conn", name: "连接数监控" },
    run_id: null,
    queue_position: null,
    result_summary: null,
    started_at: "2026-07-30T09:14:50+08:00",
    ended_at: "2026-07-30T09:14:50+08:00",
    duration: null,
    updated_at: "2026-07-30T09:14:50+08:00",
    labels: { instance: "redis-pay-02", cluster: "pay-core", env: "prod" },
    annotations: {},
    alerts: [
      { alert_id: "alt_conn_001", title: "redis-pay-02 connected_clients 1204", status: "firing", severity: "info", started_at: "2026-07-30T09:14:50+08:00", resolved_at: null },
    ],
    alerts_total: 2,
    timeline: [
      { at: "2026-07-30T09:14:51+08:00", event: "命中规则「连接数监控」，创建告警事件" },
      { at: "2026-07-30T09:14:51+08:00", event: "实例队列已满（10/10），低严重度溢出丢弃（skipped 留痕）" },
    ],
    error: null,
  },
  {
    incident_id: "inc_mysql_slow_queued",
    ...GW,
    category: "MySQL",
    title: "MySQL 慢查询数量上升（12/min）",
    severity: "warning",
    status: "queued",
    agent_result: null,
    user_feedback: null,
    feedback_note: "",
    app_id: "00000000000000000000000000000425",
    fingerprint: "fp_40aa19d7e6c3",
    alert_count: 4,
    matched_rule: { rule_id: "rule_gw_mysql_slow", name: "慢查询监控" },
    run_id: null,
    queue_position: 1,
    result_summary: null,
    started_at: "2026-07-29T22:40:00+08:00",
    ended_at: null,
    duration: null,
    updated_at: "2026-07-29T22:40:00+08:00",
    labels: { instance: "mysql-gw-01", env: "prod" },
    annotations: {},
    alerts: [
      { alert_id: "alt_slow_001", title: "mysql-gw-01 slow_query 12/min", status: "firing", severity: "warning", started_at: "2026-07-29T22:40:00+08:00", resolved_at: null },
    ],
    alerts_total: 4,
    timeline: [
      { at: "2026-07-29T22:40:01+08:00", event: "命中规则「慢查询监控」，创建告警事件" },
      { at: "2026-07-29T22:40:01+08:00", event: "进入诊断队列（第 1 位）" },
    ],
    error: null,
  },
];

/* ----------------------------- 规则清单（内存态） ----------------------------- */
/** 实例 → 规则配置。未知实例首次读取时懒初始化为空配置。 */
export const mockRulesByInstance: Record<string, AlertRulesConfig> = {
  agt_pay_fast_recovery: {
    rules: [
      {
        rule_id: "rule_pay_mysql_core",
        name: "MySQL 生产库接管",
        description: "3 条监控策略",
        categories: ["MySQL"],
        enabled: true,
        source: "custom",
        severities: ["fatal", "critical"],
        strategies: ["资源饱和标准监控", "主从延迟监控", "慢查询监控"],
        prompt: DEFAULT_PROMPT,
        app_ids: [],
        keywords: [],
        updated_at: "2026-07-28T10:00:00+08:00",
      },
      {
        // 无 strategies 的「通用」规则（告警直达一键接管建出来的形态）：rules:ensure 的覆盖/合并
        // 候选只认这类，带监控策略的规则只匹配子集不参与——mock 三态演示依赖这条种子。
        rule_id: "rule_pay_mysql_takeover",
        name: "MySQL致命告警接管规则",
        description: "告警直达一键接管创建",
        categories: ["MySQL"],
        enabled: true,
        source: "custom",
        severities: ["fatal"],
        strategies: [],
        prompt: DEFAULT_PROMPT,
        app_ids: [],
        keywords: [],
        updated_at: "2026-08-01T11:20:00+08:00",
      },
      {
        rule_id: "rule_pay_pg_order",
        name: "PostgreSQL 订单库接管",
        description: "2 条监控策略",
        categories: ["PostgreSQL"],
        enabled: true,
        source: "custom",
        severities: ["fatal", "critical"],
        strategies: ["连接池耗尽监控", "死锁检测"],
        prompt: "先核对连接池水位与长事务占用，再按五步法诊断；恢复动作限于受控重启连接池，禁止直接 kill 业务会话。关键结论与证据写入对话。",
        app_ids: [],
        keywords: [],
        updated_at: "2026-07-29T15:30:00+08:00",
      },
      {
        rule_id: "rule_pay_ads_edge",
        name: "ADS 边缘容器接管",
        description: "1 条监控策略",
        categories: ["Docker"],
        enabled: false,
        source: "custom",
        severities: ["fatal", "critical", "warning"],
        strategies: ["容器重启监控"],
        prompt: DEFAULT_PROMPT,
        app_ids: [],
        keywords: [],
        updated_at: "2026-07-27T09:00:00+08:00",
      },
    ],
  },
  agt_gateway_watch: {
    rules: [
      {
        rule_id: "rule_gw_docker",
        name: "网关容器接管",
        description: "2 条监控策略",
        categories: ["Docker"],
        enabled: true,
        source: "custom",
        severities: ["fatal", "critical"],
        strategies: ["容器重启监控", "健康检查监控"],
        prompt: DEFAULT_PROMPT,
        app_ids: [],
        keywords: [],
        updated_at: "2026-07-27T09:00:00+08:00",
      },
      {
        rule_id: "rule_gw_mysql_slow",
        name: "网关 MySQL 接管",
        description: "1 条监控策略",
        categories: ["MySQL"],
        enabled: false,
        source: "custom",
        severities: ["fatal", "critical", "warning"],
        strategies: ["慢查询监控"],
        prompt: DEFAULT_PROMPT,
        app_ids: [],
        keywords: [],
        updated_at: "2026-07-27T09:00:00+08:00",
      },
    ],
  },
};

const ensureRules = (instanceId: string): AlertRulesConfig => {
  let cfg = mockRulesByInstance[instanceId];
  if (!cfg) {
    cfg = { rules: [] };
    mockRulesByInstance[instanceId] = cfg;
  }
  return cfg;
};

/* ------------------------------- 读操作 ------------------------------- */
const nowIso = (): string => new Date().toISOString();
let changeSeq = 1;

export interface MockListIncidentsParams {
  instanceId?: string;
  status?: IncidentStatus[];
  severity?: AlertSeverity[];
  search?: string;
  page?: number;
  pageSize?: number;
}

/** mock 档的筛选/搜索/分页都在本地完成（real 档同名参数下沉服务端）。 */
export const mockListIncidents = (params: MockListIncidentsParams = {}): AlertIncidentsPage => {
  const page = Math.max(1, params.page ?? 1);
  const pageSize = Math.max(1, params.pageSize ?? 20);
  const q = (params.search ?? "").trim().toLowerCase();
  const rows = mockIncidents.filter((it) => {
    if (params.instanceId && it.instance_id !== params.instanceId) return false;
    if (params.status?.length && !params.status.includes(it.status)) return false;
    if (params.severity?.length && !params.severity.includes(it.severity)) return false;
    if (q && !(
      it.title.toLowerCase().includes(q) ||
      it.category.toLowerCase().includes(q) ||
      it.incident_id.toLowerCase().includes(q) ||
      (it.run_id ?? "").toLowerCase().includes(q)
    )) return false;
    return true;
  });
  const sorted = [...rows].sort((a, b) => (a.started_at < b.started_at ? 1 : -1));
  return {
    items: sorted.slice((page - 1) * pageSize, page * pageSize).map((it) => ({ ...it })),
    total: sorted.length,
    page,
    page_size: pageSize,
  };
};

export const mockGetIncident = (id: string): AlertIncidentDetail => {
  const it = mockIncidents.find((x) => x.incident_id === id);
  if (!it) throw new Error(`告警事件不存在：${id}`);
  return { ...it, labels: { ...it.labels }, annotations: { ...it.annotations }, alerts: it.alerts.map((a) => ({ ...a })), timeline: it.timeline.map((t) => ({ ...t })) };
};

export const mockGetSummary = (instanceId: string): AlertSummary => {
  const rows = mockIncidents.filter((it) => !instanceId || it.instance_id === instanceId);
  return {
    queued: rows.filter((it) => it.status === "queued").length,
    diagnosing: rows.filter((it) => it.status === "diagnosing").length,
    completed_24h: rows.filter((it) => it.status === "completed").length,
    attention: rows.filter((it) => it.status === "failed" || it.status === "skipped").length,
    latest_change_seq: changeSeq,
  };
};

export const mockGetRules = (instanceId: string): AlertRulesConfig => {
  const cfg = ensureRules(instanceId);
  return {
    rules: cfg.rules.map((r) => ({ ...r, severities: [...r.severities], strategies: [...r.strategies], app_ids: [...r.app_ids], keywords: [...r.keywords] })),
  };
};

/* ---------------------- 写操作（真实修改内存数组） ---------------------- */
const touch = (it: AlertIncidentDetail): void => {
  it.updated_at = nowIso();
  changeSeq += 1;
};

export const mockIgnoreIncident = (id: string): void => {
  const it = mockIncidents.find((x) => x.incident_id === id);
  if (!it) throw new Error(`告警事件不存在：${id}`);
  if (it.status !== "queued") throw new Error("仅排队中的告警可忽略");
  it.status = "ignored";
  it.queue_position = null;
  it.ended_at = nowIso();
  it.timeline.push({ at: nowIso(), event: "用户手动忽略" });
  touch(it);
};

export const mockRetryIncident = (id: string): void => {
  const it = mockIncidents.find((x) => x.incident_id === id);
  if (!it) throw new Error(`告警事件不存在：${id}`);
  if (it.status !== "failed" && it.status !== "skipped") throw new Error("仅失败/已跳过的告警可重试");
  it.status = "queued";
  it.queue_position = mockIncidents.filter((x) => x.status === "queued").length;
  it.ended_at = null;
  it.duration = null;
  it.error = null;
  it.timeline.push({ at: nowIso(), event: `用户点击重试，重新进入诊断队列（第 ${it.queue_position} 位）` });
  touch(it);
};

export const mockFeedbackIncident = (id: string, feedback: UserFeedback, note: string): void => {
  const it = mockIncidents.find((x) => x.incident_id === id);
  if (!it) throw new Error(`告警事件不存在：${id}`);
  it.user_feedback = feedback;
  it.feedback_note = note;
  const face = feedback === "positive" ? "👍" : feedback === "negative" ? "👎" : "中评";
  it.timeline.push({ at: nowIso(), event: `用户评价：${face}${note ? " " + note : ""}` });
  touch(it);
};

let ruleSeq = 1;
export const mockCreateRule = (instanceId: string, input: NewRuleInput): void => {
  const cfg = ensureRules(instanceId);
  cfg.rules.push({
    rule_id: `rule_new_${Date.now().toString(36)}_${ruleSeq++}`,
    name: input.name,
    // 空/缺省 = 该类型全部策略（对齐后端 matcher 空数组短路语义；两步向导起编辑器不再勾选）
    description: input.strategies?.length ? `${input.strategies.length} 条监控策略` : "全部监控策略",
    categories: [...input.categories],
    enabled: input.enabled ?? true,
    source: "custom",
    severities: [...input.severities],
    strategies: [...(input.strategies ?? [])],
    prompt: input.prompt,
    app_ids: [],
    keywords: [],
    updated_at: nowIso(),
  });
  changeSeq += 1;
};

/** ensure 覆盖/合并判定只看「纯类别规则」：strategies/app_ids/keywords 任一非空的规则条件
 *  更窄，既不能替直达兜底（不算覆盖）也不被 ensure 改写（对齐后端设计；mock 规则无 label_selectors）。 */
const isEnsureCandidate = (r: AlertRule): boolean =>
  r.enabled && !r.strategies.length && !r.app_ids.length && !r.keywords.length;

/** 提示词归一判等口径（同后端 _effective_prompt）：空白 ≡ 系统默认，派发行为一致才可合并。 */
const effectivePrompt = (p: string): string => p.trim() || DEFAULT_PROMPT;

/** 出参快照：斩断与内存态的引用共享（拷贝面与 mockGetRules 同）。 */
const snapshotRule = (r: AlertRule): AlertRule =>
  ({ ...r, categories: [...r.categories], severities: [...r.severities], strategies: [...r.strategies], app_ids: [...r.app_ids], keywords: [...r.keywords] });

/** rules:ensure（告警直达链自动建规）：已覆盖零改动 / 级别并进既有规则 / 否则新建。 */
export const mockEnsureRule = (instanceId: string, input: EnsureRuleInput): EnsureRuleResult => {
  // 与 real 档 403 同形（ApiError.code=FORBIDDEN、文案同后端 service）：调用方按 code 统一识别未开通
  if (localStorage.getItem("openops.mock.alertGranted") === "0") {
    throw new ApiError("FORBIDDEN", "告警接管功能未开通：算力资源有限按需开放，请联系平台管理员开通");
  }
  const cfg = ensureRules(instanceId);
  const candidates = cfg.rules.filter(
    (r) => isEnsureCandidate(r) && input.categories.every((c) => r.categories.includes(c)));
  // 级别覆盖：severities 空=不限级别；多条候选时优先「已覆盖」而非抢先合并第一条
  const sevCovered = (r: AlertRule): boolean =>
    r.severities.length === 0 || input.severities.every((s) => r.severities.includes(s));
  const covering = candidates.find(sevCovered);
  if (covering) {
    return {
      outcome: "already_covered",
      rule: snapshotRule(covering),
      renamed: false,
      requested_name: input.name,
      merge_detail: null,
      prompt_ignored: effectivePrompt(input.prompt) !== effectivePrompt(covering.prompt),
    };
  }
  // 合并要求提示词归一后相等（同后端）：不同提示词不静默改写既有规则，落 created
  const target = candidates.find((r) => effectivePrompt(r.prompt) === effectivePrompt(input.prompt));
  if (target) {
    const before = [...target.severities];
    target.severities = SEVERITY_ORDER.filter((s) => before.includes(s) || input.severities.includes(s));
    target.updated_at = nowIso();
    changeSeq += 1;
    return {
      outcome: "merged",
      rule: snapshotRule(target),
      renamed: false,
      requested_name: input.name,
      merge_detail: {
        severities_before: before,
        severities_after: [...target.severities],
        added_severities: target.severities.filter((s) => !before.includes(s)),
      },
      prompt_ignored: false, // 提示词相等才走到这里
    };
  }
  // created：重名自动 -2/-3… 后缀（含禁用规则也算占名，避免同名歧义）
  const names = new Set(cfg.rules.map((r) => r.name));
  let name = input.name;
  for (let n = 2; names.has(name); n++) name = `${input.name}-${n}`;
  mockCreateRule(instanceId, { name, categories: input.categories, severities: input.severities, prompt: input.prompt, enabled: true });
  return {
    outcome: "created",
    rule: snapshotRule(cfg.rules[cfg.rules.length - 1]),
    renamed: name !== input.name,
    requested_name: input.name,
    merge_detail: null,
    prompt_ignored: false,
  };
};

const findRule = (ruleId: string): AlertRule => {
  for (const cfg of Object.values(mockRulesByInstance)) {
    const r = cfg.rules.find((x) => x.rule_id === ruleId);
    if (r) return r;
  }
  throw new Error(`规则不存在：${ruleId}`);
};

export const mockUpdateRule = (ruleId: string, patch: AlertRulePatch): void => {
  const r = findRule(ruleId);
  Object.assign(r, patch);
  if (patch.strategies) r.description = `${patch.strategies.length} 条监控策略`;
  r.updated_at = nowIso();
  changeSeq += 1;
};

export const mockDeleteRule = (ruleId: string): void => {
  for (const cfg of Object.values(mockRulesByInstance)) {
    const i = cfg.rules.findIndex((x) => x.rule_id === ruleId);
    if (i >= 0) {
      cfg.rules.splice(i, 1);
      changeSeq += 1;
      return;
    }
  }
  throw new Error(`规则不存在：${ruleId}`);
};

/** rules:batch：启用/禁用/删除；缺失 id 静默跳过（镜像后端 updated 计数语义）。 */
export const mockBatchRules = (ruleIds: string[], action: AlertRuleBatchAction): void => {
  for (const cfg of Object.values(mockRulesByInstance)) {
    if (action === "delete") {
      cfg.rules = cfg.rules.filter((r) => !ruleIds.includes(r.rule_id));
    } else {
      for (const r of cfg.rules) {
        if (ruleIds.includes(r.rule_id)) {
          r.enabled = action === "enable";
          r.updated_at = nowIso();
        }
      }
    }
  }
  changeSeq += 1;
};

/* ------------------------- 历史告警预览（第二步弹窗） ------------------------- */

/** mock 档历史预览：镜像后端 alert_platform_mock.list_history 的语义——
 *  平台真历史（未命中/未接管的也在），按 类别多选/级别/近 N 天窗 过滤。
 *  数据源直接复用本文件事件样本（相对日期已保证 D1/D2 落 3 天窗、D5/D6 只落 7 天窗），
 *  行形态即 AlertEventWire；source 恒 "platform"（mock 不模拟平台故障，降级路径归后端测试）。 */
export const mockHistoryPreview = (params: {
  categories: string[];
  severity: string[];
  sinceDays: number;
  pageSize?: number;
}): { items: AlertEventWire[]; total: number; page: number; page_size: number; source: "platform" } => {
  const cutoff = Date.now() - params.sinceDays * 86_400_000;
  const rows = mockEvents
    .filter((e) =>
      (!params.categories.length || params.categories.includes(e.category))
      && (!params.severity.length || params.severity.includes(e.severity))
      && new Date(e.started_at).getTime() >= cutoff)
    .sort((a, b) => (a.started_at < b.started_at ? 1 : -1));
  const size = params.pageSize ?? 20;
  // 补预览专属字段（不动 mockEvents 契约）：企业 ID 交替 OP(32×8)/KWE(32×1)，
  // detail_url 按同分发规则拼假链（与后端 alarm_detail_url 同形，演示可点）。
  const ENT_OP = "8".repeat(32);
  const ENT_KWE = "1".repeat(32);
  return {
    items: rows.slice(0, size).map((e, i) => {
      const ent = i % 2 === 0 ? ENT_OP : ENT_KWE;
      const base = ent === ENT_OP ? "https://alarm-op.example/detail" : "https://alarm-kwe.example/detail";
      return { ...e, enterprise_id: ent, detail_url: `${base}?alarmCode=${encodeURIComponent(e.alert_no)}` };
    }),
    total: rows.length,
    page: 1,
    page_size: size,
    source: "platform",
  };
};
