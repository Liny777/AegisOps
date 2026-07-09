/**
 * OpenOps 前端 API facade —— 单一调用面，按 VITE_OPENOPS_API_MODE 切 mock / real。
 * mock：纯前端跑通设计稿 demo；real：打后端 37 路由（部分 demo 内容仍回退 mock）。
 */
import type {
  Me,
  Role,
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
} from "./types";
import * as M from "./mockData";
import { apiFetch, demoIdentity } from "./client";

export const API_MODE: "mock" | "real" = import.meta.env.VITE_OPENOPS_API_MODE ?? "mock";

const delay = <T>(v: T, ms = 120): Promise<T> => new Promise((r) => setTimeout(() => r(v), ms));

export interface OpenOpsApi {
  getMe(): Promise<Me>;
  listAgents(): Promise<AgentInstance[]>;
  listConversations(): Promise<Conversation[]>;
  getWorkbenchState(instanceId: string): Promise<WorkbenchState>;
  decideApproval(id: string, decision: "approved" | "rejected"): Promise<void>;
  selectModel(runId: string, model: string): Promise<void>;
  // settings
  getBoundSkills(): Promise<AssetRow[]>;
  getSkillLibrary(): Promise<AssetRow[]>;
  getConfigVersions(): Promise<ConfigVersionRow[]>;
  getModelConfigs(): Promise<ModelOption[]>;
  // admin
  getAdminTable(key: string): Promise<AdminTableData>;
  getSandboxCfg(): Promise<SandboxCfg[]>;
  getAuditTimeline(): Promise<AuditNode[]>;
  // init
  getTemplates(): Promise<Template[]>;
  getWorkspaces(): Promise<Workspace[]>;
  getAppTree(): Promise<AppTreeNode[]>;
  createAgentTeam(input: { template_version_id: string; name: string; workspace_id: string }): Promise<{ instance_id: string }>;
}

/* -------------------------------- mock -------------------------------- */
const mockApi: OpenOpsApi = {
  getMe: () => delay(M.mockMe(demoIdentity.role)),
  listAgents: () => delay(M.mockAgents),
  listConversations: () => delay(M.mockConversations),
  getWorkbenchState: () => delay(M.mockWorkbenchState()),
  decideApproval: () => delay(undefined as unknown as void),
  selectModel: () => delay(undefined as unknown as void),
  getBoundSkills: () => delay(M.mockBoundSkills),
  getSkillLibrary: () => delay(M.mockSkillLibrary),
  getConfigVersions: () => delay(M.mockConfigVersions),
  getModelConfigs: () => delay(M.mockModels),
  getAdminTable: (key) => delay(M.adminTables[key] ?? M.adminTables.templates),
  getSandboxCfg: () => delay(M.sandboxCfg),
  getAuditTimeline: () => delay(M.auditTimeline),
  getTemplates: () => delay(M.mockTemplates),
  getWorkspaces: () => delay(M.mockWorkspaces),
  getAppTree: () => delay(M.mockAppTree),
  createAgentTeam: () => delay({ instance_id: "agt_pay_fast_recovery" }, 600),
};

/* -------------------------------- real -------------------------------- */
/** real 模式：后端已有路由走 HTTP，demo-rich 内容（RCA/活动/消息）暂回退 mock。 */
const realApi: OpenOpsApi = {
  async getMe() {
    const d = await apiFetch<{ user_id: string; display_name?: string; role: Role; has_instances?: boolean; recent_instance_id?: string }>("/openops/v1/me");
    const base = M.mockMe(d.role);
    return { ...base, user_id: d.user_id, role: d.role, has_instances: d.has_instances ?? true, recent_instance_id: d.recent_instance_id };
  },
  listAgents: () => mockApi.listAgents(),
  listConversations: () => mockApi.listConversations(),
  getWorkbenchState: (id) => mockApi.getWorkbenchState(id),
  async decideApproval(id, decision) {
    await apiFetch(`/openops/v1/approvals/${id}:decide`, { method: "POST", body: { decision } });
  },
  async selectModel(runId, model) {
    await apiFetch(`/openops/v1/agent-runs/${runId}:select-model`, { method: "POST", body: { model_source: model } });
  },
  getBoundSkills: () => mockApi.getBoundSkills(),
  getSkillLibrary: () => mockApi.getSkillLibrary(),
  getConfigVersions: () => mockApi.getConfigVersions(),
  getModelConfigs: () => mockApi.getModelConfigs(),
  getAdminTable: (key) => mockApi.getAdminTable(key),
  getSandboxCfg: () => mockApi.getSandboxCfg(),
  getAuditTimeline: () => mockApi.getAuditTimeline(),
  getTemplates: () => mockApi.getTemplates(),
  getWorkspaces: () => mockApi.getWorkspaces(),
  getAppTree: () => mockApi.getAppTree(),
  async createAgentTeam(input) {
    const d = await apiFetch<{ instance?: { instance_id: string } }>("/openops/v1/agent-teams", { method: "POST", body: input });
    return { instance_id: d.instance?.instance_id ?? "agt_pay_fast_recovery" };
  },
};

export const api: OpenOpsApi = API_MODE === "real" ? realApi : mockApi;
export { demoIdentity };
