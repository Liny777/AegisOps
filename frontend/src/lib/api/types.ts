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
  active_run_count: number;
  idle_seconds: number | null;
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

export interface ApiError {
  code: string;
  message: string;
  retryable?: boolean;
}
