/**
 * OpenOps 前端 API facade —— VITE_OPENOPS_API_MODE 切 real（默认，真打后端）/ mock（纯 UI 演示）。
 * real：后端 FastAPI（PG+SSE，vite 代理 /api → 18082）；投影见 projection.ts。
 */
import type {
  Me,
  AgentInstance,
  AgentTeamDetail,
  WorkbenchState,
  Conversation,
  AdminTableData,
  SandboxCfg,
  SandboxContainer,
  AuditNode,
  Template,
  Workspace,
  ScopeApp,
  Skill,
  AssetRow,
  ConfigVersionRow,
  ModelOption,
  ActivityNode,
  ActivityEventsPage,
  TranscriptMessage,
} from "./types";
import * as M from "./mockData";
import { apiFetch, crid, demoIdentity, setDemoUser } from "./client";
import { auditToNode, projectInstance } from "./projection";
import { normalizeActivityPage } from "../activity";
import {
  KeyedSingleFlightCache,
  SingleFlightCache,
  waitWithSignal,
} from "./singleFlight";
import type { RequestOptions } from "./singleFlight";

export type { RequestOptions } from "./singleFlight";
export { isAbortError } from "./singleFlight";

export const API_MODE: "mock" | "real" =
  (import.meta.env.VITE_OPENOPS_API_MODE as "mock" | "real" | undefined) ?? "real";

/** 「测试连接」结果（用户自带 / 平台模型共用）：ok=true 才允许保存。 */
export interface TestConnResult {
  ok: boolean;
  supports_tool_calling: boolean;
  reason: string | null;
}

const delay = <T,>(v: T, ms = 120): Promise<T> => new Promise((r) => setTimeout(() => r(v), ms));

// 后端时间字段是 tz-aware UTC ISO（…+00:00）——统一转本地时区显示（曾用 slice 裸切 UTC 差 8h）。
const fmtLocal = (v: unknown): string => {
  const s = String(v ?? "");
  if (!s) return "";
  const d = new Date(s);
  return isNaN(d.getTime()) ? s.replace("T", " ").slice(0, 16)
    : d.toLocaleString([], { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
};
const fmtLocalTime = (v: unknown): string => {
  const s = String(v ?? "");
  if (!s || s === "—") return "";
  const d = new Date(s);
  return isNaN(d.getTime()) ? s.slice(11, 16) : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
};

/* ------------------------------ 接口面 ------------------------------ */
export interface OpenOpsApi {
  getMe(): Promise<Me>;
  listAgents(): Promise<AgentInstance[]>;
  /** omodel 控制台页面前缀（设置页 iframe；空串=未配置/mock，显示空态）。 */
  getOmodelPageBase(): Promise<string>;
  /** 登出（B9）：清后端 IAM 会话缓存，返回 IAM signout/login 地址（未配 IAM 时均为 null）。 */
  logout(): Promise<{ signout_url: string | null; login_url: string | null }>;
  listConversations(options?: RequestOptions): Promise<Conversation[]>;
  // 运行态（real：ensureRun → state/task/approval/SSE）
  ensureRun(instanceId: string, options?: RequestOptions): Promise<string>; // → run_id
  createRun(instanceId: string): Promise<string>; // always create a new run
  renameRun(runId: string, title: string): Promise<void>;
  deleteRun(runId: string): Promise<void>;
  getRunState(runId: string, options?: RequestOptions): Promise<Record<string, unknown>>;
  /** 历史对话 transcript（B1：重进会话前端自渲染，绕开 CopilotKit connect——它走 sidecar 内存拿不到历史）。 */
  getMessages(runId: string, options?: RequestOptions): Promise<TranscriptMessage[]>;
  startTask(runId: string, text: string): Promise<{ task_id: string }>;
  cancelTask(taskId: string): Promise<void>;
  closeRun(runId: string): Promise<void>;
  decideApproval(id: string, decision: "approved" | "rejected"): Promise<void>;
  selectModel(runId: string, model: string): Promise<void>;
  /** 脱敏活动事件游标页；before 为空时取最新一页。 */
  getActivityEvents(
    runId: string,
    options?: { before?: string | null; limit?: number; signal?: AbortSignal },
  ): Promise<ActivityEventsPage>;
  /** 旧 ActivityRail 兼容接口；新实现统一使用 getActivityEvents + activity reducer。 */
  getAuditNodes(runId: string, options?: RequestOptions): Promise<ActivityNode[]>;
  // 实例管理（新原型清单页）
  toggleInstance(instanceId: string, enabled: boolean): Promise<void>;
  deleteInstance(instanceId: string): Promise<void>;
  // settings
  getBoundSkills(instanceId: string): Promise<AssetRow[]>;
  /** composer「/」列表：与后端执行门禁同源的可执行 Skill 装配集（skill_key 即调用名）。 */
  getAvailableSkills(instanceId: string, options?: RequestOptions): Promise<Skill[]>;
  getSkillLibrary(): Promise<AssetRow[]>;
  getMcpLibrary(): Promise<AssetRow[]>;
  getConfigVersions(instanceId: string): Promise<ConfigVersionRow[]>;
  getModelConfigs(): Promise<ModelOption[]>;
  // 用户自定义 LLM（探测真化闭合）：录 Secret → 建 llm-config（服务端探测+egress）
  createSecret(secretName: string, secretValue: string): Promise<{ secret_ref_id: string; fingerprint?: string }>;
  createLlmConfig(input: { display_name: string; base_url: string; model_name: string; secret_ref_id: string; context_window_tokens?: number }): Promise<{ llm_config_id: string }>;
  /** 用户自带模型「测试连接」（存前探测，不落库）：egress + tool-calling 探测。 */
  testLlmConnection(input: { base_url: string; model_name: string; api_key: string }): Promise<TestConnResult>;
  // settings 写闭环（B6：上传/注册/删除/绑定/解绑/main 追加/对账）
  /** 上传 Skill ZIP（29.3 §2.1 multipart）：file 必填、category 必填、tags 可选。 */
  uploadSkill(file: File, category: string, tags?: string[]): Promise<{ skill_key: string; action: string }>;
  registerMcp(name: string, endpoint: string): Promise<void>;
  deleteAsset(kind: "skill" | "mcp", id: string): Promise<void>;
  bindAsset(instanceId: string, row: AssetRow): Promise<void>;
  unbindAsset(bindingId: string): Promise<void>;
  getMainAppend(instanceId: string): Promise<string>;
  saveMainAppend(instanceId: string, text: string): Promise<void>;
  reconcileAssets(): Promise<Record<string, unknown>>;
  // admin
  getAdminTable(key: string): Promise<AdminTableData>;
  getSandboxCfg(): Promise<SandboxCfg[]>;
  saveSandboxCfg(updates: Record<string, unknown>, reason: string): Promise<void>;
  getSandboxContainers(): Promise<SandboxContainer[]>;
  destroySandboxContainer(userId: string, reason: string): Promise<void>;
  getAuditTimeline(): Promise<AuditNode[]>;
  // admin B7b：模板版本写闭环（草稿/发布）
  getAdminTemplateDetail(templateId: string): Promise<{
    template: Record<string, unknown>;
    active_version: Record<string, unknown> | null;
    draft_version: Record<string, unknown> | null;
  }>;
  saveTemplateDraft(templateId: string, content: Record<string, unknown>): Promise<Record<string, unknown>>;
  publishTemplateVersion(versionId: string): Promise<void>;
  disableTemplateVersion(versionId: string): Promise<void>;
  // admin B7·三：白名单管理动作 + 审计 Trace 串联
  adminAddWhitelist(userId: string, displayName: string): Promise<void>;
  adminRevokeWhitelist(userId: string): Promise<void>;
  adminSetRole(userId: string, role: "user" | "platform_admin"): Promise<void>;
  getAuditTrace(traceId: string): Promise<AuditNode[]>;
  // admin B7a：模板 drill（资产治理→Tool 标注）+ 标注保存 + 模型资产授权
  getAdminTemplateAssets(): Promise<AdminTableData>;
  getAdminMcpTools(mcpName: string | null): Promise<AdminTableData & { raw: Record<string, unknown>[] }>;
  adminSaveAnnotation(toolCatalogId: string, payload: Record<string, unknown>): Promise<void>;
  adminListUsers(): Promise<{ user_id: string; display_name: string }[]>;
  adminGetModelGrants(modelAssetId: string): Promise<{ access_scope: string; user_ids: string[] }>;
  adminSaveModelGrants(modelAssetId: string, accessScope: string, userIds: string[]): Promise<void>;
  adminRegisterModel(fields: { display_name: string; model_id: string; base_url?: string; secret_env_var?: string; access_scope: string; context_window_tokens?: number }): Promise<void>;
  /** 平台模型「测试连接」：Key 走服务器环境变量（secret_env_var 名），客户端不持 Key。 */
  testModelAssetConnection(input: { base_url: string; model_id: string; secret_env_var: string }): Promise<TestConnResult>;
  // init
  getTemplates(): Promise<Template[]>;
  getWorkspaces(): Promise<Workspace[]>;
  getScopeApps(): Promise<ScopeApp[]>;
  createWorkspace(name: string, apps: { app_id: string; name?: string; tenant_id?: string }[]): Promise<{ workspace_id: string }>;
  createAgentTeam(input: { template_version_id: string; name: string; workspace_id: string; initial_overlay_json?: Record<string, unknown> }): Promise<{ instance_id: string }>;
  /** 编辑向导预填：实例真实字段 + active overlay（区别于 listAgents 的卡片展示投影）。 */
  getAgentTeam(instanceId: string): Promise<AgentTeamDetail>;
  /** 编辑保存（POST :update）：改名 / 换 workspace / 换模型；user_llm_config_id=null 回平台默认。 */
  updateAgentTeam(instanceId: string, input: { name: string; workspace_id: string; user_llm_config_id: string | null }): Promise<void>;
  // demo 身份
  switchRole(admin: boolean): void;
  demoState(): WorkbenchState; // mock 兜底静态（composer skills/models 等）
}

/* -------------------------------- real -------------------------------- */
const runByInstance = new Map<string, string>();
const ensureRunCreationByInstance = new Map<string, Promise<string>>();

/** 已确认 Run closed 时清掉 ensureRun 的实例命中，下一次 generic chat 必须重新解析/创建。 */
export function forgetEnsuredRun(instanceId: string, runId?: string): void {
  const current = runByInstance.get(instanceId);
  if (current && (!runId || current === runId)) runByInstance.delete(instanceId);
}

const conversationListeners = new Set<() => void>();

async function loadConversationHistory(signal: AbortSignal): Promise<Conversation[]> {
  if (API_MODE === "mock") {
    return waitWithSignal(
      delay(M.mockConversations.map((conversation) => ({ ...conversation }))),
      signal,
    );
  }
  const runs = await apiFetch<Record<string, unknown>[]>("/openops/v1/agent-runs", { signal });
  return runs.map((r) => ({
    id: String(r.agent_run_id),
    title: String(r.run_title ?? "") || "新对话",
    instance_id: String(r.agent_team_instance_id),
    status: (r.run_status === "closed" ? "closed" : "active") as "active" | "closed",
  }));
}

const conversationCache = new SingleFlightCache(loadConversationHistory);

/** App 级历史列表失效通知；CRUD 成功后自动调用，消费者无需跟随 pathname 重拉。 */
export function invalidateConversationHistory(): void {
  conversationCache.invalidate();
  for (const listener of conversationListeners) {
    try {
      listener();
    } catch (error) {
      console.warn("[OpenOps] 历史会话失效订阅执行失败：", error);
    }
  }
}

export function subscribeConversationHistory(listener: () => void): () => void {
  conversationListeners.add(listener);
  return () => conversationListeners.delete(listener);
}

const availableSkillsCache = new KeyedSingleFlightCache<string, Skill[]>();

async function loadAvailableSkills(instanceId: string, signal: AbortSignal): Promise<Skill[]> {
  if (API_MODE === "mock") {
    return waitWithSignal(
      delay(M.mockWorkbenchState().skills.map((skill) => ({ ...skill }))),
      signal,
    );
  }
  const rows = await apiFetch<Record<string, unknown>[]>(
    `/openops/v1/agent-teams/${instanceId}/available-skills`,
    { signal },
  );
  return rows.map((r) => ({
    skill_id: String(r.skill_key),
    name: "/" + String(r.skill_key),
    desc: `${r.display_name ?? r.skill_key} · ${r.source_type === "platform" ? "平台" : "我的"}`,
  }));
}

function getAvailableSkillsCached(instanceId: string, options: RequestOptions = {}): Promise<Skill[]> {
  return availableSkillsCache.get(
    instanceId,
    (signal) => loadAvailableSkills(instanceId, signal),
    options,
  );
}

/** 资产绑定变化后失效指定 Agent；无法确定归属时清空全部实例缓存。 */
export function invalidateAvailableSkills(instanceId?: string): void {
  availableSkillsCache.invalidate(instanceId);
}

/** audit_event 行 → 审计页节点（recent 与 by-trace 共用）。 */
const auditNode = (e: Record<string, unknown>) => ({
  event: String(e.event_type),
  detail: [e.task_id, e.reason_code, e.decision].filter(Boolean).join(" · ") || String(e.action ?? ""),
  trace: e.audit_trace_id ? String(e.audit_trace_id) : undefined,
});

const realApi: OpenOpsApi = {
  async getMe() {
    const d = await apiFetch<Record<string, unknown>>("/openops/v1/me");
    return {
      user_id: String(d.user_id),
      display_name: String(d.display_name ?? d.user_id),
      role: (d.role as Me["role"]) ?? "user",
      whitelisted: Boolean(d.whitelisted),
      initials: String(d.display_name ?? "·").slice(0, 1),
      meta: `${d.user_id} · ${d.role === "platform_admin" ? "平台管理员" : "普通用户"}`,
      has_instances: Boolean(d.has_instances),
      recent_instance_id: (d.recent_instance_id as string | null) ?? undefined,
    };
  },
  async listAgents() {
    // 并取实例 + oModel workspace 清单：卡片「系统范围」显示工作空间名称而非 id；
    // 名字拉不到（oModel 瞬断/会话过期）不阻塞列表，回退显示 id
    const [rows, wss] = await Promise.all([
      apiFetch<Record<string, unknown>[]>("/openops/v1/agent-teams"),
      realApi.getWorkspaces().catch(() => []),
    ]);
    const wsNames = new Map(wss.map((w) => [w.workspace_id, w.name]));
    return rows.map((r) => projectInstance(r, wsNames));
  },
  async getOmodelPageBase() {
    const d = await apiFetch<{ page_base?: string }>("/openops/v1/omodel/console-page");
    return String(d.page_base ?? "");
  },
  async logout() {
    const d = await apiFetch<{ signout_url?: string | null; login_url?: string | null }>(
      "/openops/v1/auth/logout", { method: "POST" });
    return { signout_url: d.signout_url ?? null, login_url: d.login_url ?? null };
  },
  listConversations: (options) => conversationCache.get(options),

  async ensureRun(instanceId, options) {
    const cached = runByInstance.get(instanceId);
    if (cached) return cached;
    // 查询阶段跟随当前会话 generation 取消；进入创建阶段后不再把路由取消传播给写请求。
    const runs = await conversationCache.get(options);
    const active = runs.find(
      (run) => run.instance_id === instanceId && run.status === "active",
    );
    if (active) {
      const id = active.id;
      runByInstance.set(instanceId, id);
      return id;
    }
    if (options?.signal?.aborted) {
      throw options.signal.reason instanceof Error
        ? options.signal.reason
        : new DOMException("The operation was aborted", "AbortError");
    }
    // 两个 ensure 可能共享同一次列表查询；第一个创建已完成时，第二个不得再开一条 Run。
    const createdByPeer = runByInstance.get(instanceId);
    if (createdByPeer) return createdByPeer;

    let creation = ensureRunCreationByInstance.get(instanceId);
    if (!creation) {
      creation = apiFetch<{ run: Record<string, unknown> }>("/openops/v1/agent-runs", {
        method: "POST",
        body: { client_request_id: crid(), agent_team_instance_id: instanceId },
      }).then((d) => {
        const id = String(d.run.agent_run_id);
        runByInstance.set(instanceId, id);
        invalidateConversationHistory();
        return id;
      }).finally(() => {
        if (ensureRunCreationByInstance.get(instanceId) === creation) {
          ensureRunCreationByInstance.delete(instanceId);
        }
      });
      ensureRunCreationByInstance.set(instanceId, creation);
    }
    return creation;
  },
  async createRun(instanceId) {
    const d = await apiFetch<{ run: Record<string, unknown> }>("/openops/v1/agent-runs", {
      method: "POST",
      body: { client_request_id: crid(), agent_team_instance_id: instanceId },
    });
    const id = String(d.run.agent_run_id);
    runByInstance.set(instanceId, id);
    invalidateConversationHistory();
    return id;
  },
  async renameRun(runId, title) {
    await apiFetch(`/openops/v1/agent-runs/${runId}:rename`, {
      method: "POST",
      body: { client_request_id: crid(), title },
    });
    invalidateConversationHistory();
  },
  async deleteRun(runId) {
    await apiFetch(`/openops/v1/agent-runs/${runId}:delete`, { method: "POST", body: {} });
    runByInstance.forEach((v, k) => { if (v === runId) runByInstance.delete(k); });
    invalidateConversationHistory();
  },
  getRunState: (runId, options) => apiFetch(`/openops/v1/agent-runs/${runId}/state`, {
    signal: options?.signal,
  }),
  getMessages: (runId, options) => apiFetch(`/openops/v1/agent-runs/${runId}/messages`, {
    signal: options?.signal,
  }),
  async startTask(runId, text) {
    return apiFetch(`/openops/v1/agent-runs/${runId}/tasks`, {
      method: "POST",
      body: { client_request_id: crid(), input_text: text },
    });
  },
  async cancelTask(taskId) {
    await apiFetch(`/openops/v1/tasks/${taskId}:cancel`, { method: "POST", body: {} });
  },
  async closeRun(runId) {
    await apiFetch(`/openops/v1/agent-runs/${runId}:close`, { method: "POST", body: {} });
    runByInstance.forEach((v, k) => { if (v === runId) runByInstance.delete(k); });
    invalidateConversationHistory();
  },
  async decideApproval(id, decision) {
    await apiFetch(`/openops/v1/approvals/${id}:decide`, {
      method: "POST",
      body: { client_request_id: crid(), decision },
    });
  },
  async selectModel(runId, model) {
    // 选择器 id：平台模型是 `platform:<model_id>`，用户自定义 LLM 是裸 llm_config_id(UUID)。
    // 后端 select-model 要的是裸 model_id（走 model_source）或 llm_config_id——必须按前缀分流，
    // 否则平台模型带 `platform:` 前缀发过去，is_authorized 查不到 → 403（MODEL_NOT_AUTHORIZED）。
    const body = model.startsWith("platform:")
      ? { client_request_id: crid(), model_source: model.slice("platform:".length) }
      : { client_request_id: crid(), llm_config_id: model };
    await apiFetch(`/openops/v1/agent-runs/${runId}:select-model`, { method: "POST", body });
  },
  async getActivityEvents(runId, options) {
    const params = new URLSearchParams();
    if (options?.before) params.set("before", options.before);
    if (options?.limit !== undefined) {
      params.set("limit", String(Math.max(1, Math.min(200, Math.trunc(options.limit)))));
    }
    const query = params.size ? `?${params.toString()}` : "";
    const page = await apiFetch<Record<string, unknown>>(
      `/openops/v1/agent-runs/${runId}/events${query}`,
      { signal: options?.signal },
    );
    return normalizeActivityPage(page);
  },
  async getAuditNodes(runId, options) {
    const rows = await apiFetch<Record<string, unknown>[]>(`/openops/v1/audit/runs/${runId}`, {
      signal: options?.signal,
    });
    return rows.map(auditToNode);
  },

  async toggleInstance(instanceId, enabled) {
    await apiFetch(`/openops/v1/agent-teams/${instanceId}:${enabled ? "enable" : "disable"}`, { method: "POST", body: {} });
  },
  async deleteInstance(instanceId) {
    await apiFetch(`/openops/v1/agent-teams/${instanceId}`, { method: "DELETE" });
    invalidateAvailableSkills(instanceId);
  },

  async getBoundSkills(instanceId) {
    const rows = await apiFetch<Record<string, unknown>[]>(`/openops/v1/agent-teams/${instanceId}/asset-bindings`);
    return rows.map((r) => ({
      id: String(r.binding_id),
      name: String(r.display_name ?? (r.asset_type === "mcp" ? "HTTP MCP" : "Skill")),
      version: `v${r.version_no ?? 1}`,
      status: String(r.asset_status ?? r.status ?? "active"),
      statusTone: r.asset_status === "deleted" || r.status === "deleted" ? "danger" as const : "good" as const,
      meta: `${r.asset_type === "mcp" ? "HTTP MCP" : "Skill"} · ${r.source_type === "platform" ? "平台" : "我的"} · main`,
      bound: true,
      kind: (r.asset_type === "mcp" ? "mcp" : "skill") as "skill" | "mcp",
      sourceType: r.source_type === "platform" ? "platform" as const : "user" as const,
      assetId: String(r.skill_id ?? r.mcp_id ?? ""),
    }));
  },
  getAvailableSkills: (instanceId, options) => getAvailableSkillsCached(instanceId, options),
  async getSkillLibrary() {
    const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/assets/skills");
    return rows.map((r) => ({
      id: String(r.skill_id),
      name: String(r.display_name),
      // 版本优先 SkillHub §2.2 semver（latest_version），缺失回退本地 v{version_no}（用户上传前/mock）
      version: r.latest_version ? String(r.latest_version) : `v${r.version_no ?? 1}`,
      status: String(r.status),
      statusTone: r.status === "active" ? "good" as const : "warning" as const,
      meta: r.source_type === "platform" ? "平台 Skill" : "我的 Skill",
      bound: false,
      kind: "skill" as const,
      sourceType: r.source_type === "platform" ? "platform" as const : "user" as const,
      versionId: r.skill_version_id ? String(r.skill_version_id) : undefined,
      skillKey: r.skill_key ? String(r.skill_key) : undefined, // 模板编辑器勾选用（运行时白名单键）
    }));
  },
  async getMcpLibrary() {
    const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/assets/mcps");
    return rows.map((r) => ({
      id: String(r.mcp_id),
      name: String(r.display_name),
      version: `v${r.version_no ?? 1}`,
      status: String(r.status),
      statusTone: r.status === "active" ? "good" as const : "warning" as const,
      meta: r.source_type === "platform" ? "平台 MCP" : "我的 MCP",
      bound: false,
      kind: "mcp" as const,
      versionId: r.mcp_version_id ? String(r.mcp_version_id) : undefined,
    }));
  },
  // ---- settings 写闭环（B6） ----
  async uploadSkill(file, category, tags) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("category", category);
    if (tags && tags.length) fd.append("tags", JSON.stringify(tags));
    const d = await apiFetch<Record<string, unknown>>("/openops/v1/assets/skills:upload", { method: "POST", body: fd });
    return { skill_key: String(d.skill_key ?? ""), action: String(d.action ?? "created") };
  },
  async registerMcp(name, endpoint) {
    await apiFetch("/openops/v1/assets/mcps", {
      method: "POST",
      body: { client_request_id: crid(), display_name: name, transport: "http", endpoint, manifest_json: {} },
    });
  },
  async deleteAsset(kind, id) {
    await apiFetch(`/openops/v1/assets/${kind === "mcp" ? "mcps" : "skills"}/${id}`, { method: "DELETE" });
    invalidateAvailableSkills();
  },
  async bindAsset(instanceId, row) {
    await apiFetch(`/openops/v1/agent-teams/${instanceId}/asset-bindings`, {
      method: "POST",
      body: {
        client_request_id: crid(),
        asset_type: row.kind ?? "skill",
        skill_id: row.kind === "skill" ? row.id : null,
        skill_version_id: row.kind === "skill" ? row.versionId ?? null : null,
        mcp_id: row.kind === "mcp" ? row.id : null,
        mcp_version_id: row.kind === "mcp" ? row.versionId ?? null : null,
      },
    });
    invalidateAvailableSkills(instanceId);
  },
  async unbindAsset(bindingId) {
    await apiFetch(`/openops/v1/asset-bindings/${bindingId}`, { method: "DELETE" });
    // 删除接口只有 bindingId，无法可靠还原 instanceId，保守失效全部 Agent。
    invalidateAvailableSkills();
  },
  async getMainAppend(instanceId) {
    const d = await apiFetch<{ active_config_version?: Record<string, unknown> | null }>(
      `/openops/v1/agent-teams/${instanceId}`,
    );
    const overlay = (d.active_config_version?.overlay_json ?? {}) as Record<string, unknown>;
    return String(overlay.main_role_append ?? "");
  },
  async saveMainAppend(instanceId, text) {
    await apiFetch(`/openops/v1/agent-teams/${instanceId}/config-versions`, {
      method: "POST",
      body: { client_request_id: crid(), overlay_json: { main_role_append: text }, change_reason: "main role append" },
    });
  },
  async reconcileAssets() {
    return apiFetch<Record<string, unknown>>("/openops/v1/assets:reconcile", { method: "POST", body: {} });
  },
  async getConfigVersions(instanceId) {
    const rows = await apiFetch<Record<string, unknown>[]>(`/openops/v1/agent-teams/${instanceId}/config-versions`);
    return rows.map((r) => ({
      config_version_id: String(r.config_version_id),
      version_no: `v${r.version_no ?? 1}`,
      status: String(r.status),
      change_reason: String(r.change_reason ?? ""),
      created_by: String(r.created_by ?? ""),
      creation_date: fmtLocal(r.creation_date),
    }));
  },
  async getModelConfigs() {
    const platformRows = await apiFetch<Record<string, unknown>[]>("/openops/v1/models/platform");
    const platform: ModelOption[] = platformRows.map((r) => ({
      llm_config_id: `platform:${String(r.model_id)}`,
      label: String(r.display_name ?? r.name ?? r.model_id),  // 展示名（如 GLM-5.1）优先，回退 model_id
      note: `${r.protocol ?? "OpenAI 兼容"} · ${r.probe ?? "待探测"}`,
      available: r.status === "active",
      current: r.is_default === true,  // 后端标记的真实运行默认（同 model_gateway 口径），非列表首位
      reason: r.status === "active" ? undefined : String(r.probe ?? "不可用"),
    }));
    const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/llm-configs");
    const mine: ModelOption[] = rows.map((r) => ({
      llm_config_id: String(r.llm_config_id),
      label: String(r.display_name),
      note: `${r.model_name} · ${r.supports_tool_calling ? "tool calling" : "无 tool calling"}`,
      available: r.status === "active" && Boolean(r.supports_tool_calling),
      current: false,
    }));
    return [...platform, ...mine];
  },
  async createSecret(secretName, secretValue) {
    const d = await apiFetch<Record<string, unknown>>("/openops/v1/secrets", {
      method: "POST",
      body: { client_request_id: crid(), secret_name: secretName, secret_type: "api_key", provider: "openai_compatible", secret_value: secretValue },
    });
    return { secret_ref_id: String(d.secret_ref_id), fingerprint: d.fingerprint ? String(d.fingerprint) : undefined };
  },
  async createLlmConfig(input) {
    const d = await apiFetch<Record<string, unknown>>("/openops/v1/llm-configs", {
      method: "POST",
      body: {
        client_request_id: crid(), display_name: input.display_name, provider: "openai_compatible",
        base_url: input.base_url, model_name: input.model_name, secret_ref_id: input.secret_ref_id,
        context_window_tokens: input.context_window_tokens ?? 128000,
      },
    });
    return { llm_config_id: String(d.llm_config_id) };
  },
  async testLlmConnection(input) {
    const d = await apiFetch<Record<string, unknown>>("/openops/v1/llm-configs:test-connection", {
      method: "POST",
      body: { base_url: input.base_url, model_name: input.model_name, api_key: input.api_key },
    });
    return { ok: Boolean(d.ok), supports_tool_calling: Boolean(d.supports_tool_calling), reason: d.reason ? String(d.reason) : null };
  },
  async testModelAssetConnection(input) {
    const d = await apiFetch<Record<string, unknown>>("/openops/v1/admin/model-assets:test-connection", {
      method: "POST",
      body: { base_url: input.base_url, model_id: input.model_id, secret_env_var: input.secret_env_var },
    });
    return { ok: Boolean(d.ok), supports_tool_calling: Boolean(d.supports_tool_calling), reason: d.reason ? String(d.reason) : null };
  },

  async getAdminTable(key) {
    if (key === "templates") {
      const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/admin/templates");
      return {
        title: "模板管理",
        cols: [{ label: "模板名" }, { label: "template_key" }, { label: "状态" }, { label: "治理", width: "88px" }, { label: "编辑", width: "64px" }],
        rows: rows.map((r) => ({
          id: String(r.template_id),
          cells: [
            { text: String(r.display_name) },
            { text: String(r.template_key), mono: true },
            { text: String(r.status), kind: "badge" as const, tone: r.status === "active" ? "good" as const : "neutral" as const },
            { text: "资产治理", kind: "action" as const, onClickKey: "open-template" },
            { text: "编辑", kind: "action" as const, onClickKey: "edit-template" },
          ],
        })),
      };
    }
    if (key === "users") {
      const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/admin/users");
      return {
        title: "用户与白名单",
        primary: { label: "加入白名单", icon: "plus", actionKey: "add-user" },
        cols: [{ label: "user_id" }, { label: "展示名" }, { label: "role" }, { label: "白名单" }, { label: "最近登录" }, { label: "操作", width: "72px" }, { label: "角色", width: "110px" }],
        rows: rows.map((r) => ({
          id: String(r.user_id),
          cells: [
            { text: String(r.user_id), mono: true },
            { text: String(r.display_name ?? "") },
            { text: String(r.role) },
            { text: String(r.whitelist_status), kind: "badge" as const, tone: r.whitelist_status === "active" ? "good" as const : "neutral" as const },
            { text: fmtLocalTime(r.last_login_at) || "—" },
            r.whitelist_status === "active"
              ? { text: "移出", kind: "action" as const, onClickKey: "wl-revoke" }
              : { text: "加入", kind: "action" as const, onClickKey: "wl-add" },
            // 角色升/降（set-role 补链）：改自己会被后端 400 拦（防锁死），错误显示在动作横幅
            r.role === "platform_admin"
              ? { text: "撤销管理员", kind: "action" as const, onClickKey: "role-user" }
              : { text: "设为管理员", kind: "action" as const, onClickKey: "role-admin" },
          ],
        })),
      };
    }
    if (key === "skills") {
      // Skill 基线（只读）：系统自带（platform）skill 清单 + §2.2 semver 版本。复用 /assets/skills（含 latest_version）。
      const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/assets/skills");
      const platform = rows.filter((r) => r.source_type === "platform");
      return {
        title: "Skill 基线",
        cols: [{ label: "名称" }, { label: "skill_key" }, { label: "版本" }, { label: "分类" }, { label: "状态", width: "88px" }],
        rows: platform.map((r) => ({
          id: String(r.skill_id),
          cells: [
            { text: String(r.display_name) },
            { text: String(r.skill_key ?? ""), mono: true },
            { text: r.latest_version ? String(r.latest_version) : `v${r.version_no ?? 1}` },
            { text: String(r.category ?? "—") },
            { text: String(r.status), kind: "badge" as const, tone: r.status === "active" ? "good" as const : "neutral" as const },
          ],
        })),
      };
    }
    if (key === "model-assets") {
      const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/admin/model-assets");
      return {
        title: "模型资产",
        primary: { label: "注册模型接口", icon: "plus", actionKey: "register-model" },
        cols: [{ label: "模型名称" }, { label: "协议" }, { label: "model_id" }, { label: "归属" }, { label: "授权范围" }, { label: "状态" }, { label: "操作", width: "96px" }],
        rows: rows.map((m) => ({
          id: String(m.model_asset_id),
          cells: [
            { text: String(m.display_name) },
            { text: m.protocol === "openai_compatible" ? "OpenAI 兼容" : String(m.protocol) },
            { text: String(m.model_id), mono: true },
            { text: m.registered_by === "system" ? "平台" : String(m.registered_by) },
            m.access_scope === "all"
              ? { text: "全员开放", kind: "badge" as const, tone: "good" as const }
              : { text: `限 ${m.grant_count ?? 0} 人`, kind: "badge" as const, tone: "warning" as const },
            { text: String(m.status), kind: "badge" as const, tone: m.status === "active" ? "good" as const : "neutral" as const },
            { text: "白名单授权", kind: "action" as const, onClickKey: "model-grants" },
          ],
        })),
      };
    }
    return M.adminTables[key] ?? M.adminTables.templates;
  },
  // ---- admin B7b：模板版本写闭环 ----
  async getAdminTemplateDetail(templateId) {
    return apiFetch(`/openops/v1/admin/templates/${templateId}`);
  },
  async saveTemplateDraft(templateId, content) {
    return apiFetch(`/openops/v1/admin/templates/${templateId}/versions`, {
      method: "POST",
      body: { client_request_id: crid(), content_json: content },
    });
  },
  async publishTemplateVersion(versionId) {
    await apiFetch(`/openops/v1/admin/template-versions/${versionId}:publish`, {
      method: "POST",
      body: { client_request_id: crid() },
    });
  },
  async disableTemplateVersion(versionId) {
    await apiFetch(`/openops/v1/admin/template-versions/${versionId}:disable`, {
      method: "POST",
      body: { client_request_id: crid() },
    });
  },
  // ---- admin B7a：模板 drill + 标注保存 + 模型授权 ----
  async getAdminTemplateAssets() {
    const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/assets/mcps");
    const platform = rows.filter((r) => r.source_type === "platform");
    return {
      title: "资产治理",
      cols: [{ label: "名称" }, { label: "类型" }, { label: "最新版本" }, { label: "状态" }, { label: "操作", width: "96px" }],
      rows: platform.map((r) => ({
        id: String(r.display_name),
        cells: [
          { text: String(r.display_name) },
          { text: "HTTP MCP" },
          { text: `v${r.version_no ?? 1}` },
          { text: String(r.status), kind: "badge" as const, tone: r.status === "active" ? "good" as const : "warning" as const },
          { text: "Tool 标注", kind: "action" as const, onClickKey: "open-mcp" },
        ],
      })),
    };
  },
  async getAdminMcpTools(mcpName) {
    const raw = await apiFetch<Record<string, unknown>[]>("/openops/v1/admin/mcp-tools");
    const rows = mcpName ? raw.filter((r) => String(r.mcp_display_name) === mcpName) : raw;
    return {
      title: "Tool 标注",
      cols: [{ label: "tool_name" }, { label: "所属 MCP" }, { label: "标注状态" }, { label: "操作", width: "88px" }],
      raw: rows,
      rows: rows.map((r) => {
        const annotated = r.annotation_id != null;
        const blocked = annotated && r.annotation_status !== "allowed";
        const needAsk = Boolean(r.is_approval_required);
        return {
          id: String(r.tool_catalog_id),
          cells: [
            { text: String(r.tool_name), mono: true },
            { text: String(r.mcp_display_name ?? "") },
            !annotated
              ? { text: "未标注 → 运行时 block", kind: "badge" as const, tone: "danger" as const }
              : blocked
                ? { text: `blocked${r.blocked_reason ? " · " + r.blocked_reason : ""}`, kind: "badge" as const, tone: "danger" as const }
                : { text: needAsk ? "allowed · 需审批" : "allowed", kind: "badge" as const, tone: needAsk ? "warning" as const : "good" as const },
            { text: annotated ? "编辑标注" : "标注", kind: "action" as const, onClickKey: "annotate" },
          ],
        };
      }),
    };
  },
  async adminSaveAnnotation(toolCatalogId, payload) {
    await apiFetch(`/openops/v1/admin/mcp-tools/${toolCatalogId}/annotation`, { method: "PUT", body: payload });
  },
  async adminListUsers() {
    const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/admin/users");
    return rows.map((r) => ({ user_id: String(r.user_id), display_name: String(r.display_name ?? "") }));
  },
  async adminAddWhitelist(userId, displayName) {
    await apiFetch("/openops/v1/admin/users/whitelist", {
      method: "POST",
      body: { client_request_id: crid(), user_id: userId, display_name: displayName },
    });
  },
  async adminRevokeWhitelist(userId) {
    await apiFetch("/openops/v1/admin/users/whitelist:revoke", {
      method: "POST",
      body: { client_request_id: crid(), user_id: userId },
    });
  },
  async adminSetRole(userId, role) {
    await apiFetch(`/openops/v1/admin/users/${encodeURIComponent(userId)}:set-role`, {
      method: "POST",
      body: { client_request_id: crid(), role },
    });
  },
  async adminGetModelGrants(modelAssetId) {
    const d = await apiFetch<{ access_scope: string; user_ids: string[] }>(
      `/openops/v1/admin/model-assets/${modelAssetId}/grants`,
    );
    return { access_scope: d.access_scope, user_ids: d.user_ids };
  },
  async adminSaveModelGrants(modelAssetId, accessScope, userIds) {
    await apiFetch(`/openops/v1/admin/model-assets/${modelAssetId}/grants`, {
      method: "PUT",
      body: { client_request_id: crid(), access_scope: accessScope, user_ids: userIds },
    });
  },
  async adminRegisterModel(fields) {
    await apiFetch("/openops/v1/admin/model-assets", {
      method: "POST",
      body: { client_request_id: crid(), protocol: "openai_compatible", ...fields },
    });
  },
  async getSandboxCfg() {
    const rows = await apiFetch<{ key: string; val: unknown; desc: string }[]>("/openops/v1/admin/sandbox");
    return rows.map((r) => ({ key: r.key, desc: r.desc, val: String(r.val) }));
  },
  async saveSandboxCfg(updates, reason) {
    await apiFetch("/openops/v1/admin/sandbox", {
      method: "PUT",
      body: { client_request_id: crid(), updates, reason },
    });
  },
  async getSandboxContainers() {
    return apiFetch<SandboxContainer[]>("/openops/v1/admin/sandbox/containers");
  },
  async destroySandboxContainer(userId, reason) {
    await apiFetch(`/openops/v1/admin/sandbox/containers/${encodeURIComponent(userId)}:destroy`, {
      method: "POST",
      body: { client_request_id: crid(), reason },
    });
  },
  async getAuditTimeline() {
    const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/admin/audit/recent");
    return rows.slice(0, 20).map(auditNode);
  },
  async getAuditTrace(traceId) {
    const rows = await apiFetch<Record<string, unknown>[]>(`/openops/v1/audit/traces/${traceId}`);
    return rows.map(auditNode);
  },

  async getTemplates() {
    const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/templates/available");
    return rows.map((t) => ({
      template_version_id: String(t.template_version_id),
      name: String(t.display_name),
      desc: String(t.description ?? ""),
      capabilities: (t.capabilities as string[]) ?? [],
      active_version: `v${t.version_no ?? 1}`,
    }));
  },
  async getWorkspaces() {
    const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/workspaces");
    return rows.map((w) => ({
      workspace_id: String(w.workspace_id),
      name: String(w.name),
      scope_revision: String(w.scope_revision),
      sync_status: (w.sync_status as Workspace["sync_status"]) ?? "ready",
      updated: "",
    }));
  },
  async getScopeApps() {
    const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/apps");
    return rows.map((a) => ({ app_id: String(a.app_id), name: String(a.name), type: String(a.type ?? ""), tenant_id: String(a.tenant_id ?? "") }));
  },
  async createWorkspace(name, apps) {
    const d = await apiFetch<Record<string, unknown>>("/openops/v1/workspaces", {
      method: "POST",
      // apps 带应用中文名（→ umodel scopes[].projectCn）；app_ids 冗余保留（后端兼容旧形状）
      body: { client_request_id: crid(), name, app_ids: apps.map((a) => a.app_id), apps },
    });
    return { workspace_id: String(d.workspace_id) };
  },
  async createAgentTeam(input) {
    const d = await apiFetch<{ instance: Record<string, unknown> }>("/openops/v1/agent-teams", {
      method: "POST",
      body: {
        client_request_id: crid(), template_version_id: input.template_version_id, name: input.name,
        workspace_id: input.workspace_id, initial_overlay_json: input.initial_overlay_json ?? {},
      },
    });
    return { instance_id: String(d.instance.instance_id) };
  },
  async getAgentTeam(instanceId) {
    const d = await apiFetch<{ instance: Record<string, unknown>; active_config_version: Record<string, unknown> | null }>(
      `/openops/v1/agent-teams/${instanceId}`);
    return {
      instance_id: String(d.instance.instance_id),
      name: String(d.instance.instance_name ?? ""),
      template_version_id: String(d.instance.template_version_id ?? ""),
      workspace_id: String(d.instance.workspace_id ?? ""),
      overlay: (d.active_config_version?.overlay_json as Record<string, unknown>) ?? {},
    };
  },
  async updateAgentTeam(instanceId, input) {
    await apiFetch(`/openops/v1/agent-teams/${instanceId}:update`, {
      method: "POST",
      body: { client_request_id: crid(), name: input.name, workspace_id: input.workspace_id,
              user_llm_config_id: input.user_llm_config_id },
    });
  },

  switchRole(admin: boolean) {
    setDemoUser(admin ? "admin" : "0026demo01", admin ? "李四（管理员）" : "林一");
    runByInstance.clear();
    invalidateConversationHistory();
    invalidateAvailableSkills();
  },
  demoState: () => M.mockWorkbenchState(),
};

/* -------------------------------- mock -------------------------------- */
// 编辑向导 mock 态：per-instance overlay（updateAgentTeam 写入，getAgentTeam 读回，支撑再编辑预填）
const mockOverlays = new Map<string, Record<string, unknown>>();

const mockApi: OpenOpsApi = {
  getMe: () => {
    const me = M.mockMe(demoIdentity.user === "admin" ? "platform_admin" : "user");
    // e2e 缝：openops.mock.nowl=1 模拟未开通白名单（→ 开通引导页，外链 ?q= 保留提示）
    if (typeof localStorage !== "undefined" && localStorage.getItem("openops.mock.nowl") === "1") {
      return delay({ ...me, whitelisted: false, has_instances: false, recent_instance_id: undefined });
    }
    // e2e 缝：localStorage 置 openops.mock.fresh=1 模拟全新用户（无实例→进初始化向导）
    if (typeof localStorage !== "undefined" && localStorage.getItem("openops.mock.fresh") === "1") {
      return delay({ ...me, has_instances: false, recent_instance_id: undefined });
    }
    return delay(me);
  },
  listAgents: () => {
    if (typeof localStorage !== "undefined" && localStorage.getItem("openops.mock.fresh") === "1") {
      return delay([]); // 全新用户无实例（配合 openops.mock.fresh）
    }
    return delay(M.mockAgents);
  },
  getOmodelPageBase: () => delay(""), // mock 无内网 omodel，前端空态
  logout: () => delay({ signout_url: null, login_url: null }), // mock 无 IAM，登出为空操作
  listConversations: (options) => conversationCache.get(options),
  ensureRun: (_instanceId, options) => waitWithSignal(delay("run_demo"), options?.signal),
  createRun: async () => {
    const id = await delay("run_demo_" + Math.random().toString(36).slice(2, 8));
    invalidateConversationHistory();
    return id;
  },
  renameRun: (runId, title) => {
    const c = M.mockConversations.find((x) => x.id === runId);
    if (c) c.title = title;
    return delay(undefined as unknown as void).then(() => invalidateConversationHistory());
  },
  deleteRun: (runId) => {
    const i = M.mockConversations.findIndex((x) => x.id === runId);
    if (i >= 0) M.mockConversations.splice(i, 1);
    return delay(undefined as unknown as void).then(() => invalidateConversationHistory());
  },
  getRunState: (_runId, options) => waitWithSignal(delay({}), options?.signal),
  getMessages: (_runId, options) => waitWithSignal(delay([] as TranscriptMessage[]), options?.signal),
  startTask: () => delay({ task_id: "tsk_demo" }),
  cancelTask: () => delay(undefined as unknown as void),
  closeRun: () => delay(undefined as unknown as void).then(() => invalidateConversationHistory()),
  decideApproval: () => delay(undefined as unknown as void),
  selectModel: () => delay(undefined as unknown as void),
  getActivityEvents: () => delay(normalizeActivityPage({
    items: [{
      event_id: "evt-earlier-run-created",
      audit_event_id: "evt-earlier-run-created",
      event_type: "agent_run.created",
      agent_run_id: "run_demo",
      occurred_at: "2026-07-14T01:59:55Z",
      action: "create",
      payload_redacted_json: { summary: "会话已创建" },
    }],
    next_cursor: null,
    has_more: false,
  })),
  getAuditNodes: () => delay([]),
  toggleInstance: () => delay(undefined as unknown as void),
  deleteInstance: () => delay(undefined as unknown as void),
  getBoundSkills: () => delay(M.mockBoundSkills),
  getAvailableSkills: (instanceId, options) => getAvailableSkillsCached(instanceId, options),
  getSkillLibrary: () => delay(M.mockSkillLibrary),
  getMcpLibrary: () => delay(M.mockMcpLibrary),
  uploadSkill: (file) => delay({ skill_key: file.name.replace(/\.zip$/i, "").toLowerCase(), action: "created" }),
  registerMcp: () => delay(undefined as unknown as void),
  deleteAsset: () => delay(undefined as unknown as void).then(() => invalidateAvailableSkills()),
  bindAsset: (instanceId) => delay(undefined as unknown as void).then(() => invalidateAvailableSkills(instanceId)),
  unbindAsset: () => delay(undefined as unknown as void).then(() => invalidateAvailableSkills()),
  getMainAppend: () => delay("优先关注支付链路核心接口的 P99 与错误率。"),
  saveMainAppend: () => delay(undefined as unknown as void),
  reconcileAssets: () => delay({ skipped: true }),
  getConfigVersions: () => delay(M.mockConfigVersions),
  getModelConfigs: () => delay(M.mockModels),
  createSecret: () => delay({ secret_ref_id: "sec_mock", fingerprint: "sk-…mock" }),
  createLlmConfig: () => delay({ llm_config_id: "llm_mock_" + Math.random().toString(36).slice(2, 8) }),
  // mock「测试连接」：镜像后端 mock 探测（model 含 no-tool → 失败），供离线 UI 验证保存门
  testLlmConnection: (input) => delay(
    input.model_name.toLowerCase().includes("no-tool")
      ? { ok: false, supports_tool_calling: false, reason: "模型不支持 tool calling" }
      : { ok: true, supports_tool_calling: true, reason: null },
  ),
  testModelAssetConnection: () => delay({ ok: true, supports_tool_calling: true, reason: null }),
  getAdminTable: (key) => delay(M.adminTables[key] ?? M.adminTables.templates),
  getSandboxCfg: () => delay(M.sandboxCfg),
  saveSandboxCfg: () => delay(undefined as unknown as void),
  getSandboxContainers: () => delay([] as SandboxContainer[]),
  destroySandboxContainer: () => delay(undefined as unknown as void),
  getAuditTimeline: () => delay(M.auditTimeline),
  getAdminTemplateDetail: () => delay({ template: {}, active_version: null, draft_version: null }),
  saveTemplateDraft: () => delay({}),
  publishTemplateVersion: () => delay(undefined as unknown as void),
  disableTemplateVersion: () => delay(undefined as unknown as void),
  adminAddWhitelist: () => delay(undefined as unknown as void),
  adminRevokeWhitelist: () => delay(undefined as unknown as void),
  adminSetRole: () => delay(undefined as unknown as void),
  getAuditTrace: () => delay(M.auditTimeline),
  getAdminTemplateAssets: () => delay(M.adminTables.assets ?? M.adminTables.templates),
  getAdminMcpTools: () => delay({ ...(M.adminTables["mcp-tools"] ?? M.adminTables.templates), raw: [] }),
  adminSaveAnnotation: () => delay(undefined as unknown as void),
  adminListUsers: () => delay([{ user_id: "0026demo01", display_name: "林一" }]),
  adminGetModelGrants: () => delay({ access_scope: "all", user_ids: [] }),
  adminSaveModelGrants: () => delay(undefined as unknown as void),
  adminRegisterModel: () => delay(undefined as unknown as void),
  getTemplates: () => delay(M.mockTemplates),
  getWorkspaces: () => delay(M.mockWorkspaces),
  getScopeApps: () => delay(M.mockScopeApps),
  createWorkspace: (_name, _apps) => delay({ workspace_id: "ws_mock_" + Math.random().toString(36).slice(2, 8) }),
  createAgentTeam: () => {
    // 建完即非「全新用户」：清 fresh 缝，后续 getMe/listAgents 恢复带实例形状
    // （支撑 e2e「删光后新建→侧栏 picker 兜底重拉」幕，也贴近真实语义）
    if (typeof localStorage !== "undefined") localStorage.removeItem("openops.mock.fresh");
    return delay({ instance_id: "agt_pay_fast_recovery" }, 600);
  },
  getAgentTeam: (instanceId) => {
    const ag = M.mockAgents.find((a) => a.instance_id === instanceId) ?? M.mockAgents[0];
    return delay({
      instance_id: ag.instance_id,
      name: ag.name,
      template_version_id: "tplv_sre_fast_recovery_3",
      workspace_id: ag.workspace_id,
      overlay: mockOverlays.get(ag.instance_id) ?? {},
    });
  },
  updateAgentTeam: (instanceId, input) => {
    // 原地改 module 态（先例：renameRun/deleteRun 改 mockConversations）——保存后清单立即反映
    const ag = M.mockAgents.find((a) => a.instance_id === instanceId);
    if (ag) {
      ag.name = input.name;
      ag.workspace_id = input.workspace_id;
      ag.workspace_label = M.mockWorkspaces.find((w) => w.workspace_id === input.workspace_id)?.name ?? input.workspace_id;
      ag.model = input.user_llm_config_id ? "自定义模型" : "千问 (平台提供)";
    }
    mockOverlays.set(instanceId, input.user_llm_config_id ? { user_llm_config_id: input.user_llm_config_id } : {});
    return delay(undefined as unknown as void);
  },
  switchRole(admin: boolean) {
    setDemoUser(admin ? "admin" : "0026demo01", admin ? "李四（管理员）" : "林一");
    invalidateConversationHistory();
    invalidateAvailableSkills();
  },
  demoState: () => M.mockWorkbenchState(),
};

export const api: OpenOpsApi = API_MODE === "real" ? realApi : mockApi;
export { demoIdentity };
