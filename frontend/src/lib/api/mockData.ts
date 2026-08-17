/** 设计稿 demo 数据 —— mock 模式下驱动整份工作台。围绕「支付域延迟突增」事件。 */
import type {
  Me,
  AgentInstance,
  RcaCardData,
  WorkbenchState,
  Conversation,
  AdminTableData,
  SandboxCfg,
  AuditNode,
  Template,
  Workspace,
  ScopeApp,
  AssetRow,
  ConfigVersionRow,
  AdminModelAssetOption,
  AdminModelTemplate,
  ModelTemplateOption,
  MyLlmConfig,
} from "./types";

export const mockMe = (role: "user" | "platform_admin" = "user"): Me => ({
  user_id: role === "platform_admin" ? "admin" : "0026demo01",
  display_name: role === "platform_admin" ? "李四（管理员）" : "林一",
  role,
  whitelisted: true,
  initials: role === "platform_admin" ? "李" : "林",
  meta: role === "platform_admin" ? "平台管理员 · SRE 平台组" : "0026demo01 · 支付研发",
  has_instances: true,
  recent_instance_id: "agt_pay_fast_recovery",
});

export const mockAgents: AgentInstance[] = [
  {
    instance_id: "agt_pay_fast_recovery",
    name: "支付域感知快恢",
    template: "感知快恢 Agent · v3",
    workspace_id: "ws_pay_abc",
    workspace_label: "支付核心域（APP-A/B/C）",
    scope_revision: "rev-20260708-001",
    status: "active",
    active_config_version: "v4",
    counts: "2 Skill · 1 MCP",
  },
  {
    instance_id: "agt_gateway_watch",
    name: "网关健康守护",
    template: "感知快恢 Agent · v3",
    workspace_id: "ws_gw",
    workspace_label: "接入网关域（APP-G）",
    scope_revision: "rev-20260706-002",
    status: "active",
    active_config_version: "v2",
    counts: "1 Skill · 2 MCP",
  },
];

// ⚠ 不得加入 run_alert_* 会话——镜像后端 GET /agent-runs 默认过滤 entry_source='alert' 的语义；告警诊断会话只出现在 alerts 切片清单里
export const mockConversations: Conversation[] = [
  { id: "conv_1", title: "支付延迟突增诊断" },
  { id: "conv_2", title: "对账任务超时排查" },
  { id: "conv_3", title: "Redis 连接池告警" },
];

const mockActivityEvent = (
  eventId: string,
  eventType: string,
  occurredAt: string,
  message: string,
  payload: Record<string, unknown> = {},
) => ({
  event_id: eventId,
  audit_event_id: eventId,
  event_type: eventType,
  agent_run_id: "run_demo",
  task_id: "tsk_demo_multiagent",
  occurred_at: occurredAt,
  message,
  payload_redacted_json: { summary: message, ...payload },
});

/** 新活动栏离线演示：两轮派发、同角色重复 delegation、异常与待审批状态同时覆盖。 */
export const mockActivitySnapshot = {
  recent_events: [
    mockActivityEvent("evt-main-start", "openops.task.started", "2026-07-14T02:00:00Z", "任务已启动"),
    mockActivityEvent("evt-scope", "openops.scope.resolved", "2026-07-14T02:00:01Z", "范围已解析（3 个 APPID）"),
    mockActivityEvent("evt-b1-i-dispatch", "openops.subagent.dispatched", "2026-07-14T02:00:02Z", "派发巡检 Agent", {
      agent_key: "inspect", agent_label: "巡检 Agent", leader_task_id: "tsk_demo_multiagent",
      child_task_id: "tsk_demo_multiagent.inspect-a1", delegation_id: "dlg-inspect-a1",
      dispatch_batch_id: "batch-demo-1", dispatch_batch_no: 1, task_summary: "检查支付链路指标与健康状态",
    }),
    mockActivityEvent("evt-b1-i-tool", "openops.tool.call.succeeded", "2026-07-14T02:00:05Z", "指标查询完成", {
      agent_key: "inspect", agent_label: "巡检 Agent", delegation_id: "dlg-inspect-a1",
      dispatch_batch_id: "batch-demo-1", dispatch_batch_no: 1, tool: "query_metrics",
      display_label: "查询指标", result_summary: "P99 升至 1.4s，Redis active 连接 1000/1000",
      // 防御性 E2E：旧事件即使混入非白名单字段，活动组件也不得把 payload 整包 dump 到 DOM。
      arguments: { authorization: "Bearer dom-secret-must-not-render" },
      raw_response: "Cookie: dom-cookie-must-not-render",
    }),
    mockActivityEvent("evt-b1-i-report", "openops.subagent.reported", "2026-07-14T02:00:08Z", "巡检 Agent 已汇报", {
      agent_key: "inspect", agent_label: "巡检 Agent", delegation_id: "dlg-inspect-a1",
      dispatch_batch_id: "batch-demo-1", dispatch_batch_no: 1, report_summary: "确认 Redis 连接池饱和，流量未明显增长",
    }),
    mockActivityEvent("evt-b1-d-fail", "openops.subagent.failed", "2026-07-14T02:00:09Z", "日志 Agent 执行异常", {
      agent_key: "diagnose", agent_label: "日志 Agent", delegation_id: "dlg-diagnose-a1",
      dispatch_batch_id: "batch-demo-1", dispatch_batch_no: 1, reason_code: "MCP_TEMPORARY_UNAVAILABLE",
    }),
    mockActivityEvent("evt-b1-r-timeout", "openops.subagent.timeout", "2026-07-14T02:00:10Z", "恢复 Agent 执行超时", {
      agent_key: "recover", agent_label: "恢复 Agent", delegation_id: "dlg-recover-timeout",
      dispatch_batch_id: "batch-demo-1", dispatch_batch_no: 1,
    }),
    mockActivityEvent("evt-b2-i-dispatch", "openops.subagent.dispatched", "2026-07-14T02:01:00Z", "派发巡检 Agent 复核", {
      agent_key: "inspect", agent_label: "巡检 Agent", delegation_id: "dlg-inspect-a2",
      dispatch_batch_id: "batch-demo-2", dispatch_batch_no: 2, task_summary: "复核连接池趋势和副本差异",
    }),
    mockActivityEvent("evt-b2-i-report", "openops.subagent.reported", "2026-07-14T02:01:05Z", "巡检 Agent 已汇报", {
      agent_key: "inspect", agent_label: "巡检 Agent", delegation_id: "dlg-inspect-a2",
      dispatch_batch_id: "batch-demo-2", dispatch_batch_no: 2, report_summary: "问题集中在 svc-a，其他副本连接数正常",
    }),
    mockActivityEvent("evt-b2-r-dispatch", "openops.subagent.dispatched", "2026-07-14T02:01:01Z", "派发恢复 Agent", {
      agent_key: "recover", agent_label: "恢复 Agent", delegation_id: "dlg-recover-a1",
      dispatch_batch_id: "batch-demo-2", dispatch_batch_no: 2, task_summary: "准备受控重启 svc-a",
    }),
    mockActivityEvent("evt-b2-r-ask", "openops.approval.required", "2026-07-14T02:01:06Z", "等待人工批准重启 svc-a", {
      agent_key: "recover", agent_label: "恢复 Agent", delegation_id: "dlg-recover-a1",
      dispatch_batch_id: "batch-demo-2", dispatch_batch_no: 2, approval_request_id: "appr_1",
      tool: "recover_execute", display_label: "重启实例", request_id: "req-demo-1",
    }),
    mockActivityEvent("evt-b2-d-cancel", "openops.subagent.cancelled", "2026-07-14T02:01:07Z", "日志 Agent 任务已取消", {
      agent_key: "diagnose", agent_label: "日志 Agent", delegation_id: "dlg-diagnose-cancel",
      dispatch_batch_id: "batch-demo-2", dispatch_batch_no: 2,
    }),
  ],
  delegations: [
    { delegation_id: "dlg-inspect-a1", run_id: "run_demo", leader_task_id: "tsk_demo_multiagent", child_task_id: "tsk_demo_multiagent.inspect-a1", agent_key: "inspect", agent_label: "巡检 Agent", dispatch_batch_id: "batch-demo-1", dispatch_batch_no: 1, delegation_status: "completed", had_final_report: true, task_summary: "检查支付链路指标与健康状态", report_summary: "确认 Redis 连接池饱和，流量未明显增长", creation_date: "2026-07-14T02:00:02Z", last_update_date: "2026-07-14T02:00:08Z" },
    { delegation_id: "dlg-diagnose-a1", run_id: "run_demo", leader_task_id: "tsk_demo_multiagent", child_task_id: "tsk_demo_multiagent.diagnose-a1", agent_key: "diagnose", agent_label: "日志 Agent", dispatch_batch_id: "batch-demo-1", dispatch_batch_no: 1, delegation_status: "failed_no_report", had_final_report: false, task_summary: "聚类近 30 分钟错误日志", creation_date: "2026-07-14T02:00:02Z", last_update_date: "2026-07-14T02:00:09Z" },
    { delegation_id: "dlg-recover-timeout", run_id: "run_demo", leader_task_id: "tsk_demo_multiagent", child_task_id: "tsk_demo_multiagent.recover-timeout", agent_key: "recover", agent_label: "恢复 Agent", dispatch_batch_id: "batch-demo-1", dispatch_batch_no: 1, delegation_status: "timeout", had_final_report: false, task_summary: "验证恢复前置条件", creation_date: "2026-07-14T02:00:03Z", last_update_date: "2026-07-14T02:00:10Z" },
    { delegation_id: "dlg-inspect-a2", run_id: "run_demo", leader_task_id: "tsk_demo_multiagent", child_task_id: "tsk_demo_multiagent.inspect-a2", agent_key: "inspect", agent_label: "巡检 Agent", dispatch_batch_id: "batch-demo-2", dispatch_batch_no: 2, delegation_status: "completed", had_final_report: true, task_summary: "复核连接池趋势和副本差异", report_summary: "问题集中在 svc-a，其他副本连接数正常", creation_date: "2026-07-14T02:01:00Z", last_update_date: "2026-07-14T02:01:05Z" },
    { delegation_id: "dlg-recover-a1", run_id: "run_demo", leader_task_id: "tsk_demo_multiagent", child_task_id: "tsk_demo_multiagent.recover-a1", agent_key: "recover", agent_label: "恢复 Agent", dispatch_batch_id: "batch-demo-2", dispatch_batch_no: 2, delegation_status: "running", had_final_report: false, task_summary: "准备受控重启 svc-a", creation_date: "2026-07-14T02:01:01Z", last_update_date: "2026-07-14T02:01:06Z" },
    { delegation_id: "dlg-diagnose-cancel", run_id: "run_demo", leader_task_id: "tsk_demo_multiagent", child_task_id: "tsk_demo_multiagent.diagnose-cancel", agent_key: "diagnose", agent_label: "日志 Agent", dispatch_batch_id: "batch-demo-2", dispatch_batch_no: 2, delegation_status: "cancelled", had_final_report: false, task_summary: "补充聚类历史日志", creation_date: "2026-07-14T02:01:02Z", last_update_date: "2026-07-14T02:01:07Z" },
  ],
  events_next_cursor: "mock-activity-cursor",
  events_has_more: true,
};

/** mock 恢复闭环合成事件：mock send 900ms 回调把 demo 快照里的待审批 appr_1
 *  （evt-b2-r-ask，tool=recover_execute）推到 已批准 → 工具执行成功，时间线第六节点
 *  「恢复执行」全程演示 待审批 → 已执行。approved 对齐真实后端 payload（无 tool 字段，
 *  只按 approval_request_id 关联）；事件 id 固定，活动流按 event_id 去重天然幂等。 */
export const mockRecoveryClosureEvents = () => {
  const base = Date.now();
  const iso = (offsetMs: number) => new Date(base + offsetMs).toISOString();
  return [
    mockActivityEvent("evt-appr1-approved", "openops.approval.approved", iso(0), "审批已通过：重启 svc-a", {
      agent_key: "recover", agent_label: "恢复 Agent", delegation_id: "dlg-recover-a1",
      dispatch_batch_id: "batch-demo-2", dispatch_batch_no: 2, approval_request_id: "appr_1",
    }),
    mockActivityEvent("evt-appr1-tool-ok", "openops.tool.call.succeeded", iso(400), "恢复动作执行成功：svc-a 已重启", {
      agent_key: "recover", agent_label: "恢复 Agent", delegation_id: "dlg-recover-a1",
      dispatch_batch_id: "batch-demo-2", dispatch_batch_no: 2, tool: "recover_execute",
      display_label: "重启实例", result_summary: "svc-a 重启完成，active 连接回落 210/1000",
    }),
  ];
};

export const mockWorkbenchState = (): WorkbenchState => ({
  chatTitle: "支付延迟突增诊断",
  agentName: "支付域感知快恢",
  summaryText:
    "10:02 起支付下单 P99 从 180ms 升到 1.4s，错误率 0.6%。已诊断到 svc-payment-api 依赖的 Redis 连接饱和，正在验证假设 H1。",
  statusChips: [
    { key: "agui", label: "AG-UI", value: "已连接", tone: "good" },
    { key: "mcp", label: "MCP 服务", value: "2/2 在线", tone: "good" },
    { key: "omodel", label: "OModel", value: "已同步", tone: "good" },
    { key: "scope", label: "范围", value: "APP-A/B/C", tone: "neutral" },
  ],
  messages: [
    { id: "m1", role: "user", text: "支付下单接口刚才延迟突然变高，帮我看下是什么问题。" },
    {
      id: "m2",
      role: "bot",
      text: "我已拉取近 30 分钟的指标与日志，确认 10:02 起 svc-payment-api 的 P99 从 180ms 升到 1.4s、错误率 0.6%。范围锁定在支付核心域（APP-A/B/C）。下面是我的诊断进展。",
    },
    {
      id: "m3",
      role: "bot",
      text: "初步诊断：延迟与 Redis 连接池饱和高度相关（active 连接打满、等待队列上升）。正在验证是否由慢查询或连接泄漏引起。",
      showCopy: true,
    },
  ],
  rca: {
    title: "支付延迟突增",
    phaseLabel: "诊断中",
    time: "10:11",
    revision: 4,
    status: "in_progress",
    tiles: [
      { label: "症状", value: "下单 P99 180ms→1.4s" },
      { label: "时间窗", value: "10:02 起 · 持续 9min" },
      { label: "影响面", value: "APP-A 支付下单 · 0.6% 错误" },
      { label: "当前阶段", value: "诊断 · 验证 H1" },
    ],
    // done 步必给 summary（e2e 断言收起态一行摘要）；step 5 waiting 留空走 fallbackSummary。
    steps: [
      { num: 1, label: "范围", state: "done", summary: "范围锁定支付核心域（APP-A/B/C），主症状为下单 P99 突增" },
      { num: 2, label: "证据", state: "done", summary: "指标与日志确认 Redis active 连接打满，同期无发布无变更" },
      { num: 3, label: "假设", state: "done", summary: "生成 H1 连接泄漏 / H2 慢查询 / H3 流量突增三个假设" },
      { num: 4, label: "验证", state: "active", summary: "正在区分连接泄漏与慢查询占用：核对 svc-a 连接持有时长" },
      { num: 5, label: "结论", state: "waiting" },
    ],
    currentQ: "Redis 连接饱和是慢查询导致，还是连接泄漏导致？",
    why: "两者的恢复动作不同：慢查询→限流/优化，连接泄漏→重启释放；需先验证再给恢复建议。",
    facts: [
      { text: "10:02 P99 由 180ms 升至 1.4s，错误率 0.6%" },
      { text: "svc-payment-api → Redis active 连接打满（1000/1000）" },
      { text: "同期无发布、无配置变更" },
    ],
    unknowns: [
      { text: "连接是否泄漏（未释放）还是被慢查询长期占用" },
      { text: "是否单实例问题（svc-a）或全副本" },
    ],
    sources: [
      { name: "Prometheus", status: "done", tone: "good" },
      { name: "Loki", status: "running", tone: "warning" },
      { name: "oModel 拓扑", status: "done", tone: "good" },
    ],
    hypotheses: [
      { text: "H1 Redis 连接泄漏（svc-a 未释放）", tag: "支持", tagTone: "good", conf: 0.72 },
      { text: "H2 下游慢查询占用连接", tag: "部分支持", tagTone: "warning", conf: 0.41 },
      { text: "H3 流量突增打满连接池", tag: "反证", tagTone: "danger", conf: 0.12 },
    ],
    actions: [
      { tier: "立即", text: "重启 svc-payment-api 实例 svc-a 释放连接", confirm: true, impact: "3 实例", status: "待确认", statusTone: "warning" },
      { tier: "短期", text: "对 Redis 连接加 max-idle 与超时回收", impact: "配置", status: "建议", statusTone: "neutral" },
      { tier: "长期", text: "补充连接池饱和告警与自动扩容", impact: "预案", status: "建议", statusTone: "neutral" },
    ],
    conclusion:
      "根因倾向 H1（连接泄漏）：重启 svc-a 后若连接数回落且 P99 恢复即可确认；否则回到 H2 验证慢查询。",
  },
  hitl: {
    approval_request_id: "appr_1",
    title: "需要人工批准",
    tool: "recover_execute",
    summary: "Agent 建议重启 svc-payment-api 的实例 svc-a 以释放饱和的 Redis 连接。该动作会中断 svc-a 当前处理中的请求。",
    facts: [
      { label: "工具", value: "recover_execute" },
      { label: "目标 APPID / 资源", value: "APP-A · svc-payment-api/svc-a" },
      { label: "影响说明", value: "重启期间 svc-a 短暂不可用（约 15s），流量自动切副本" },
      { label: "发起人", value: "支付域感知快恢 · 恢复子 Agent" },
    ],
    countdown: "4:38",
    status: "pending",
    tone: "warning",
  },
  activity: [
    {
      label: "进行中",
      items: [
        { id: "a1", title: "诊断 · 日志聚类", tool: "skill.logscan", detail: "Loki 拉取 svc-payment-api 近 30min 错误日志", time: "刚刚", icon: "loader-2", tone: "neutral", running: true },
      ],
    },
    {
      label: "10:02 – 10:11",
      items: [
        { id: "a2", title: "范围解析", tool: "scope.resolved", detail: "effective_appids = APP-A/B/C（rev-20260708-001）", time: "10:02", icon: "target", tone: "good" },
        { id: "a3", title: "巡检 · 指标查询", tool: "mcp.query_metrics", detail: "P99 / 错误率 / Redis 连接数", time: "10:03", icon: "chart-line", tone: "good" },
        { id: "a4", title: "诊断 · 拓扑依赖", tool: "mcp.omodel_topo", detail: "svc-payment-api → Redis / MySQL 依赖图", time: "10:06", icon: "sitemap", tone: "good" },
        { id: "a5", title: "假设生成", tool: "reasoning", detail: "H1/H2/H3 排行，置信度更新", time: "10:09", icon: "bulb", tone: "neutral" },
        { id: "a6", title: "ASK · 等待批准", tool: "approval.required", detail: "recover_execute 重启 svc-a", time: "10:11", icon: "shield-check", tone: "warning" },
      ],
    },
  ],
  activitySnapshot: mockActivitySnapshot,
  skills: [
    { skill_id: "skill_inspection", name: "/inspect", desc: "巡检：健康状态与异常信号" },
    { skill_id: "skill_logscan", name: "/logscan", desc: "日志聚类与错误归并" },
    { skill_id: "skill_topo", name: "/topo", desc: "资源拓扑与依赖分析" },
    { skill_id: "skill_recover", name: "/recover", desc: "受控恢复动作（需审批）" },
  ],
  models: [
    { llm_config_id: "llm_platform_default", label: "平台默认（Qwen3.5）", note: "平台提供 · 128k", current: true, available: true },
    { llm_config_id: "llm_user_gpt4o", label: "我的 GPT-4o", note: "OpenAI 兼容 · 64k", available: true },
    { llm_config_id: "llm_user_noTool", label: "本地 Llama", note: "不支持 tool calling", available: false, reason: "不支持 tool calling" },
  ],
  currentModel: "Qwen3.5",
});

/** mock 诊断完成态变体：mock send 两段式推进的第二拍（进行中 → 900ms 后闭环定格）。
 *  五步全 done、status=concluded（后端权威信号的 mock 对位）、需确认动作已执行 →
 *  时间线 footer 主/副按钮都应隐藏。 */
export const mockRcaFinal = (): RcaCardData => {
  const base = mockWorkbenchState().rca!;
  // 终态每步小结：4/5 两步补上验证结论与根因报告（与后端 step_summary 聚合口径对位）。
  const finalStepSummaries: Record<number, string> = {
    4: "重启 svc-a 后 active 连接回落、P99 恢复 190ms，H1 验证闭环",
    5: "根因确认 H1 连接泄漏；短期加连接回收配置，长期补饱和告警",
  };
  return {
    ...base,
    phaseLabel: "诊断完成",
    revision: 5,
    status: "concluded",
    steps: base.steps.map((step) => ({
      ...step,
      state: "done",
      summary: finalStepSummaries[step.num] ?? step.summary,
    })),
    tiles: base.tiles.map((tile) =>
      tile.label === "当前阶段" ? { ...tile, value: "结论 · 已完成" } : tile),
    currentQ: "诊断完成：根因确认为 H1（Redis 连接泄漏，svc-a 未释放）。",
    why: "重启 svc-a 后 active 连接回落、P99 恢复至 190ms，验证闭环，无需继续追问。",
    sources: base.sources.map((source) => ({ ...source, status: "done", tone: "good" })),
    hypotheses: [
      { text: "H1 Redis 连接泄漏（svc-a 未释放）", tag: "已证实", tagTone: "good", conf: 0.93 },
      { text: "H2 下游慢查询占用连接", tag: "已排除", tagTone: "neutral", conf: 0.08 },
      { text: "H3 流量突增打满连接池", tag: "反证", tagTone: "danger", conf: 0.04 },
    ],
    actions: base.actions.map((action) =>
      action.confirm ? { ...action, status: "已执行", statusTone: "good" } : action),
    conclusion:
      "根因 H1（连接泄漏）已确认：重启 svc-a 后 active 连接回落至 210/1000、P99 恢复 190ms。建议跟进短期（连接回收配置）与长期（饱和告警 + 自动扩容）加固项。",
  };
};

/* ------------------------- 实例配置（30.5） ------------------------- */
export const mockModels = [
  { llm_config_id: "llm_platform_default", label: "Qwen3.5", note: "平台提供 · 128k · 支持 tool calling", current: true, available: true },
  { llm_config_id: "llm_user_gpt4o", label: "我的 GPT-4o", note: "OpenAI 兼容 · 64k · 我的 SecretRef", current: false, available: true },
];

/** 「我的模型」管理页 mock（可变数组：mock 下改/删后重拉即反映，同 mockModelTemplates 口径）。
 *  两行刻意一有 header 一无 header，覆盖「已配 / 未配」两种卡片展示。 */
export const mockMyLlmConfigs: MyLlmConfig[] = [
  {
    llm_config_id: "llm_user_gpt4o", display_name: "我的 GPT-4o", base_url: "https://api.openai.com/v1",
    model_name: "gpt-4o", context_window_tokens: 65536, supports_tool_calling: true, status: "active",
    extra_headers: { "X-Tenant-Id": "pay-core" },
  },
  {
    llm_config_id: "llm_user_glm", display_name: "内网 GLM", base_url: "https://glm.internal.example.com/v1",
    model_name: "glm-5.1", context_window_tokens: 128000, supports_tool_calling: true, status: "active",
    extra_headers: {},
  },
];

export const mockBoundSkills: AssetRow[] = [
  { id: "bind_logscan", name: "日志聚类 logscan", version: "v2", status: "enabled", statusTone: "good", meta: "Skill · 我的 · main", bound: true, kind: "skill", assetId: "skill_user_logscan" },
];
export const mockSkillLibrary: AssetRow[] = [
  // inspection/logscan 保留裸名 skillKey（29.9 前存量形态）；traceparse 用命名空间化新形态（29.9），
  // 两形态并存即真实世界——顺带覆盖长 key 在插件页/管理台的展示。
  { id: "skill_inspection", name: "巡检 inspection", version: "v2", status: "active", statusTone: "good", meta: "平台 Skill · 更新于 07-06", bound: false, kind: "skill", skillKey: "inspection" },
  { id: "skill_user_logscan", name: "日志聚类 logscan", version: "v2", status: "active", statusTone: "good", meta: "我的 Skill · 更新于 07-06", bound: true, kind: "skill", skillKey: "logscan" },
  { id: "skill_user_traceparse", name: "链路解析 traceparse", version: "v1", status: "active", statusTone: "good", meta: "我的 Skill · 更新于 07-01", bound: false, kind: "skill", skillKey: "user-0026demo01-traceparse" },
];
export const mockMcpLibrary: AssetRow[] = [
  { id: "mcp_alarm_server", name: "alarm-server", version: "v1", status: "active", statusTone: "good", meta: "平台 MCP · 告警列表/详情查询", bound: false, kind: "mcp" },
  { id: "mcp_user_cmdb", name: "CMDB 查询 MCP", version: "v1", status: "active", statusTone: "good", meta: "我的 MCP · https://cmdb.example.com", bound: false, kind: "mcp" },
];
export const mockConfigVersions: ConfigVersionRow[] = [
  { version_no: "v4", config_version_id: "cfg_...a4", status: "active", change_reason: "绑定 logscan v2", created_by: "0026demo01", creation_date: "07-08 10:20" },
  { version_no: "v3", config_version_id: "cfg_...a3", status: "archived", change_reason: "追加 main role", created_by: "0026demo01", creation_date: "07-07 16:02" },
  { version_no: "v2", config_version_id: "cfg_...a2", status: "archived", change_reason: "切换实例默认模型", created_by: "0026demo01", creation_date: "07-06 09:30" },
];

/* ---------------------- 模型模板（38 号）mock ---------------------- */
/** 模板编辑弹窗的槽位候选（可变数组：管理台 mock 写闭环的真源）。 */
export const mockModelAssets: AdminModelAssetOption[] = [
  // glm-5.1 带自定义 Header：覆盖资产编辑弹窗的「已配 header」回填路径
  { model_asset_id: "ma_glm51", model_id: "glm-5.1", display_name: "GLM-5.1", status: "active",
    base_url: "https://glm.internal.example.com/v1",
    secret_fingerprint: "fp_a1b2c3d4e5f6", has_secret: true,
    context_window_tokens: 128000, extra_headers: { "X-Tenant-Id": "sre-platform" } },
  // 未配 Key：覆盖编辑弹窗「Key 框留空 + 无指纹」这一分支
  { model_asset_id: "ma_qwen35", model_id: "qwen3.5-instruct", display_name: "Qwen3.5", status: "active",
    base_url: "", secret_fingerprint: null, has_secret: false,
    context_window_tokens: 128000, extra_headers: {} },
  { model_asset_id: "ma_deepseek", model_id: "deepseek-chat", display_name: "DeepSeek-V3", status: "active",
    base_url: "https://api.deepseek.com/v1",
    secret_fingerprint: "fp_9f8e7d6c5b4a", has_secret: true,
    context_window_tokens: 65536, extra_headers: {} },
  { model_asset_id: "ma_txllm", model_id: "tx-llm-v2", display_name: "交易大模型-TX", status: "active",
    base_url: "https://tx.internal.example.com/v1",
    secret_fingerprint: "fp_0011223344ff", has_secret: true,
    context_window_tokens: 128000, extra_headers: {} },
];

export const mockModelTemplates: AdminModelTemplate[] = [
  { model_template_id: "mtpl_balanced", display_name: "均衡（推荐）", description: "主 / 子 Agent 均使用 GLM-5.1，效果优先",
    main_model_asset_id: "ma_glm51", main_model_id: "glm-5.1", main_model_name: "GLM-5.1",
    sub_model_asset_id: "ma_glm51", sub_model_id: "glm-5.1", sub_model_name: "GLM-5.1",
    access_scope: "all", grant_count: 0, is_default: true, status: "active" },
  { model_template_id: "mtpl_economy", display_name: "经济", description: "主 GLM-5.1 + 子 Qwen3.5，子任务省成本",
    main_model_asset_id: "ma_glm51", main_model_id: "glm-5.1", main_model_name: "GLM-5.1",
    sub_model_asset_id: "ma_qwen35", sub_model_id: "qwen3.5-instruct", sub_model_name: "Qwen3.5",
    access_scope: "all", grant_count: 0, is_default: false, status: "active" },
  // restricted 演示（38.1：授权在模板维度）：镜像 seed「交易专用（受限演示）」
  { model_template_id: "mtpl_tx_restricted", display_name: "交易专用（受限演示）",
    description: "主 GLM-5.1 + 子 交易大模型-TX，部门私有组合仅白名单用户可用",
    main_model_asset_id: "ma_glm51", main_model_id: "glm-5.1", main_model_name: "GLM-5.1",
    sub_model_asset_id: "ma_txllm", sub_model_id: "tx-llm-v2", sub_model_name: "交易大模型-TX",
    access_scope: "restricted", grant_count: 1, is_default: false, status: "active" },
  { model_template_id: "mtpl_retired", display_name: "旧组合（停用）", description: "",
    main_model_asset_id: "ma_deepseek", main_model_id: "deepseek-chat", main_model_name: "DeepSeek-V3",
    sub_model_asset_id: "ma_deepseek", sub_model_id: "deepseek-chat", sub_model_name: "DeepSeek-V3",
    access_scope: "all", grant_count: 0, is_default: false, status: "disabled" },
];

/** mock 的模板白名单（38.1 写闭环：GrantsDialog 读写；real 的 user_ids 来自后端）。 */
export const mockModelTemplateGrants: Record<string, string[]> = {
  mtpl_tx_restricted: ["0026demo01"],
};

/** AdminModelTemplate → 用户侧选项（mock 专用；real 由后端模板级 ACL 过滤后下发，仅 active 行）。 */
export const toUserModelTemplate = (t: AdminModelTemplate): ModelTemplateOption => ({
  model_template_id: t.model_template_id,
  display_name: t.display_name,
  description: t.description || undefined,
  main_model: { model_id: t.main_model_id, display_name: t.main_model_name || t.main_model_id },
  sub_model: { model_id: t.sub_model_id, display_name: t.sub_model_name || t.sub_model_id },
  access_scope: t.access_scope,
  is_default: t.is_default,
  status: t.status,
});

/** 管理台「模型模板」表（real/mock 共用同一构建器，保证两模式表形状一致）。
 * 启/停编码进 onClickKey（mt-disable/mt-enable）：onCellAction 只有 rowId，拿不到行状态。
 * 38.1：授权范围列（全员开放/限 N 人）+「白名单授权」动作（原模型资产页的授权入口整体迁到本页）。 */
/** 模型资产表（real 与 mock 共用，防两处列数漂移）。protocol/registered_by 在 mock 侧无此字段，
 *  按平台默认呈现即可——两态列结构必须一致，否则 mock 下的列错位极难排查。 */
export const buildModelAssetTable = (
  rows: (AdminModelAssetOption & { protocol?: string; registered_by?: string })[],
): AdminTableData => ({
  title: "模型资产",
  primary: { label: "注册模型接口", icon: "plus", actionKey: "register-model" },
  cols: [{ label: "模型名称" }, { label: "协议" }, { label: "model_id" }, { label: "归属" },
         { label: "自定义 Header", width: "112px" }, { label: "状态", width: "96px" },
         { label: "编辑", width: "56px" }, { label: "删除", width: "56px" }],
  rows: rows.map((m) => {
    const n = Object.keys(m.extra_headers || {}).length;
    return {
      id: m.model_asset_id,
      cells: [
        { text: m.display_name },
        { text: !m.protocol || m.protocol === "openai_compatible" ? "OpenAI 兼容" : m.protocol },
        { text: m.model_id, mono: true },
        { text: !m.registered_by || m.registered_by === "system" ? "平台" : m.registered_by },
        n > 0 ? { text: `${n} 个`, kind: "badge" as const, tone: "good" as const } : { text: "—" },
        { text: m.status, kind: "badge" as const, tone: m.status === "active" ? "good" as const : "neutral" as const },
        { text: "编辑", kind: "action" as const, onClickKey: "ma-edit" },
        { text: "删除", kind: "action" as const, onClickKey: "ma-delete" },
      ],
    };
  }),
});

export const buildModelTemplateTable = (rows: AdminModelTemplate[]): AdminTableData => ({
  title: "模型模板",
  primary: { label: "新建模板", icon: "plus", actionKey: "new-model-template" },
  cols: [{ label: "模板名" }, { label: "主 Agent 模型" }, { label: "子 Agent 模型" },
         { label: "授权范围" }, { label: "默认", width: "64px" }, { label: "状态", width: "80px" },
         { label: "授权", width: "88px" }, { label: "编辑", width: "56px" },
         { label: "启停", width: "56px" }, { label: "设默认", width: "72px" }, { label: "删除", width: "56px" }],
  rows: rows.map((t) => ({
    id: t.model_template_id,
    cells: [
      { text: t.display_name },
      { text: t.main_model_name || t.main_model_id },
      { text: t.sub_model_name || t.sub_model_id },
      t.access_scope === "all"
        ? { text: "全员开放", kind: "badge" as const, tone: "good" as const }
        : { text: `限 ${t.grant_count} 人`, kind: "badge" as const, tone: "warning" as const },
      t.is_default ? { text: "默认", kind: "badge" as const, tone: "good" as const } : { text: "—" },
      { text: t.status, kind: "badge" as const, tone: t.status === "active" ? "good" as const : "neutral" as const },
      { text: "白名单授权", kind: "action" as const, onClickKey: "mt-grants" },
      { text: "编辑", kind: "action" as const, onClickKey: "mt-edit" },
      t.status === "active"
        ? { text: "停用", kind: "action" as const, onClickKey: "mt-disable" }
        : { text: "启用", kind: "action" as const, onClickKey: "mt-enable" },
      t.is_default ? { text: "—" } : { text: "设默认", kind: "action" as const, onClickKey: "mt-default" },
      { text: "删除", kind: "action" as const, onClickKey: "mt-delete" },
    ],
  })),
});

/* --------------------------- 管理台（30.6） --------------------------- */
export const adminTables: Record<string, AdminTableData> = {
  // 38.1：授权范围/白名单授权已迁「模型模板」页——本表只剩资产基础列（补齐原 mock 空隙：
  // 此前无 model-assets 键，mock 下模型资产页会错误回退显示 templates 表）
  // 行从 mockModelAssets 现算（同 buildModelTemplateTable 口径）：注册/编辑/删除后重拉即反映，
  // 也免去硬编码行与资产数组两处漂移（此前 mock 表少了 DeepSeek 那行）
  "model-assets": {
    title: "模型资产",
    primary: { label: "注册模型接口", icon: "plus", actionKey: "register-model" },
    cols: [{ label: "模型名称" }, { label: "协议" }, { label: "model_id" }, { label: "归属" }, { label: "状态", width: "96px" }, { label: "编辑", width: "56px" }, { label: "删除", width: "56px" }],
    rows: [],
  },
  templates: {
    title: "模板管理",
    cols: [{ label: "模板名" }, { label: "template_key" }, { label: "状态" }, { label: "active 版本" }, { label: "操作", width: "148px" }],
    rows: [
      { id: "tpl_sre_fast_recovery", cells: [
        { text: "感知快恢 Agent" }, { text: "sensai_fast_recovery", mono: true },
        { text: "active", kind: "badge", tone: "good" }, { text: "v3" },
        { text: "资产治理", kind: "action", onClickKey: "open-template" },
        { text: "编辑", kind: "action", onClickKey: "edit-template" } ] },
      { id: "tpl_draft", cells: [
        { text: "网络诊断 Agent" }, { text: "net_diag", mono: true },
        { text: "draft", kind: "badge", tone: "neutral" }, { text: "—" },
        { text: "资产治理", kind: "action", onClickKey: "open-template" },
        { text: "编辑", kind: "action", onClickKey: "edit-template" } ] },
    ],
  },
  "mcp-tools": {
    title: "MCP Tool 标注",
    tabs: [{ key: "all", label: "全部" }, { key: "unreviewed", label: "未标注" }],
    cols: [{ label: "tool_name" }, { label: "所属 MCP" }, { label: "标注状态" }, { label: "操作", width: "88px" }],
    rows: [
      { id: "query_resource", cells: [
        { text: "query_resource", mono: true }, { text: "mcp_omodel_query" },
        { text: "allowed", kind: "badge", tone: "good" }, { text: "编辑标注", kind: "action", onClickKey: "annotate" } ] },
      { id: "recover_execute", cells: [
        { text: "recover_execute", mono: true }, { text: "mcp_recovery" },
        { text: "allowed · 需审批", kind: "badge", tone: "warning" }, { text: "编辑标注", kind: "action", onClickKey: "annotate" } ] },
      { id: "raw_shell", cells: [
        { text: "raw_shell", mono: true }, { text: "mcp_user_metrics" },
        { text: "未标注 → 运行时 block", kind: "badge", tone: "danger" }, { text: "标注", kind: "action", onClickKey: "annotate" } ] },
    ],
  },
  assets: {
    title: "平台资产治理",
    primary: { label: "刷新对账", icon: "refresh", actionKey: "refresh-assets" },
    tabs: [{ key: "skill", label: "Skill" }, { key: "mcp", label: "MCP" }],
    cols: [{ label: "名称" }, { label: "来源" }, { label: "最新版本" }, { label: "状态" }, { label: "tool 发现" }],
    rows: [
      { id: "skill_inspection", cells: [
        { text: "巡检 inspection" }, { text: "platform" }, { text: "v2" },
        { text: "active", kind: "badge", tone: "good" }, { text: "—" } ] },
      { id: "mcp_omodel_query", cells: [
        { text: "oModel 查询" }, { text: "platform" }, { text: "v3" },
        { text: "active", kind: "badge", tone: "good" }, { text: "8 tools" } ] },
    ],
  },
  skills: {
    title: "Skill 基线",
    primary: { label: "上传 Skill", icon: "upload", actionKey: "upload-skill" },
    cols: [{ label: "名称" }, { label: "skill_key" }, { label: "版本" }, { label: "更新时间" }, { label: "状态", width: "88px" }, { label: "删除", width: "56px" }],
    rows: [
      { id: "skill_inspection", cells: [
        { text: "巡检 inspection" }, { text: "inspection", mono: true }, { text: "2.0.0" },
        { text: "2026-07-20 10:30" }, { text: "active", kind: "badge", tone: "good" },
        { text: "删除", kind: "action", onClickKey: "skill-delete" } ] },
    ],
  },
  // ⚠ 这一项不能少：getAdminTable 末尾是 `M.adminTables[key] ?? M.adminTables.templates`，
  // 缺 mock 条目会让 mock 模式下 MCP 服务页静默显示成模板表（model-assets 曾踩过同一个坑）。
  mcps: {
    title: "MCP 服务",
    primary: { label: "注册 MCP", icon: "plus", actionKey: "register-mcp" },
    cols: [{ label: "服务名称" }, { label: "endpoint" }, { label: "分类", width: "96px" }, { label: "状态", width: "88px" }, { label: "删除", width: "56px" }],
    rows: [
      { id: "mcp_omodel_query", cells: [
        { text: "oModel 查询与恢复" }, { text: "https://omode…", mono: true }, { text: "监控" },
        { text: "active", kind: "badge", tone: "good" },
        { text: "删除", kind: "action", onClickKey: "mcp-delete" } ] },
    ],
  },
  users: {
    title: "用户与白名单",
    primary: { label: "加入白名单", icon: "plus", actionKey: "add-user" },
    cols: [{ label: "user_id" }, { label: "展示名" }, { label: "标签" }, { label: "role" }, { label: "白名单" }, { label: "最近登录" }],
    rows: [
      { id: "0026demo01", cells: [
        { text: "0026demo01", mono: true }, { text: "林一" },
        { text: "研发", kind: "action", onClickKey: "user-tags" }, { text: "user" },
        { text: "active", kind: "badge", tone: "good" }, { text: "10:00" } ] },
      { id: "admin", cells: [
        { text: "admin", mono: true }, { text: "李四" },
        { text: "设标签", kind: "action", onClickKey: "user-tags" }, { text: "platform_admin" },
        { text: "active", kind: "badge", tone: "good" }, { text: "09:30" } ] },
    ],
  },
};

/** getAdminMcpTools().raw 的 mock 数据源（同名冲突根治场景）：两家 server（omodel-mcp-server /
 * opsdfx-mcp）各有 allowed 的同名 query_resource + recover_execute（复现内网事故），外加各一个
 * 独有工具与一条未标注行。前端按「server::tool」复合键分组，勾 A 家不再联动 B 家。
 * adminTables["mcp-tools"] 亦由此派生，避免两份数据漂移。 */
export const adminMcpToolsRaw: Record<string, unknown>[] = [
  { tool_catalog_id: "tc_omodel_qr", tool_name: "query_resource", mcp_display_name: "omodel-mcp-server",
    mcp_version_id: "v_omodel", annotation_id: "an_omodel_qr", annotation_status: "allowed", is_approval_required: false },
  { tool_catalog_id: "tc_omodel_re", tool_name: "recover_execute", mcp_display_name: "omodel-mcp-server",
    mcp_version_id: "v_omodel", annotation_id: "an_omodel_re", annotation_status: "allowed", is_approval_required: true },
  { tool_catalog_id: "tc_omodel_topo", tool_name: "query_topology", mcp_display_name: "omodel-mcp-server",
    mcp_version_id: "v_omodel", annotation_id: "an_omodel_topo", annotation_status: "allowed", is_approval_required: false },
  { tool_catalog_id: "tc_opsdfx_qr", tool_name: "query_resource", mcp_display_name: "opsdfx-mcp",
    mcp_version_id: "v_opsdfx", annotation_id: "an_opsdfx_qr", annotation_status: "allowed", is_approval_required: false },
  { tool_catalog_id: "tc_opsdfx_re", tool_name: "recover_execute", mcp_display_name: "opsdfx-mcp",
    mcp_version_id: "v_opsdfx", annotation_id: "an_opsdfx_re", annotation_status: "allowed", is_approval_required: true },
  { tool_catalog_id: "tc_opsdfx_shell", tool_name: "raw_shell", mcp_display_name: "opsdfx-mcp",
    mcp_version_id: "v_opsdfx", annotation_id: null, annotation_status: "unreviewed", is_approval_required: false },
];

/** getAdminTemplateDetail() 的 mock：main.default_tools 混入①唯一归属裸名(query_topology→升级为
 * omodel 复合键，令 omodel 起始为「部分」)、②目录外残留名(ghost_tool)——覆盖读时归一化的升级/
 * 残留分支，且让 omodel 起始未满、opsdfx 起始未选，使「勾 omodel 不联动 opsdfx」的核心回归可断言。 */
export const mockTemplateDetail = {
  template: { template_id: "tpl_sre_fast_recovery", display_name: "感知快恢 Agent" },
  active_version: {
    template_version_id: "tv_active", version_no: 2, status: "active",
    content_json: {
      main: {
        role: "理解用户任务，调度巡检/诊断/恢复能力。",
        default_tools: ["query_topology", "ghost_tool"],
        skills: [],
      },
      sub_agents: [{ key: "inspect", label: "巡检", role: "巡检", skills: [], mcp_tools: ["query_resource"] }],
    },
  },
  draft_version: null as Record<string, unknown> | null,
};

export const sandboxCfg: SandboxCfg[] = [
  { key: "max_user_containers_per_host", desc: "单机最大用户容器数", val: "26" },
  { key: "per_user_running_task_limit", desc: "每用户最多 running task", val: "2" },
  { key: "user_container_idle_ttl_minutes", desc: "idle 容器保留时间", val: "15" },
  { key: "capacity_full_policy", desc: "容量满策略（V1 固定 strict_ttl）", val: "strict_ttl" },
  { key: "container_cpu_limit", desc: "新建容器 CPU 限额", val: "0.5" },
  { key: "container_memory_limit_mib", desc: "新建容器内存限额", val: "2048" },
];

export const auditTimeline: AuditNode[] = [
  { event: "task.started", detail: "task_id=tsk_… · 支付延迟突增诊断" },
  { event: "scope.resolved", detail: "snapshot_id=snap_… · effective_appids=APP-A/B/C" },
  { event: "tool.call.started → allowed", detail: "query_metrics · request_id=req_…" },
  { event: "approval.required", detail: "recover_execute · appr_1" },
  { event: "approval.approved", detail: "by 0026demo01 · 10:12" },
  { event: "task.completed", detail: "duration=612s" },
];

/* -------------------------- 初始化向导（30.2） -------------------------- */
export const mockTemplates: Template[] = [
  {
    template_version_id: "tplv_sre_fast_recovery_3",
    name: "感知快恢 Agent",
    desc: "面向 SRE 巡检 / 诊断 / 恢复闭环的平台模板，V1 唯一可用模板。",
    capabilities: ["巡检", "诊断", "恢复"],
    active_version: "v3",
  },
];

export const mockWorkspaces: Workspace[] = [
  { workspace_id: "ws_pay_abc", name: "支付核心域", scope_revision: "rev-20260708-001", sync_status: "ready", updated: "07-08 09:50" },
];

export const mockScopeApps: ScopeApp[] = [
  { app_id: "00000000000000000000000000000423", name: "日志管理分析(多租)", type: "HIS-OP" },
  { app_id: "00000000000000000000000000000425", name: "统一查询服务", type: "HIS-OP" },
  { app_id: "00000000000000000000000000000601", name: "支付核心交易", type: "HIS-OP" },
  { app_id: "00000000000000000000000000000602", name: "订单履约中心", type: "HIS-OP" },
];

// workspace → 已选 app_ids（编辑预填用；用真实存在于 mockScopeApps 的 id 使预勾选点亮）
export const mockWorkspaceApps: Record<string, string[]> = {
  ws_pay_abc: ["00000000000000000000000000000601", "00000000000000000000000000000602"],
};
