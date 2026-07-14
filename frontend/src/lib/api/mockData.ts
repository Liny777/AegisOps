/** 设计稿 demo 数据 —— mock 模式下驱动整份工作台。围绕「支付域延迟突增」事件。 */
import type {
  Me,
  AgentInstance,
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

export const mockConversations: Conversation[] = [
  { id: "conv_1", title: "支付延迟突增定界" },
  { id: "conv_2", title: "对账任务超时排查" },
  { id: "conv_3", title: "Redis 连接池告警" },
];

export const mockWorkbenchState = (): WorkbenchState => ({
  chatTitle: "支付延迟突增定界",
  agentName: "支付域感知快恢",
  summaryText:
    "10:02 起支付下单 P99 从 180ms 升到 1.4s，错误率 0.6%。已定界到 svc-payment-api 依赖的 Redis 连接饱和，正在验证假设 H1。",
  statusChips: [
    { key: "agui", label: "AG-UI", value: "已连接", tone: "good" },
    { key: "mcp", label: "MCP 服务", value: "2/2 在线", tone: "good" },
    { key: "omodel", label: "oModel", value: "已同步", tone: "good" },
    { key: "scope", label: "范围", value: "APP-A/B/C", tone: "neutral" },
  ],
  messages: [
    { id: "m1", role: "user", text: "支付下单接口刚才延迟突然变高，帮我看下是什么问题。" },
    {
      id: "m2",
      role: "bot",
      text: "我已拉取近 30 分钟的指标与日志，确认 10:02 起 svc-payment-api 的 P99 从 180ms 升到 1.4s、错误率 0.6%。范围锁定在支付核心域（APP-A/B/C）。下面是我的定界进展。",
    },
    {
      id: "m3",
      role: "bot",
      text: "初步定界：延迟与 Redis 连接池饱和高度相关（active 连接打满、等待队列上升）。正在验证是否由慢查询或连接泄漏引起。",
      showCopy: true,
    },
  ],
  rca: {
    title: "支付延迟突增",
    phaseLabel: "定界中",
    time: "10:11",
    tiles: [
      { label: "症状", value: "下单 P99 180ms→1.4s" },
      { label: "时间窗", value: "10:02 起 · 持续 9min" },
      { label: "影响面", value: "APP-A 支付下单 · 0.6% 错误" },
      { label: "当前阶段", value: "定界 · 验证 H1" },
    ],
    steps: [
      { num: 1, label: "范围", state: "done" },
      { num: 2, label: "证据", state: "done" },
      { num: 3, label: "假设", state: "done" },
      { num: 4, label: "验证", state: "active" },
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
        { id: "a1", title: "定界 · 日志聚类", tool: "skill.logscan", detail: "Loki 拉取 svc-payment-api 近 30min 错误日志", time: "刚刚", icon: "loader-2", tone: "neutral", running: true },
      ],
    },
    {
      label: "10:02 – 10:11",
      items: [
        { id: "a2", title: "范围解析", tool: "scope.resolved", detail: "effective_appids = APP-A/B/C（rev-20260708-001）", time: "10:02", icon: "target", tone: "good" },
        { id: "a3", title: "巡检 · 指标查询", tool: "mcp.query_metrics", detail: "P99 / 错误率 / Redis 连接数", time: "10:03", icon: "chart-line", tone: "good" },
        { id: "a4", title: "定界 · 拓扑依赖", tool: "mcp.omodel_topo", detail: "svc-payment-api → Redis / MySQL 依赖图", time: "10:06", icon: "sitemap", tone: "good" },
        { id: "a5", title: "假设生成", tool: "reasoning", detail: "H1/H2/H3 排行，置信度更新", time: "10:09", icon: "bulb", tone: "neutral" },
        { id: "a6", title: "ASK · 等待批准", tool: "approval.required", detail: "recover_execute 重启 svc-a", time: "10:11", icon: "shield-check", tone: "warning" },
      ],
    },
  ],
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

/* ------------------------- 实例配置（30.5） ------------------------- */
export const mockModels = [
  { llm_config_id: "llm_platform_default", label: "平台默认 · Qwen3.5", note: "平台提供 · 128k · 支持 tool calling", current: true, available: true },
  { llm_config_id: "llm_user_gpt4o", label: "我的 GPT-4o", note: "OpenAI 兼容 · 64k · 我的 SecretRef", current: false, available: true },
];

export const mockBoundSkills: AssetRow[] = [
  { id: "bind_logscan", name: "日志聚类 logscan", version: "v2", status: "enabled", statusTone: "good", meta: "Skill · 我的 · main", bound: true, kind: "skill", assetId: "skill_user_logscan" },
];
export const mockSkillLibrary: AssetRow[] = [
  { id: "skill_inspection", name: "巡检 inspection", version: "v2", status: "active", statusTone: "good", meta: "平台 Skill · 更新于 07-06", bound: false, kind: "skill", skillKey: "inspection" },
  { id: "skill_user_logscan", name: "日志聚类 logscan", version: "v2", status: "active", statusTone: "good", meta: "我的 Skill · 更新于 07-06", bound: true, kind: "skill", skillKey: "logscan" },
  { id: "skill_user_traceparse", name: "链路解析 traceparse", version: "v1", status: "active", statusTone: "good", meta: "我的 Skill · 更新于 07-01", bound: false, kind: "skill", skillKey: "traceparse" },
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

/* --------------------------- 管理台（30.6） --------------------------- */
export const adminTables: Record<string, AdminTableData> = {
  templates: {
    title: "模板管理",
    primary: { label: "新建模板", icon: "plus", actionKey: "new-template" },
    cols: [{ label: "模板名" }, { label: "template_key" }, { label: "状态" }, { label: "active 版本" }, { label: "操作", width: "88px" }],
    rows: [
      { id: "tpl_sre_fast_recovery", cells: [
        { text: "感知快恢 Agent" }, { text: "sensai_fast_recovery", mono: true },
        { text: "active", kind: "badge", tone: "good" }, { text: "v3" },
        { text: "编辑", kind: "action", onClickKey: "edit-template" } ] },
      { id: "tpl_draft", cells: [
        { text: "网络诊断 Agent" }, { text: "net_diag", mono: true },
        { text: "draft", kind: "badge", tone: "neutral" }, { text: "—" },
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
  users: {
    title: "用户与白名单",
    primary: { label: "加入白名单", icon: "plus", actionKey: "add-user" },
    cols: [{ label: "user_id" }, { label: "展示名" }, { label: "role" }, { label: "白名单" }, { label: "最近登录" }],
    rows: [
      { id: "0026demo01", cells: [
        { text: "0026demo01", mono: true }, { text: "林一" }, { text: "user" },
        { text: "active", kind: "badge", tone: "good" }, { text: "10:00" } ] },
      { id: "admin", cells: [
        { text: "admin", mono: true }, { text: "李四" }, { text: "platform_admin" },
        { text: "active", kind: "badge", tone: "good" }, { text: "09:30" } ] },
    ],
  },
};

export const sandboxCfg: SandboxCfg[] = [
  { key: "max_user_containers_per_host", desc: "单机最大用户容器数", val: "26" },
  { key: "per_user_running_task_limit", desc: "每用户最多 running task", val: "2" },
  { key: "user_container_idle_ttl_minutes", desc: "idle 容器保留时间", val: "15" },
  { key: "capacity_full_policy", desc: "容量满策略", val: "strict_ttl" },
  { key: "container_cpu_limit", desc: "新建容器 CPU 限额", val: "0.5" },
  { key: "container_memory_limit_mib", desc: "新建容器内存限额", val: "2048" },
];

export const auditTimeline: AuditNode[] = [
  { event: "task.started", detail: "task_id=tsk_… · 支付延迟突增定界" },
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
    desc: "面向 SRE 巡检 / 定界 / 恢复闭环的平台模板，V1 唯一可用模板。",
    capabilities: ["巡检", "定界", "恢复"],
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
