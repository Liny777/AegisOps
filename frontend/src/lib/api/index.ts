/**
 * OpenOps 前端 API facade —— VITE_OPENOPS_API_MODE 切 real（默认，真打后端）/ mock（纯 UI 演示）。
 * real：后端 FastAPI（PG+SSE，vite 代理 /api → 18081）；投影见 projection.ts。
 */
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
  AppTreeNode,
  AssetRow,
  ConfigVersionRow,
  ModelOption,
  ActivityNode,
} from "./types";
import * as M from "./mockData";
import { apiFetch, crid, demoIdentity, setDemoUser } from "./client";
import { auditToNode, projectInstance } from "./projection";

export const API_MODE: "mock" | "real" =
  (import.meta.env.VITE_OPENOPS_API_MODE as "mock" | "real" | undefined) ?? "real";

const delay = <T,>(v: T, ms = 120): Promise<T> => new Promise((r) => setTimeout(() => r(v), ms));

/* ------------------------------ 接口面 ------------------------------ */
export interface OpenOpsApi {
  getMe(): Promise<Me>;
  listAgents(): Promise<AgentInstance[]>;
  listConversations(): Promise<Conversation[]>;
  // 运行态（real：ensureRun → state/task/approval/SSE）
  ensureRun(instanceId: string): Promise<string>; // → run_id
  createRun(instanceId: string): Promise<string>; // always create a new run
  getRunState(runId: string): Promise<Record<string, unknown>>;
  startTask(runId: string, text: string): Promise<{ task_id: string }>;
  cancelTask(taskId: string): Promise<void>;
  closeRun(runId: string): Promise<void>;
  decideApproval(id: string, decision: "approved" | "rejected"): Promise<void>;
  selectModel(runId: string, model: string): Promise<void>;
  getAuditNodes(runId: string): Promise<ActivityNode[]>;
  // 实例管理（新原型清单页）
  toggleInstance(instanceId: string, enabled: boolean): Promise<void>;
  deleteInstance(instanceId: string): Promise<void>;
  // settings
  getBoundSkills(instanceId: string): Promise<AssetRow[]>;
  getSkillLibrary(): Promise<AssetRow[]>;
  getMcpLibrary(): Promise<AssetRow[]>;
  getConfigVersions(instanceId: string): Promise<ConfigVersionRow[]>;
  getModelConfigs(): Promise<ModelOption[]>;
  // settings 写闭环（B6：上传/注册/删除/绑定/解绑/main 追加/对账）
  uploadSkill(name: string): Promise<void>;
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
  getAuditTimeline(): Promise<AuditNode[]>;
  // admin B7a：模板 drill（资产治理→Tool 标注）+ 标注保存 + 模型资产授权
  getAdminTemplateAssets(): Promise<AdminTableData>;
  getAdminMcpTools(mcpName: string | null): Promise<AdminTableData & { raw: Record<string, unknown>[] }>;
  adminSaveAnnotation(toolCatalogId: string, payload: Record<string, unknown>): Promise<void>;
  adminListUsers(): Promise<{ user_id: string; display_name: string }[]>;
  adminGetModelGrants(modelAssetId: string): Promise<{ access_scope: string; user_ids: string[] }>;
  adminSaveModelGrants(modelAssetId: string, accessScope: string, userIds: string[]): Promise<void>;
  adminRegisterModel(fields: { display_name: string; model_id: string; base_url?: string; secret_env_var?: string; access_scope: string }): Promise<void>;
  // init
  getTemplates(): Promise<Template[]>;
  getWorkspaces(): Promise<Workspace[]>;
  getAppTree(): Promise<AppTreeNode[]>;
  createAgentTeam(input: { template_version_id: string; name: string; workspace_id: string }): Promise<{ instance_id: string }>;
  // demo 身份
  switchRole(admin: boolean): void;
  demoState(): WorkbenchState; // mock 兜底静态（composer skills/models 等）
}

/* -------------------------------- real -------------------------------- */
const runByInstance = new Map<string, string>();

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
    const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/agent-teams");
    return rows.map(projectInstance);
  },
  listConversations: () => delay(M.mockConversations),

  async ensureRun(instanceId) {
    const cached = runByInstance.get(instanceId);
    if (cached) return cached;
    const runs = await apiFetch<Record<string, unknown>[]>("/openops/v1/agent-runs");
    const active = runs.find(
      (r) => String(r.agent_team_instance_id) === instanceId && r.run_status === "active",
    );
    if (active) {
      const id = String(active.agent_run_id);
      runByInstance.set(instanceId, id);
      return id;
    }
    const d = await apiFetch<{ run: Record<string, unknown> }>("/openops/v1/agent-runs", {
      method: "POST",
      body: { client_request_id: crid(), agent_team_instance_id: instanceId },
    });
    const id = String(d.run.agent_run_id);
    runByInstance.set(instanceId, id);
    return id;
  },
  async createRun(instanceId) {
    const d = await apiFetch<{ run: Record<string, unknown> }>("/openops/v1/agent-runs", {
      method: "POST",
      body: { client_request_id: crid(), agent_team_instance_id: instanceId },
    });
    const id = String(d.run.agent_run_id);
    runByInstance.set(instanceId, id);
    return id;
  },
  getRunState: (runId) => apiFetch(`/openops/v1/agent-runs/${runId}/state`),
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
  },
  async decideApproval(id, decision) {
    await apiFetch(`/openops/v1/approvals/${id}:decide`, {
      method: "POST",
      body: { client_request_id: crid(), decision },
    });
  },
  async selectModel(runId, model) {
    await apiFetch(`/openops/v1/agent-runs/${runId}:select-model`, {
      method: "POST",
      body: { client_request_id: crid(), model_source: model },
    });
  },
  async getAuditNodes(runId) {
    const rows = await apiFetch<Record<string, unknown>[]>(`/openops/v1/audit/runs/${runId}`);
    return rows.map(auditToNode);
  },

  async toggleInstance(instanceId, enabled) {
    await apiFetch(`/openops/v1/agent-teams/${instanceId}:${enabled ? "enable" : "disable"}`, { method: "POST", body: {} });
  },
  async deleteInstance(instanceId) {
    await apiFetch(`/openops/v1/agent-teams/${instanceId}`, { method: "DELETE" });
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
      assetId: String(r.skill_id ?? r.mcp_id ?? ""),
    }));
  },
  async getSkillLibrary() {
    const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/assets/skills");
    return rows.map((r) => ({
      id: String(r.skill_id),
      name: String(r.display_name),
      version: `v${r.version_no ?? 1}`,
      status: String(r.status),
      statusTone: r.status === "active" ? "good" as const : "warning" as const,
      meta: r.source_type === "platform" ? "平台 Skill" : "我的 Skill",
      bound: false,
      kind: "skill" as const,
      versionId: r.skill_version_id ? String(r.skill_version_id) : undefined,
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
  async uploadSkill(name) {
    await apiFetch("/openops/v1/assets/skills", {
      method: "POST",
      body: { client_request_id: crid(), display_name: name, manifest_json: { entrypoint: "run.py" }, checksum_sha256: "" },
    });
  },
  async registerMcp(name, endpoint) {
    await apiFetch("/openops/v1/assets/mcps", {
      method: "POST",
      body: { client_request_id: crid(), display_name: name, transport: "http", endpoint, manifest_json: {} },
    });
  },
  async deleteAsset(kind, id) {
    await apiFetch(`/openops/v1/assets/${kind === "mcp" ? "mcps" : "skills"}/${id}`, { method: "DELETE" });
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
  },
  async unbindAsset(bindingId) {
    await apiFetch(`/openops/v1/asset-bindings/${bindingId}`, { method: "DELETE" });
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
      creation_date: String(r.creation_date ?? "").replace("T", " ").slice(0, 16),
    }));
  },
  async getModelConfigs() {
    const platformRows = await apiFetch<Record<string, unknown>[]>("/openops/v1/models/platform");
    const platform: ModelOption[] = platformRows.map((r, idx) => ({
      llm_config_id: `platform:${String(r.model_id)}`,
      label: String(r.name ?? r.model_id),
      note: `${r.protocol ?? "OpenAI 兼容"} · ${r.probe ?? "待探测"}`,
      available: r.status === "active",
      current: idx === 0,
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

  async getAdminTable(key) {
    if (key === "templates") {
      const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/admin/templates");
      return {
        title: "模板管理",
        cols: [{ label: "模板名" }, { label: "template_key" }, { label: "状态" }, { label: "操作", width: "96px" }],
        rows: rows.map((r) => ({
          id: String(r.template_id),
          cells: [
            { text: String(r.display_name) },
            { text: String(r.template_key), mono: true },
            { text: String(r.status), kind: "badge" as const, tone: r.status === "active" ? "good" as const : "neutral" as const },
            { text: "资产治理", kind: "action" as const, onClickKey: "open-template" },
          ],
        })),
      };
    }
    if (key === "users") {
      const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/admin/users");
      return {
        title: "用户与白名单",
        primary: { label: "加入白名单", icon: "plus", actionKey: "add-user" },
        cols: [{ label: "user_id" }, { label: "展示名" }, { label: "role" }, { label: "白名单" }, { label: "最近登录" }],
        rows: rows.map((r) => ({
          id: String(r.user_id),
          cells: [
            { text: String(r.user_id), mono: true },
            { text: String(r.display_name ?? "") },
            { text: String(r.role) },
            { text: String(r.whitelist_status), kind: "badge" as const, tone: r.whitelist_status === "active" ? "good" as const : "neutral" as const },
            { text: String(r.last_login_at ?? "—").slice(11, 16) || "—" },
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
  async getAuditTimeline() {
    const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/admin/audit/recent");
    return rows.slice(0, 20).map((e) => ({
      event: String(e.event_type),
      detail: [e.task_id, e.reason_code, e.decision].filter(Boolean).join(" · ") || String(e.action ?? ""),
    }));
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
  getAppTree: () => delay(M.mockAppTree),
  async createAgentTeam(input) {
    const d = await apiFetch<{ instance: Record<string, unknown> }>("/openops/v1/agent-teams", {
      method: "POST",
      body: { client_request_id: crid(), template_version_id: input.template_version_id, name: input.name, workspace_id: input.workspace_id },
    });
    return { instance_id: String(d.instance.instance_id) };
  },

  switchRole(admin: boolean) {
    setDemoUser(admin ? "admin" : "0026demo01", admin ? "李四（管理员）" : "林一");
    runByInstance.clear();
  },
  demoState: () => M.mockWorkbenchState(),
};

/* -------------------------------- mock -------------------------------- */
const mockApi: OpenOpsApi = {
  getMe: () => delay(M.mockMe(demoIdentity.user === "admin" ? "platform_admin" : "user")),
  listAgents: () => delay(M.mockAgents),
  listConversations: () => delay(M.mockConversations),
  ensureRun: () => delay("run_demo"),
  createRun: () => delay("run_demo_" + Math.random().toString(36).slice(2, 8)),
  getRunState: () => delay({}),
  startTask: () => delay({ task_id: "tsk_demo" }),
  cancelTask: () => delay(undefined as unknown as void),
  closeRun: () => delay(undefined as unknown as void),
  decideApproval: () => delay(undefined as unknown as void),
  selectModel: () => delay(undefined as unknown as void),
  getAuditNodes: () => delay([]),
  toggleInstance: () => delay(undefined as unknown as void),
  deleteInstance: () => delay(undefined as unknown as void),
  getBoundSkills: () => delay(M.mockBoundSkills),
  getSkillLibrary: () => delay(M.mockSkillLibrary),
  getMcpLibrary: () => delay([]),
  uploadSkill: () => delay(undefined as unknown as void),
  registerMcp: () => delay(undefined as unknown as void),
  deleteAsset: () => delay(undefined as unknown as void),
  bindAsset: () => delay(undefined as unknown as void),
  unbindAsset: () => delay(undefined as unknown as void),
  getMainAppend: () => delay("优先关注支付链路核心接口的 P99 与错误率。"),
  saveMainAppend: () => delay(undefined as unknown as void),
  reconcileAssets: () => delay({ skipped: true }),
  getConfigVersions: () => delay(M.mockConfigVersions),
  getModelConfigs: () => delay(M.mockModels),
  getAdminTable: (key) => delay(M.adminTables[key] ?? M.adminTables.templates),
  getSandboxCfg: () => delay(M.sandboxCfg),
  saveSandboxCfg: () => delay(undefined as unknown as void),
  getAuditTimeline: () => delay(M.auditTimeline),
  getAdminTemplateAssets: () => delay(M.adminTables.assets ?? M.adminTables.templates),
  getAdminMcpTools: () => delay({ ...(M.adminTables["mcp-tools"] ?? M.adminTables.templates), raw: [] }),
  adminSaveAnnotation: () => delay(undefined as unknown as void),
  adminListUsers: () => delay([{ user_id: "0026demo01", display_name: "林一" }]),
  adminGetModelGrants: () => delay({ access_scope: "all", user_ids: [] }),
  adminSaveModelGrants: () => delay(undefined as unknown as void),
  adminRegisterModel: () => delay(undefined as unknown as void),
  getTemplates: () => delay(M.mockTemplates),
  getWorkspaces: () => delay(M.mockWorkspaces),
  getAppTree: () => delay(M.mockAppTree),
  createAgentTeam: () => delay({ instance_id: "agt_pay_fast_recovery" }, 600),
  switchRole(admin: boolean) {
    setDemoUser(admin ? "admin" : "0026demo01", admin ? "李四（管理员）" : "林一");
  },
  demoState: () => M.mockWorkbenchState(),
};

export const api: OpenOpsApi = API_MODE === "real" ? realApi : mockApi;
export { demoIdentity };
