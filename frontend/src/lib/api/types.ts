/** OpenOps 前端领域模型 —— 综合设计稿 demo 数据 + 后端 mock（37 路由）/ 30.x 口径。 */
import type { Tone } from "../../theme/tokens";

export type Role = "user" | "platform_admin";

export interface Me {
  user_id: string;
  display_name: string;
  role: Role;
  whitelisted: boolean;
  initials: string;
  meta: string; // 工号 / 部门
  has_instances: boolean;
  recent_instance_id?: string;
}

/** AgentTeam 实例（用户视角单 Agent）。 */
export interface AgentInstance {
  instance_id: string;
  name: string;
  template: string;
  workspace_id: string;
  workspace_label: string;
  scope_revision: string;
  status: "active" | "disabled" | "deleted";
  active_config_version: string;
  counts: string; // "2 Skill · 1 MCP"
  desc?: string; // 新原型：卡片描述行
  model?: string; // 新原型：模型提供商行
}

/** 编辑向导预填数据（GET /agent-teams/{id} 投影：实例列 + active 配置版本 overlay）。
 * 与卡片投影（projectInstance 的展示串）分开——预填要的是真实 id/overlay，不是显示文案。 */
export interface AgentTeamDetail {
  instance_id: string;
  name: string;
  template_version_id: string;
  workspace_id: string;
  overlay: Record<string, unknown>; // active_config_version.overlay_json（user_llm_config_id 等）
}

/* -------------------------- 对话工作台 -------------------------- */
export interface ChatMessage {
  id: string;
  role: "user" | "bot";
  text: string;
  showCopy?: boolean;
}

export interface Skill {
  skill_id: string;
  name: string; // /slash 名，mono
  desc: string;
}

export interface ModelOption {
  llm_config_id: string;
  label: string;
  note: string;
  current?: boolean;
  available: boolean;
  reason?: string;
}

export interface StatusChip {
  key: string;
  label: string;
  value: string;
  tone: Tone;
}

/* RCA 决策卡（对齐 frontend-v2 rcaCatalogSchema 精简版） */
export interface RcaTile {
  label: string;
  value: string;
}
export interface RcaStep {
  num: number;
  label: string;
  state: "done" | "active" | "waiting";
  /** 模型每步上报的一句小结（后端 step_summary 聚合）；缺失时前端 fallbackSummary 兜底。 */
  summary?: string;
}
export interface RcaFact {
  text: string;
}
export interface RcaSource {
  name: string;
  status: string;
  tone: Tone;
}
export interface RcaHypothesis {
  text: string;
  tag: string;
  tagTone: Tone;
  conf: number; // 0..1
}
export interface RcaAction {
  tier: string; // 立即 / 短期 / 长期
  text: string;
  confirm?: boolean;
  impact: string;
  status: string;
  statusTone: Tone;
}
export interface RcaCardData {
  title: string;
  phaseLabel: string;
  time?: string;
  revision?: number;
  /** 完成态唯一权威信号（后端派生）；前端不得用 steps 全 done 自行推断闭环。 */
  status?: "in_progress" | "concluded";
  tiles: RcaTile[];
  steps: RcaStep[];
  currentQ: string;
  why: string;
  facts: RcaFact[];
  unknowns: RcaFact[];
  sources: RcaSource[];
  hypotheses: RcaHypothesis[];
  actions: RcaAction[];
  conclusion: string;
}

/* HITL / ASK 卡 */
export interface HitlFact {
  label: string;
  value: string;
}
export interface HitlCardData {
  approval_request_id: string;
  title: string;
  tool: string;
  summary: string;
  facts: HitlFact[];
  countdown: string;
  status: "pending" | "approved" | "rejected";
  tone: Tone;
}

/* 活动时间线 */
export interface ActivityNode {
  id: string;
  title: string;
  tool?: string;
  detail: string;
  time: string;
  icon: string;
  agentKey?: string;  // D 块：事件归属 agent（main/子角色 key）——活动栏分组用
  tone: Tone;
  running?: boolean;
}
export interface ActivityGroup {
  label: string;
  items: ActivityNode[];
  roleKey?: string;   // E3：子 Agent 组的角色 key（main 组不带）——组头图标/徽标用
  status?: "running" | "done" | "failed";  // E3：从组内 subagent.* 事件推导的组状态
}

/**
 * 多 Agent 活动栏的统一事件模型。
 *
 * 这里只暴露后端已脱敏的 payload，刻意不提供 raw arguments/result/prompt 字段，避免
 * 技术视图在后续迭代中绕过后端脱敏边界。state、audit、AG-UI CUSTOM 与备用 SSE 都
 * 先归一化为此模型，再进入同一个 reducer。
 */
export type ActivityEventSource = "state" | "audit" | "live";

export interface ActivityEvent {
  eventId: string;
  eventType: string;
  runId?: string;
  taskId?: string;
  sequence?: number;
  occurredAt: string;
  severity: string;
  message: string;
  reasonCode?: string;
  action?: string;
  auditTraceId?: string;
  externalRequestId?: string;
  agentKey: string;
  agentLabel?: string;
  leaderTaskId?: string;
  childTaskId?: string;
  delegationId?: string;
  dispatchBatchId?: string;
  dispatchBatchNo?: number;
  displayLabel?: string;
  /** 后端 payload_redacted_json 的只读副本；不得放入原始调用参数或结果。 */
  redactedPayload: Readonly<Record<string, unknown>>;
  sources: ActivityEventSource[];
}

export type DelegationStatus =
  | "running"
  | "completed"
  | "failed"
  | "timeout"
  | "cancelled"
  | "unknown";

/** sre_agent_delegation 的前端领域投影；同一角色每次派发都有独立 delegationId。 */
export interface Delegation {
  delegationId: string;
  runId?: string;
  leaderTaskId?: string;
  childTaskId?: string;
  agentKey: string;
  agentLabel?: string;
  dispatchBatchId?: string;
  dispatchBatchNo?: number;
  status: DelegationStatus;
  taskSummary?: string;
  reportSummary?: string;
  hadFinalReport: boolean;
  deadline?: string;
  createdAt?: string;
  updatedAt?: string;
  /** true 表示来自账本；false 表示仅由旧事件补出的兼容轨迹。 */
  authoritative: boolean;
}

export interface DelegationTrack {
  delegation: Delegation;
  events: ActivityEvent[];
  /** 账本状态仍为 running；等待审批只是其展示子态。 */
  waitingApproval: boolean;
}

export type DispatchRoundStatus = "running" | "completed" | "completed_with_errors" | "unknown";

export interface DispatchRound {
  id: string;
  dispatchBatchId?: string;
  dispatchBatchNo?: number;
  leaderTaskId?: string;
  label: string;
  legacy: boolean;
  status: DispatchRoundStatus;
  startedAt?: string;
  latestAt?: string;
  tracks: DelegationTrack[];
  /** 无法无歧义归属到 delegation 的旧事件，不用时间差强行猜轨迹。 */
  unassignedEvents: ActivityEvent[];
  counts: {
    total: number;
    running: number;
    waitingApproval: number;
    completed: number;
    failed: number;
    timeout: number;
    cancelled: number;
  };
}

/** `/agent-runs/{id}/events` 的前端页模型。 */
export interface ActivityEventsPage {
  items: ActivityEvent[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface ActivityStoreState {
  events: ActivityEvent[];
  delegations: Delegation[];
  nextCursor: string | null;
  hasMore: boolean;
}

/** 活动 reducer 的最终 UI 投影；活动栏组件不再自行猜测轮次或终态。 */
export interface RailModel {
  events: ActivityEvent[];
  generalEvents: ActivityEvent[];
  rounds: DispatchRound[];
  nextCursor: string | null;
  hasMore: boolean;
}

export interface Conversation {
  id: string;           // real = agent_run_id（点击经 /agent-runs/:id 恢复）
  title: string;        // run_title；未起名显示「新对话」
  instance_id?: string;
  status?: "active" | "closed";
}

/** 对话工作台聚合状态（对应 GET /agent-runs/{id}/state 的前端投影）。 */
export interface WorkbenchState {
  chatTitle: string;
  agentName: string;
  summaryText: string;
  statusChips: StatusChip[];
  messages: ChatMessage[];
  rca?: RcaCardData;
  hitl?: HitlCardData;
  activity: ActivityGroup[];
  /** mock/E2E 演示用的 `/state` 等价形状；真实模式始终直接消费后端 state。 */
  activitySnapshot?: Record<string, unknown>;
  skills: Skill[];
  models: ModelOption[];
  currentModel: string;
  // real 模式运行态
  runId?: string;
  runStatus?: "active" | "closed";
  taskId?: string;
  taskStatus?: string; // running/completed/cancelled/failed
  lastEventSeq?: number;
}

/** 后端 openops.* 事件 envelope（30.4）。 */
export interface OpenOpsEvent {
  event_id: string;
  event_type: string;
  agent_run_id: string;
  task_id?: string | null;
  sequence: number;
  severity: string;
  message: string;
  reason_code?: string | null;
  audit_trace_id?: string | null;
  external_request_id?: string | null;
  payload_redacted_json?: Record<string, unknown>;
  occurred_at: string;
}

/* ---------------------------- 实例配置 ---------------------------- */
/** 分页信封（对齐后端 /assets/* 与 29.3 §2.2 口径：页码式 + total）。 */
export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}
/** 资产列表查询：过滤/搜索一律走服务端——分页后再在客户端过滤，每页数量必然错乱。 */
export interface AssetQuery {
  page?: number;
  pageSize?: number;
  sourceType?: "platform" | "user";
  q?: string;
  tag?: string; // 管理台用户列表：按领域标签精确过滤（与 q 为 AND）
}
export interface AssetRow {
  id: string;
  name: string;
  version: string;
  status: string;
  statusTone: Tone;
  meta: string;
  bound: boolean;
  kind?: "skill" | "mcp"; // 库行：资产类型（绑定/删除用）
  sourceType?: "platform" | "user"; // 系统自带(platform) / 用户自定义(user)——插件页只读「已装配」判定、分组
  versionId?: string; // 库行：最新版本 id（绑定用）
  assetId?: string; // 已绑行：底层资产 id（与库行对照「已绑定」态）
  skillKey?: string; // skill 行：运行时白名单键（模板编辑器勾选用；display_name 只是展示名）
  bindingStatus?: string; // 已绑行：绑定行状态（active/muted）——个人 skill/MCP 的 muted=已从本 Agent 解绑
  description?: string; // 真描述（SKILL.md frontmatter / registry 服务描述）——插件页「说明」用
  category?: string;
}
export interface ConfigVersionRow {
  version_no: string;
  config_version_id: string;
  status: string;
  change_reason: string;
  created_by: string;
  creation_date: string;
}

/* ----------------------------- 管理台 ----------------------------- */
export interface AdminCell {
  text: string;
  kind?: "text" | "badge" | "action";
  tone?: Tone;
  mono?: boolean;
  onClickKey?: string; // 前端解析成回调
}
export interface AdminRow {
  id: string;
  cells: AdminCell[];
}
export interface AdminTableData {
  title: string;
  primary?: { label: string; icon: string; actionKey: string };
  tabs?: { key: string; label: string }[];
  cols: { label: string; width?: string }[];
  rows: AdminRow[];
  // 服务端分页（目前只有 Skill 基线用；其余表不带 → 不渲染分页器）
  total?: number;
  page?: number;
  pageSize?: number;
}
export interface SandboxCfg {
  key: string;
  desc: string;
  val: string;
}
export interface SandboxContainer {
  user_id: string;
  runtime_status: string; // active / idle
  image_version: string;
  active_run_count: number;        // 打开中的会话/run 数（session 口径，非并发任务量，可能因 run 未正常关闭虚高）
  running_task_count: number;      // 当前运行的主任务数（对齐 per_user_running_task_limit 限额）
  running_subtask_count?: number;  // 子 Agent 在飞数（fan-out 观测，不计入限额）
  queued_task_count?: number;      // 名额满后在交互队列等待的任务数（持续>0=对话侧拥堵）
  idle_seconds: number | null;
}
export interface SandboxDestroyResult {
  destroyed: boolean;          // false=容器本就不在册（已被回收），非报错
  target_user_id: string;
  cancelled_task_ids: string[]; // 连带中断的运行中主任务（不掐任务容器会被自愈重建）
}
export interface AuditNode {
  event: string;
  detail: string;
  trace?: string;  // audit_trace_id（B7·三：审计页按 Trace 串联过滤）
}

/* 初始化向导 */
export interface Template {
  template_version_id: string;
  name: string;
  desc: string;
  capabilities: string[];
  active_version: string;
}

/** 模型模板（38 号：管理员编排的 主/子 Agent 模型组合）。
 * 用户侧 GET /models/templates 经模板级 ACL 过滤（38.1：scope=all ∪ 白名单授权），real 模式只回 active 行。 */
export interface ModelTemplateOption {
  model_template_id: string;
  display_name: string;
  description?: string;
  main_model: { model_id: string; display_name: string };
  sub_model: { model_id: string; display_name: string };
  access_scope: "all" | "restricted";
  is_default: boolean;
  status: "active" | "disabled";
}

/** 管理台模型模板行（列表/编辑弹窗共用）：比用户侧多编辑所需的槽位资产 UUID 与授权信息（38.1）。 */
export interface AdminModelTemplate {
  model_template_id: string;
  display_name: string;
  description: string;
  main_model_asset_id: string;
  main_model_id: string;   // 槽位资产的 model_id（展示/兜底）
  main_model_name: string; // 槽位资产展示名（资产被删时为空串）
  sub_model_asset_id: string;
  sub_model_id: string;
  sub_model_name: string;
  access_scope: "all" | "restricted";
  grant_count: number;     // active 授权人数（「授权范围」徽标：限 N 人）
  is_default: boolean;
  status: "active" | "disabled";
}

/** 模型模板编辑弹窗的槽位候选（来自 GET /admin/model-assets）。 */
export interface AdminModelAssetOption {
  model_asset_id: string;
  model_id: string;
  display_name: string;
  status: string;
}
export interface ScopeApp {
  app_id: string;
  name: string;
  type: string;
  tenant_id?: string;
}
export interface Workspace {
  workspace_id: string;
  name: string;
  scope_revision: string;
  sync_status: "creating" | "syncing" | "ready" | "failed";
  updated: string;
}
/** 系统范围详情（编辑向导预填用）：含 name + 已选应用 app_ids。 */
export interface WorkspaceDetail {
  workspace_id: string;
  name: string;
  app_ids: string[];
  scope_revision: string;
  sync_status: Workspace["sync_status"];
}

/** 看护空间统计（oModel wesee/statistics 四数；Agent 初始化「确认能力清单」页展示）。 */
export interface OmodelStatistics {
  node_count: number;
  relation_count: number;
  node_type_count: number;
  relation_type_count: number;
}

export interface ApiError {
  code: string;
  message: string;
  retryable?: boolean;
}
