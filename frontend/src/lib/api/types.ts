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
  time: string;
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
  tone: Tone;
  running?: boolean;
}
export interface ActivityGroup {
  label: string;
  items: ActivityNode[];
}

export interface Conversation {
  id: string;
  title: string;
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
  skills: Skill[];
  models: ModelOption[];
  currentModel: string;
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
export interface AuditNode {
  event: string;
  detail: string;
}

/* 初始化向导 */
export interface Template {
  template_version_id: string;
  name: string;
  desc: string;
  capabilities: string[];
  active_version: string;
}
export interface AppTreeNode {
  id: string;
  name: string;
  hasPermission?: boolean;
  children?: AppTreeNode[];
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
