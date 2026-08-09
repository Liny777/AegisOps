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
  SandboxDestroyResult,
  AuditNode,
  Template,
  Workspace,
  WorkspaceDetail,
  OmodelStatistics,
  ScopeApp,
  Skill,
  AssetRow,
  AssetQuery,
  Paged,
  ConfigVersionRow,
  ModelOption,
  ModelTemplateOption,
  AdminModelTemplate,
  AdminModelAssetOption,
  ActivityNode,
  ActivityEventsPage,
} from "./types";
import * as M from "./mockData";
import { API_MODE, apiFetch, crid, demoIdentity } from "./client";
import { auditToNode, projectInstance } from "./projection";
import { normalizeActivityPage } from "../activity";

/** 后端 /assets/* 分页信封（snake_case 原样）→ 前端 Paged<T>（camelCase）。 */
type AssetPageDto = { items: Record<string, unknown>[]; total: number; page: number; page_size: number };

/** 资产列表查询串：过滤/搜索一律带给服务端——分页 + 客户端过滤 = 每页数量错乱。 */
const assetQs = (p?: AssetQuery): string => {
  const s = new URLSearchParams();
  s.set("page", String(p?.page ?? 1));
  s.set("page_size", String(p?.pageSize ?? 20)); // 后端上限 100（29.3 §2.2）
  if (p?.sourceType) s.set("source_type", p.sourceType);
  const q = p?.q?.trim();
  if (q) s.set("q", q);
  if (p?.tag) s.set("tag", p.tag);
  return `?${s.toString()}`;
};

const toSkillRow = (r: Record<string, unknown>): AssetRow => ({
  id: String(r.skill_id),
  name: String(r.display_name),
  // 版本优先 SkillHub §2.2 semver（latest_version），缺失回退本地 v{version_no}（用户上传前/mock）
  version: r.latest_version ? String(r.latest_version) : `v${r.version_no ?? 1}`,
  status: String(r.status),
  statusTone: r.status === "active" ? "good" : "warning",
  meta: r.source_type === "platform" ? "平台 Skill" : "我的 Skill",
  bound: false,
  kind: "skill",
  sourceType: r.source_type === "platform" ? "platform" : "user",
  versionId: r.skill_version_id ? String(r.skill_version_id) : undefined,
  skillKey: r.skill_key ? String(r.skill_key) : undefined, // 模板编辑器勾选用（运行时白名单键）
  description: r.description ? String(r.description) : undefined, // 插件页「说明」真描述
  category: r.category ? String(r.category) : undefined,
});

const toMcpRow = (r: Record<string, unknown>): AssetRow => ({
  id: String(r.mcp_id),
  name: String(r.display_name),
  version: `v${r.version_no ?? 1}`,
  status: String(r.status),
  statusTone: r.status === "active" ? "good" : "warning",
  meta: r.source_type === "platform" ? "平台 MCP" : "我的 MCP",
  bound: false,
  kind: "mcp",
  sourceType: r.source_type === "platform" ? "platform" : "user",
  versionId: r.mcp_version_id ? String(r.mcp_version_id) : undefined,
  description: r.description ? String(r.description) : undefined, // registry 服务描述
  category: r.category ? String(r.category) : undefined,
});

/** 翻完所有页取全量（pageSize 用上限 100 压页数）。
 *  给**确实需要完整集合**的消费方用：模板编辑器的 skills 白名单勾选、工作台的资产统计。
 *  这些地方绝不能只取默认首页——否则超出一页的资产会被静默漏掉（正是上游 page_size 截断踩过的坑）。 */
const fetchAllAssets = async (
  kind: "skills" | "mcps",
  sourceType?: AssetQuery["sourceType"],
): Promise<Record<string, unknown>[]> => {
  const out: Record<string, unknown>[] = [];
  for (let page = 1; ; page++) {
    const d = await apiFetch<AssetPageDto>(`/openops/v1/assets/${kind}${assetQs({ page, pageSize: 100, sourceType })}`);
    out.push(...d.items);
    if (!d.items.length || page * d.page_size >= d.total) break;
  }
  return out;
};
import {
  KeyedSingleFlightCache,
  SingleFlightCache,
  waitWithSignal,
} from "./singleFlight";
import type { RequestOptions } from "./singleFlight";

export type { RequestOptions } from "./singleFlight";
export { isAbortError } from "./singleFlight";

// API_MODE 定义在 client.ts（传输层配置单一真源），此处再导出保持既有 import 面不变
export { API_MODE } from "./client";

/** 「测试连接」结果（用户自带 / 平台模型共用）：ok=true 才允许保存。 */
export interface TestConnResult {
  ok: boolean;
  supports_tool_calling: boolean;
  reason: string | null;
  /** 后端探测模式：mock=未真实打网（UI 须显示「未真实探测」而非绿勾，别再让假探测冒充真的）。 */
  probe_mode?: "mock" | "real";
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
  /** 看护空间统计四数（Agent 初始化「确认能力清单」页；上游失败后端降级为 0）。 */
  getWorkspaceStatistics(workspaceId: string): Promise<OmodelStatistics>;
  /** 登出（B9）：清后端 IAM 会话缓存，返回 IAM signout（**须后台 POST + CSRF 头**调用，非浏览器
   * GET 导航）/ login 地址、CSRF cookie/header 名、回跳地址（未配 IAM 时相关字段为 null）。 */
  logout(): Promise<{
    signout_url: string | null;
    login_url: string | null;
    csrf_cookie_name: string | null;
    csrf_header_name: string | null;
    redirect_url: string | null;
  }>;
  listConversations(options?: RequestOptions): Promise<Conversation[]>;
  // 运行态（real：ensureRun → state/task/approval/SSE）
  ensureRun(instanceId: string, options?: RequestOptions): Promise<string>; // → run_id
  createRun(instanceId: string): Promise<string>; // always create a new run
  renameRun(runId: string, title: string): Promise<void>;
  deleteRun(runId: string): Promise<void>;
  getRunState(runId: string, options?: RequestOptions): Promise<Record<string, unknown>>;
  /** 名额满时后端不再 429，而是入队：status="queued" + queue_position（前端显示排队条）。 */
  startTask(runId: string, text: string): Promise<{ task_id: string; status?: string; queue_position?: number }>;
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
  /** 资产库分页读（服务端过滤/搜索；对齐 29.3 §2.2 页码式 + total）。 */
  getSkillLibrary(params?: AssetQuery): Promise<Paged<AssetRow>>;
  getMcpLibrary(params?: AssetQuery): Promise<Paged<AssetRow>>;
  /** 全量读（内部翻完所有页）：**确实需要完整集合**的地方用（模板编辑器白名单勾选、工作台统计）——
   *  这些场景取单页会静默漏掉超出一页的资产。列表 UI 一律用上面的分页读。 */
  getAllSkills(): Promise<AssetRow[]>;
  getAllMcps(): Promise<AssetRow[]>;
  getConfigVersions(instanceId: string): Promise<ConfigVersionRow[]>;
  getModelConfigs(): Promise<ModelOption[]>;
  /** 模型模板清单（38 号：主/子 Agent 模型组合）——初始化向导「选一套模板」与展示面数据源；
   * 按模板级 ACL 过滤（38.1：scope=all ∪ 白名单授权）。 */
  getModelTemplates(): Promise<ModelTemplateOption[]>;
  // 用户自定义 LLM（探测真化闭合）：录 Secret → 建 llm-config（服务端探测+egress）
  createSecret(secretName: string, secretValue: string): Promise<{ secret_ref_id: string; fingerprint?: string }>;
  createLlmConfig(input: { display_name: string; base_url: string; model_name: string; secret_ref_id: string; context_window_tokens?: number }): Promise<{ llm_config_id: string }>;
  /** 用户自带模型「测试连接」（存前探测，不落库）：egress + tool-calling 探测。 */
  testLlmConnection(input: { base_url: string; model_name: string; api_key: string }): Promise<TestConnResult>;
  // settings 写闭环（B6：上传/注册/删除/绑定/解绑/main 追加/对账）
  /** 上传 Skill ZIP（29.3 §2.1 multipart）：仅 file 必填（分类/标签已移除）。
   * 29.9 起 skill_key 是命名空间化 id（user-{工号}-{name}），display_name 才是原始名（横幅展示用）。 */
  uploadSkill(file: File): Promise<{ skill_key: string; display_name?: string; action: string }>;
  registerMcp(name: string, endpoint: string): Promise<void>;
  deleteAsset(kind: "skill" | "mcp", id: string): Promise<void>;
  bindAsset(instanceId: string, row: AssetRow): Promise<void>;
  unbindAsset(bindingId: string): Promise<void>;
  /** 个人 skill/MCP 解绑（默认自动挂载的 opt-out）：记一条 muted 绑定行；重新绑定用 unbindAsset(该 mute 行 id)。 */
  unbindOwnAsset(kind: "skill" | "mcp", instanceId: string, row: AssetRow): Promise<void>;
  getMainAppend(instanceId: string): Promise<string>;
  saveMainAppend(instanceId: string, text: string): Promise<void>;
  reconcileAssets(): Promise<Record<string, unknown>>;
  // admin
  /** 管理台表格；分页/搜索参数由 Skill 基线（key="skills"）与用户表（key="users"）消费（服务端过滤 + 分页）。 */
  getAdminTable(key: string, params?: { page?: number; pageSize?: number; q?: string; tag?: string }): Promise<AdminTableData>;
  getSandboxCfg(): Promise<SandboxCfg[]>;
  saveSandboxCfg(updates: Record<string, unknown>, reason: string): Promise<void>;
  getSandboxContainers(): Promise<SandboxContainer[]>;
  /** destroyed=false 表示容器本就不在（已被回收），非报错；cancelled_task_ids=连带中断的运行中任务。 */
  destroySandboxContainer(userId: string, reason: string): Promise<SandboxDestroyResult>;
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
  adminAddWhitelist(userId: string, displayName: string, tags?: string[]): Promise<void>;
  /** 告警接管白名单开关（算力保护；管理台用户表「告警接管」列）。 */
  adminSetAlertGrant(userId: string, granted: boolean): Promise<void>;
  adminRevokeWhitelist(userId: string): Promise<void>;
  adminSetRole(userId: string, role: "user" | "platform_admin"): Promise<void>;
  /** 改领域标签（整体替换，[] 清空）——「标签」单元格行内编辑用。 */
  adminSetTags(userId: string, tags: string[]): Promise<void>;
  /** 删除用户（软删 + 连带撤白名单）：该用户重新被加白名单前不再出现在列表。 */
  adminDeleteUser(userId: string): Promise<void>;
  getAuditTrace(traceId: string): Promise<AuditNode[]>;
  // admin B7a：模板 drill（资产治理→Tool 标注）+ 标注保存 + 模型资产授权
  getAdminTemplateAssets(): Promise<AdminTableData>;
  getAdminMcpTools(mcpName: string | null): Promise<AdminTableData & { raw: Record<string, unknown>[] }>;
  adminSaveAnnotation(toolCatalogId: string, payload: Record<string, unknown>): Promise<void>;
  adminListUsers(): Promise<{ user_id: string; display_name: string }[]>;
  /** 标签下拉候选：所有未删用户已用的领域标签（去重、排序）——「用户与白名单」页头筛选用。 */
  adminListUserTags(): Promise<string[]>;
  // Agent Studio 的 api 已随垂直切片外移到 src/studio/api.ts（core 门面不再承载切片方法）
  /** 模板白名单授权（38.1：授权在模板维度，原资产 /grants 已移除）。 */
  adminGetModelTemplateGrants(modelTemplateId: string): Promise<{ access_scope: string; user_ids: string[] }>;
  adminSaveModelTemplateGrants(modelTemplateId: string, accessScope: string, userIds: string[]): Promise<void>;
  adminRegisterModel(fields: { display_name: string; model_id: string; base_url?: string; secret_env_var?: string; context_window_tokens?: number }): Promise<void>;
  /** 平台模型「测试连接」：Key 走服务器环境变量（secret_env_var 名），客户端不持 Key。 */
  testModelAssetConnection(input: { base_url: string; model_id: string; secret_env_var: string }): Promise<TestConnResult>;
  // admin 模型模板（38 号）：主/子 Agent 槽位编排 CRUD
  adminListModelTemplates(): Promise<AdminModelTemplate[]>;
  /** 模板编辑弹窗的槽位候选（复用 GET /admin/model-assets）。 */
  adminListModelAssets(): Promise<AdminModelAssetOption[]>;
  adminCreateModelTemplate(input: { display_name: string; description?: string; main_model_asset_id: string; sub_model_asset_id: string; access_scope?: "all" | "restricted"; is_default?: boolean }): Promise<void>;
  /** PATCH 语义：只传要改的键。 */
  adminUpdateModelTemplate(id: string, input: { display_name?: string; description?: string; main_model_asset_id?: string; sub_model_asset_id?: string }): Promise<void>;
  adminSetModelTemplateStatus(id: string, status: "active" | "disabled"): Promise<void>;
  adminSetModelTemplateDefault(id: string): Promise<void>;
  /** 软删模板（38.2）：允许删默认模板；已绑实例下次任务降级回平台默认。 */
  adminDeleteModelTemplate(id: string): Promise<void>;
  /** 软删模型资产（38.2）：被模板槽位引用时后端 400（提示先调整模板）。 */
  adminDeleteModelAsset(id: string): Promise<void>;
  // init
  getTemplates(): Promise<Template[]>;
  getWorkspaces(): Promise<Workspace[]>;
  /** 单个 workspace 实时状态（服务状态面板 oModel/范围 chip 用）：同步态 + 真实 APPID 列表。 */
  getWorkspaceStatus(workspaceId: string): Promise<{ sync_status: string; scope_revision: string; app_ids: string[] }>;
  getScopeApps(): Promise<ScopeApp[]>;
  createWorkspace(name: string, apps: { app_id: string; name?: string; tenant_id?: string }[]): Promise<{ workspace_id: string }>;
  /** 管理员代查通道：手输 APPID（未经权限过滤）创建范围；reason 必填进审计。 */
  adminCreateWorkspace(name: string, apps: { app_id: string; name?: string }[], reason: string): Promise<{ workspace_id: string }>;
  /** 编辑向导预填：范围详情（含 name + 已选 app_ids）。 */
  getWorkspace(workspaceId: string): Promise<WorkspaceDetail>;
  /** 编辑系统范围（改名 + 重选应用）：apps 全量覆盖 scopes。 */
  updateWorkspace(workspaceId: string, name: string, apps: { app_id: string; name?: string; tenant_id?: string }[]): Promise<{ workspace_id: string }>;
  /** 删除系统范围（软删，幂等）。 */
  deleteWorkspace(workspaceId: string): Promise<void>;
  createAgentTeam(input: { template_version_id: string; name: string; workspace_id: string; initial_overlay_json?: Record<string, unknown> }): Promise<{ instance_id: string }>;
  /** 编辑向导预填：实例真实字段 + active overlay（区别于 listAgents 的卡片展示投影）。 */
  getAgentTeam(instanceId: string): Promise<AgentTeamDetail>;
  /** 编辑保存（POST :update）：改名 / 换 workspace / 换模型。模型四态互斥：
   * user_llm_config_id（BYO）/ model_template_id（模型模板）/ platform_model_id（legacy 原样回传）/ 全 null=平台默认。 */
  updateAgentTeam(instanceId: string, input: { name: string; workspace_id: string; user_llm_config_id: string | null; platform_model_id: string | null; model_template_id: string | null }): Promise<void>;
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
  // 29.9 命名空间化：skill_key 带 user-{工号}-/system- 前缀，人打的斜杠名用原始名（display_name）。
  // 后端对 "/" hint 做「原始名→key」唯一别名解析；display_name 含空白（hint 按空白分词）或本集合内
  // 重名（解析多义）时回退完整 key，保证插进输入框的 token 一定能被解析。
  const dup = new Map<string, number>();
  for (const r of rows) {
    const d = String(r.display_name ?? "");
    dup.set(d, (dup.get(d) ?? 0) + 1);
  }
  return rows.map((r) => {
    const key = String(r.skill_key);
    const dn = String(r.display_name ?? key);
    const token = /^\S+$/.test(dn) && (dup.get(dn) ?? 0) <= 1 ? dn : key;
    return {
      skill_id: key,
      name: "/" + token,
      desc: `${key} · ${r.source_type === "platform" ? "平台" : "我的"}`,
    };
  });
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

/** audit_event 行 → 审计页节点（recent 与 by-trace 共用）。
 * detail 补读 payload 里的模型模板降级键（slot/model_template_id/reason_summary）——
 * 审计页是 model.template_degraded 的唯一排障入口（工作台主 Agent 时间线已下线）。 */
const auditNode = (e: Record<string, unknown>) => {
  const p = (e.payload_redacted_json ?? {}) as Record<string, unknown>;
  const extras = [
    p.slot ? `槽位 ${String(p.slot)}` : null,
    p.model_template_id ? `模板 ${String(p.model_template_id).slice(0, 8)}` : null,
    p.reason_summary ? String(p.reason_summary) : null,
  ].filter(Boolean) as string[];
  return {
    event: String(e.event_type),
    detail: [e.task_id, e.reason_code, e.decision, ...extras].filter(Boolean).join(" · ")
      || String(e.action ?? ""),
    trace: e.audit_trace_id ? String(e.audit_trace_id) : undefined,
  };
};

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
  async getWorkspaceStatistics(workspaceId) {
    return apiFetch<OmodelStatistics>(`/openops/v1/workspaces/${encodeURIComponent(workspaceId)}/statistics`);
  },
  async logout() {
    const d = await apiFetch<{
      signout_url?: string | null; login_url?: string | null;
      csrf_cookie_name?: string | null; csrf_header_name?: string | null; redirect_url?: string | null;
    }>("/openops/v1/auth/logout", { method: "POST" });
    return {
      signout_url: d.signout_url ?? null,
      login_url: d.login_url ?? null,
      csrf_cookie_name: d.csrf_cookie_name ?? null,
      csrf_header_name: d.csrf_header_name ?? null,
      redirect_url: d.redirect_url ?? null,
    };
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
      bindingStatus: String(r.binding_status ?? r.status ?? "active"),
    }));
  },
  getAvailableSkills: (instanceId, options) => getAvailableSkillsCached(instanceId, options),
  async getSkillLibrary(params) {
    const d = await apiFetch<AssetPageDto>(`/openops/v1/assets/skills${assetQs(params)}`);
    return { items: d.items.map(toSkillRow), total: d.total, page: d.page, pageSize: d.page_size };
  },
  async getAllSkills() {
    return (await fetchAllAssets("skills")).map(toSkillRow);
  },
  async getAllMcps() {
    return (await fetchAllAssets("mcps")).map(toMcpRow);
  },
  async getMcpLibrary(params) {
    const d = await apiFetch<AssetPageDto>(`/openops/v1/assets/mcps${assetQs(params)}`);
    return { items: d.items.map(toMcpRow), total: d.total, page: d.page, pageSize: d.page_size };
  },
  // ---- settings 写闭环（B6） ----
  async uploadSkill(file) {
    const fd = new FormData();
    fd.append("file", file);
    const d = await apiFetch<Record<string, unknown>>("/openops/v1/assets/skills:upload", { method: "POST", body: fd });
    return {
      skill_key: String(d.skill_key ?? ""),
      display_name: d.display_name ? String(d.display_name) : undefined, // 29.9：原始名（横幅展示）
      action: String(d.action ?? "created"),
    };
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
  async unbindOwnAsset(kind, instanceId, row) {
    const isSkill = kind === "skill";
    await apiFetch(`/openops/v1/agent-teams/${instanceId}/${kind}-mutes`, {
      method: "POST",
      body: {
        client_request_id: crid(),
        asset_type: kind,
        skill_id: isSkill ? row.id : null,
        skill_version_id: isSkill ? row.versionId ?? null : null,
        mcp_id: isSkill ? null : row.id,
        mcp_version_id: isSkill ? null : row.versionId ?? null,
      },
    });
    invalidateAvailableSkills(instanceId);
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
  async getModelTemplates() {
    const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/models/templates");
    return rows.map((r) => {
      const main = (r.main_model ?? {}) as Record<string, unknown>;
      const sub = (r.sub_model ?? {}) as Record<string, unknown>;
      return {
        model_template_id: String(r.model_template_id),
        display_name: String(r.display_name),
        description: r.description ? String(r.description) : undefined,
        main_model: { model_id: String(main.model_id ?? ""), display_name: String(main.display_name ?? main.model_id ?? "") },
        sub_model: { model_id: String(sub.model_id ?? ""), display_name: String(sub.display_name ?? sub.model_id ?? "") },
        access_scope: r.access_scope === "restricted" ? ("restricted" as const) : ("all" as const),
        is_default: r.is_default === true,
        status: r.status === "disabled" ? ("disabled" as const) : ("active" as const),
      };
    });
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
    return { ok: Boolean(d.ok), supports_tool_calling: Boolean(d.supports_tool_calling), reason: d.reason ? String(d.reason) : null, probe_mode: d.probe_mode === "mock" ? "mock" : "real" };
  },
  async testModelAssetConnection(input) {
    const d = await apiFetch<Record<string, unknown>>("/openops/v1/admin/model-assets:test-connection", {
      method: "POST",
      body: { base_url: input.base_url, model_id: input.model_id, secret_env_var: input.secret_env_var },
    });
    return { ok: Boolean(d.ok), supports_tool_calling: Boolean(d.supports_tool_calling), reason: d.reason ? String(d.reason) : null, probe_mode: d.probe_mode === "mock" ? "mock" : "real" };
  },

  async getAdminTable(key, params) {
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
      // 服务端分页 + q 搜索（user_id/display_name 模糊）：分页后客户端 filter 会每页数量错乱
      const d = await apiFetch<AssetPageDto>(`/openops/v1/admin/users${assetQs(params)}`);
      return {
        title: "用户与白名单",
        primary: { label: "加入白名单", icon: "plus", actionKey: "add-user" },
        cols: [{ label: "user_id" }, { label: "展示名" }, { label: "标签" }, { label: "role" }, { label: "白名单" }, { label: "告警接管", width: "88px" }, { label: "最近登录" }, { label: "操作", width: "72px" }, { label: "角色", width: "110px" }, { label: "删除", width: "56px" }],
        rows: d.items.map((r) => {
          const tags = Array.isArray(r.tags_json) ? r.tags_json.map(String) : [];
          return {
            id: String(r.user_id),
            cells: [
              { text: String(r.user_id), mono: true },
              { text: String(r.display_name ?? "") },
              // 领域标签（多标签）：单元格即入口，点击行内编辑（prompt），不再单加一列操作
              { text: tags.length ? tags.join("、") : "设标签", kind: "action" as const, onClickKey: "user-tags" },
              { text: String(r.role) },
              { text: String(r.whitelist_status), kind: "badge" as const, tone: r.whitelist_status === "active" ? "good" as const : "neutral" as const },
              // 告警接管白名单（算力有限按需开通）：双态 action，点击即切换（同 wl 列模式）
              r.alert_takeover_granted
                ? { text: "已开通·关", kind: "action" as const, onClickKey: "alert-revoke" }
                : { text: "未开通·开", kind: "action" as const, onClickKey: "alert-grant" },
              { text: fmtLocalTime(r.last_login_at) || "—" },
              r.whitelist_status === "active"
                ? { text: "移出", kind: "action" as const, onClickKey: "wl-revoke" }
                : { text: "加入", kind: "action" as const, onClickKey: "wl-add" },
              // 角色升/降（set-role 补链）：改自己会被后端 400 拦（防锁死），错误显示在动作横幅
              r.role === "platform_admin"
                ? { text: "撤销管理员", kind: "action" as const, onClickKey: "role-user" }
                : { text: "设为管理员", kind: "action" as const, onClickKey: "role-admin" },
              // 删除（软删 + 连带撤白）：删自己后端 400 拦（防锁死）；确认弹窗在 AdminConsole
              { text: "删除", kind: "action" as const, onClickKey: "user-delete" },
            ],
          };
        }),
        total: d.total, page: d.page, pageSize: d.page_size,
      };
    }
    if (key === "skills") {
      // Skill 基线（只读）：系统自带（platform）skill 清单 + §2.2 semver 版本。复用 /assets/skills（含 latest_version）。
      // platform 过滤走**服务端** source_type：分页后再客户端 filter 会让每页数量错乱、total 也不对。
      const d = await apiFetch<AssetPageDto>(
        `/openops/v1/assets/skills${assetQs({ ...params, sourceType: "platform" })}`);
      return {
        title: "Skill 基线",
        cols: [{ label: "名称" }, { label: "skill_key" }, { label: "版本" }, { label: "更新时间" }, { label: "状态", width: "88px" }],
        rows: d.items.map((r) => ({
          id: String(r.skill_id),
          cells: [
            { text: String(r.display_name) },
            { text: String(r.skill_key ?? ""), mono: true },
            { text: r.latest_version ? String(r.latest_version) : `v${r.version_no ?? 1}` },
            // SkillHub §2.2 updated_date（manifest 同步落库）；对账回填前为空 → 「—」
            { text: fmtLocal(r.updated_date) || "—" },
            { text: String(r.status), kind: "badge" as const, tone: r.status === "active" ? "good" as const : "neutral" as const },
          ],
        })),
        total: d.total, page: d.page, pageSize: d.page_size,
      };
    }
    if (key === "model-templates") {
      const rows = await realApi.adminListModelTemplates();
      return M.buildModelTemplateTable(rows);
    }
    if (key === "model-assets") {
      // 38.1：授权范围列与「白名单授权」入口已整体迁到「模型模板」页（授权在模板维度）
      const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/admin/model-assets");
      return {
        title: "模型资产",
        primary: { label: "注册模型接口", icon: "plus", actionKey: "register-model" },
        cols: [{ label: "模型名称" }, { label: "协议" }, { label: "model_id" }, { label: "归属" }, { label: "状态", width: "96px" }, { label: "删除", width: "56px" }],
        rows: rows.map((m) => ({
          id: String(m.model_asset_id),
          cells: [
            { text: String(m.display_name) },
            { text: m.protocol === "openai_compatible" ? "OpenAI 兼容" : String(m.protocol) },
            { text: String(m.model_id), mono: true },
            { text: m.registered_by === "system" ? "平台" : String(m.registered_by) },
            { text: String(m.status), kind: "badge" as const, tone: m.status === "active" ? "good" as const : "neutral" as const },
            { text: "删除", kind: "action" as const, onClickKey: "ma-delete" },
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
    // 全量取（本表无分页器，且下游「绑定/解绑」按全集算）+ platform 过滤走服务端 source_type。
    const platform = await fetchAllAssets("mcps", "platform");
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
    // 授权弹窗要**完整**用户集：翻完所有页（pageSize 上限 100 压页数），只取首页会静默漏人
    const out: { user_id: string; display_name: string }[] = [];
    for (let page = 1; ; page++) {
      const d = await apiFetch<AssetPageDto>(`/openops/v1/admin/users${assetQs({ page, pageSize: 100 })}`);
      out.push(...d.items.map((r) => ({ user_id: String(r.user_id), display_name: String(r.display_name ?? "") })));
      if (!d.items.length || page * d.page_size >= d.total) break;
    }
    return out;
  },
  async adminListUserTags() {
    return apiFetch<string[]>("/openops/v1/admin/users/tags");
  },
  async adminAddWhitelist(userId, displayName, tags) {
    await apiFetch("/openops/v1/admin/users/whitelist", {
      method: "POST",
      // tags 省略时不带键（后端 None=不动已有标签），与传 [] 清空语义区分
      body: { client_request_id: crid(), user_id: userId, display_name: displayName, ...(tags ? { tags } : {}) },
    });
  },
  async adminSetAlertGrant(userId, granted) {
    await apiFetch<void>(`/openops/v1/admin/alerts/grants:update`, {
      method: "POST",
      body: { client_request_id: crid(), user_id: userId, granted },
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
  async adminSetTags(userId, tags) {
    await apiFetch(`/openops/v1/admin/users/${encodeURIComponent(userId)}:set-tags`, {
      method: "POST",
      body: { client_request_id: crid(), tags },
    });
  },
  async adminDeleteUser(userId) {
    await apiFetch(`/openops/v1/admin/users/${encodeURIComponent(userId)}`, { method: "DELETE" });
  },
  async adminGetModelTemplateGrants(modelTemplateId) {
    const d = await apiFetch<{ access_scope: string; user_ids: string[] }>(
      `/openops/v1/admin/model-templates/${modelTemplateId}/grants`,
    );
    return { access_scope: d.access_scope, user_ids: d.user_ids };
  },
  async adminSaveModelTemplateGrants(modelTemplateId, accessScope, userIds) {
    await apiFetch(`/openops/v1/admin/model-templates/${modelTemplateId}/grants`, {
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
  // ---- admin 模型模板（38 号）----
  async adminListModelTemplates() {
    const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/admin/model-templates");
    return rows.map((r) => {
      const main = (r.main_model ?? {}) as Record<string, unknown>;
      const sub = (r.sub_model ?? {}) as Record<string, unknown>;
      return {
        model_template_id: String(r.model_template_id),
        display_name: String(r.display_name),
        description: r.description ? String(r.description) : "",
        main_model_asset_id: String(main.model_asset_id ?? ""),
        main_model_id: String(main.model_id ?? ""),
        main_model_name: String(main.display_name ?? ""),
        sub_model_asset_id: String(sub.model_asset_id ?? ""),
        sub_model_id: String(sub.model_id ?? ""),
        sub_model_name: String(sub.display_name ?? ""),
        access_scope: r.access_scope === "restricted" ? ("restricted" as const) : ("all" as const),
        grant_count: Number(r.grant_count ?? 0),
        is_default: r.is_default === true,
        status: r.status === "disabled" ? ("disabled" as const) : ("active" as const),
      };
    });
  },
  async adminListModelAssets() {
    const rows = await apiFetch<Record<string, unknown>[]>("/openops/v1/admin/model-assets");
    return rows.map((m) => ({
      model_asset_id: String(m.model_asset_id),
      model_id: String(m.model_id),
      display_name: String(m.display_name),
      status: String(m.status),
    }));
  },
  async adminCreateModelTemplate(input) {
    await apiFetch("/openops/v1/admin/model-templates", {
      method: "POST",
      body: { client_request_id: crid(), ...input },
    });
  },
  async adminUpdateModelTemplate(id, input) {
    await apiFetch(`/openops/v1/admin/model-templates/${id}`, {
      method: "PUT",
      body: { client_request_id: crid(), ...input },
    });
  },
  async adminSetModelTemplateStatus(id, status) {
    await apiFetch(`/openops/v1/admin/model-templates/${id}:status`, {
      method: "PUT",
      body: { client_request_id: crid(), status },
    });
  },
  async adminSetModelTemplateDefault(id) {
    await apiFetch(`/openops/v1/admin/model-templates/${id}:set-default`, {
      method: "POST",
      body: { client_request_id: crid() },
    });
  },
  async adminDeleteModelTemplate(id) {
    await apiFetch(`/openops/v1/admin/model-templates/${id}`, { method: "DELETE" });
  },
  async adminDeleteModelAsset(id) {
    await apiFetch(`/openops/v1/admin/model-assets/${id}`, { method: "DELETE" });
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
    return apiFetch<SandboxDestroyResult>(`/openops/v1/admin/sandbox/containers/${encodeURIComponent(userId)}:destroy`, {
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
  async getWorkspaceStatus(workspaceId) {
    const d = await apiFetch<Record<string, unknown>>(`/openops/v1/workspaces/${workspaceId}/status`);
    return {
      sync_status: String(d.sync_status ?? "ready"),
      scope_revision: String(d.scope_revision ?? ""),
      app_ids: Array.isArray(d.app_ids) ? d.app_ids.map(String) : [],
    };
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
  async adminCreateWorkspace(name, apps, reason) {
    // 同一创建端点：manual_app_ids 非空触发后端 reason 必填校验 + workspace.admin_created 审计
    const d = await apiFetch<Record<string, unknown>>("/openops/v1/workspaces", {
      method: "POST",
      body: {
        client_request_id: crid(), name, app_ids: apps.map((a) => a.app_id), apps,
        manual_app_ids: apps.map((a) => a.app_id), reason,
      },
    });
    return { workspace_id: String(d.workspace_id) };
  },
  async getWorkspace(workspaceId) {
    const d = await apiFetch<Record<string, unknown>>(`/openops/v1/workspaces/${workspaceId}`);
    return {
      workspace_id: String(d.workspace_id),
      name: String(d.name),
      app_ids: ((d.app_ids as string[]) ?? []).map(String),
      scope_revision: String(d.scope_revision ?? ""),
      sync_status: (d.sync_status as Workspace["sync_status"]) ?? "ready",
    };
  },
  async updateWorkspace(workspaceId, name, apps) {
    const d = await apiFetch<Record<string, unknown>>(`/openops/v1/workspaces/${workspaceId}`, {
      method: "PUT",
      body: { client_request_id: crid(), name, app_ids: apps.map((a) => a.app_id), apps },
    });
    return { workspace_id: String(d.workspace_id ?? workspaceId) };
  },
  async deleteWorkspace(workspaceId) {
    await apiFetch(`/openops/v1/workspaces/${workspaceId}`, { method: "DELETE" });
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
              user_llm_config_id: input.user_llm_config_id, platform_model_id: input.platform_model_id,
              model_template_id: input.model_template_id },
    });
  },

  demoState: () => M.mockWorkbenchState(),
};

/* -------------------------------- mock -------------------------------- */
// 编辑向导 mock 态：per-instance overlay（updateAgentTeam 写入，getAgentTeam 读回，支撑再编辑预填）
const mockOverlays = new Map<string, Record<string, unknown>>();
// 模板编辑器 mock：saveTemplateDraft 写入的草稿内容，getAdminTemplateDetail 读回（e2e 断言保存后 payload）
let mockTemplateDraft: Record<string, unknown> | null = null;

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
  getWorkspaceStatistics: () => delay({ node_count: 3116, relation_count: 2246, node_type_count: 37, relation_type_count: 5 }),
  // mock 无 IAM，登出为空操作（signout/redirect 均 null → 前端走 reload 分支）
  logout: () => delay({ signout_url: null, login_url: null, csrf_cookie_name: null, csrf_header_name: null, redirect_url: null }),
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
  startTask: () => delay({ task_id: "tsk_demo", status: "running" }),
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
  getSkillLibrary: (p) => delay({
    items: M.mockSkillLibrary, total: M.mockSkillLibrary.length,
    page: p?.page ?? 1, pageSize: p?.pageSize ?? 20,
  }),
  getMcpLibrary: (p) => delay({
    items: M.mockMcpLibrary, total: M.mockMcpLibrary.length,
    page: p?.page ?? 1, pageSize: p?.pageSize ?? 20,
  }),
  getAllSkills: () => delay(M.mockSkillLibrary),
  getAllMcps: () => delay(M.mockMcpLibrary),
  uploadSkill: (file) => {
    // 29.9 形态对齐后端 mock：skill_key = 命名空间化 id（user-{uid}-{原始名}），display_name = 原始名
    const raw = file.name.replace(/\.zip$/i, "").toLowerCase();
    return delay({ skill_key: `user-0026demo01-${raw}`, display_name: raw, action: "created" });
  },
  registerMcp: () => delay(undefined as unknown as void),
  deleteAsset: () => delay(undefined as unknown as void).then(() => invalidateAvailableSkills()),
  bindAsset: (instanceId) => delay(undefined as unknown as void).then(() => invalidateAvailableSkills(instanceId)),
  unbindAsset: () => delay(undefined as unknown as void).then(() => invalidateAvailableSkills()),
  unbindOwnAsset: (_kind, instanceId) => delay(undefined as unknown as void).then(() => invalidateAvailableSkills(instanceId)),
  getMainAppend: () => delay("优先关注支付链路核心接口的 P99 与错误率。"),
  saveMainAppend: () => delay(undefined as unknown as void),
  reconcileAssets: () => delay({ skipped: true }),
  getConfigVersions: () => delay(M.mockConfigVersions),
  getModelConfigs: () => delay(M.mockModels),
  // 用户侧模板清单：active 行 ∅ 过滤交给消费方；含 disabled 行供「已绑定但被停用」的兜底展示演示
  getModelTemplates: () => delay(M.mockModelTemplates.map(M.toUserModelTemplate)),
  createSecret: () => delay({ secret_ref_id: "sec_mock", fingerprint: "sk-…mock" }),
  createLlmConfig: () => delay({ llm_config_id: "llm_mock_" + Math.random().toString(36).slice(2, 8) }),
  // mock「测试连接」：镜像后端 mock 探测（model 含 no-tool → 失败），供离线 UI 验证保存门。
  // probe_mode: "mock" 让 UI 如实显示「未真实探测」——离线态同样不许冒充真探测。
  testLlmConnection: (input) => delay(
    input.model_name.toLowerCase().includes("no-tool")
      ? { ok: false, supports_tool_calling: false, reason: "模型不支持 tool calling", probe_mode: "mock" as const }
      : { ok: true, supports_tool_calling: true, reason: null, probe_mode: "mock" as const },
  ),
  testModelAssetConnection: () => delay({ ok: true, supports_tool_calling: true, reason: null, probe_mode: "mock" as const }),
  getAdminTable: (key) =>
    key === "model-templates"
      ? delay(M.buildModelTemplateTable([...M.mockModelTemplates]))  // 从可变数组现算：创建/启停后重拉即反映
      : delay(M.adminTables[key] ?? M.adminTables.templates),
  getSandboxCfg: () => delay(M.sandboxCfg),
  saveSandboxCfg: () => delay(undefined as unknown as void),
  getSandboxContainers: () => delay([] as SandboxContainer[]),
  destroySandboxContainer: (userId) =>
    delay({ destroyed: true, target_user_id: userId, cancelled_task_ids: [] }),
  getAuditTimeline: () => delay(M.auditTimeline),
  getAdminTemplateDetail: () => delay({
    ...M.mockTemplateDetail,
    draft_version: mockTemplateDraft
      ? { template_version_id: "tv_draft", version_no: 3, status: "draft", content_json: mockTemplateDraft }
      : null,
  }),
  saveTemplateDraft: (_id, content) => {
    mockTemplateDraft = content;  // 回存供再打开断言（镜像后端草稿 upsert）
    return delay({ template_version_id: "tv_draft", version_no: 3, status: "draft",
                   content_json: content, dropped_tools: [] });
  },
  publishTemplateVersion: () => delay(undefined as unknown as void),
  disableTemplateVersion: () => delay(undefined as unknown as void),
  adminAddWhitelist: () => delay(undefined as unknown as void),
  adminSetTags: () => delay(undefined as unknown as void),
  adminRevokeWhitelist: () => delay(undefined as unknown as void),
  adminSetAlertGrant: () => delay(undefined as unknown as void),
  adminSetRole: () => delay(undefined as unknown as void),
  adminDeleteUser: () => delay(undefined as unknown as void),
  getAuditTrace: () => delay(M.auditTimeline),
  getAdminTemplateAssets: () => delay({
    title: "资产治理",
    cols: [{ label: "名称" }, { label: "类型" }, { label: "最新版本" }, { label: "状态" }, { label: "操作", width: "96px" }],
    // id = mcp_display_name（与 raw 的分组键、绑定列判定一致）
    rows: [...new Set(M.adminMcpToolsRaw.map((r) => String(r.mcp_display_name)))].map((name) => ({
      id: name,
      cells: [
        { text: name }, { text: "HTTP MCP" }, { text: "v1" },
        { text: "active", kind: "badge" as const, tone: "good" as const },
        { text: "Tool 标注", kind: "action" as const, onClickKey: "open-mcp" },
      ],
    })),
  }),
  getAdminMcpTools: (mcpName) => {
    const rows = mcpName ? M.adminMcpToolsRaw.filter((r) => String(r.mcp_display_name) === mcpName) : M.adminMcpToolsRaw;
    return delay({
      title: "Tool 标注",
      cols: [{ label: "tool_name" }, { label: "所属 MCP" }, { label: "标注状态" }, { label: "操作", width: "88px" }],
      raw: rows,
      rows: rows.map((r) => ({
        id: String(r.tool_catalog_id),
        cells: [
          { text: String(r.tool_name), mono: true },
          { text: String(r.mcp_display_name ?? "") },
          r.annotation_id == null
            ? { text: "未标注 → 运行时 block", kind: "badge" as const, tone: "danger" as const }
            : { text: r.is_approval_required ? "allowed · 需审批" : "allowed", kind: "badge" as const, tone: r.is_approval_required ? "warning" as const : "good" as const },
          { text: r.annotation_id != null ? "编辑标注" : "标注", kind: "action" as const, onClickKey: "annotate" },
        ],
      })),
    });
  },
  adminSaveAnnotation: () => delay(undefined as unknown as void),
  adminListUsers: () => delay([{ user_id: "0026demo01", display_name: "林一" }]),
  adminListUserTags: () => delay(["财经", "研发", "供应"]),
  // 模板白名单授权 mock（38.1 写闭环）：scope 读写 mockModelTemplates、名单读写 mockModelTemplateGrants
  adminGetModelTemplateGrants: (id) => {
    const t = M.mockModelTemplates.find((x) => x.model_template_id === id);
    return delay({ access_scope: t?.access_scope ?? "all", user_ids: [...(M.mockModelTemplateGrants[id] ?? [])] });
  },
  adminSaveModelTemplateGrants: (id, accessScope, userIds) => {
    const t = M.mockModelTemplates.find((x) => x.model_template_id === id);
    const ids = accessScope === "all" ? [] : [...new Set(userIds)];
    if (t) {
      t.access_scope = accessScope as "all" | "restricted";
      t.grant_count = ids.length;
    }
    M.mockModelTemplateGrants[id] = ids;
    return delay(undefined as unknown as void);
  },
  adminRegisterModel: () => delay(undefined as unknown as void),
  // ---- 模型模板（38 号）mock 写闭环：原地改可变数组（先例：createWorkspace/renameRun）----
  adminListModelTemplates: () => delay([...M.mockModelTemplates]),
  adminListModelAssets: () => delay([...M.mockModelAssets]),
  adminCreateModelTemplate: (input) => {
    const find = (id: string) => M.mockModelAssets.find((a) => a.model_asset_id === id);
    const main = find(input.main_model_asset_id);
    const sub = find(input.sub_model_asset_id);
    if (input.is_default) M.mockModelTemplates.forEach((t) => { t.is_default = false; });
    M.mockModelTemplates.push({
      model_template_id: "mtpl_mock_" + Math.random().toString(36).slice(2, 8),
      display_name: input.display_name,
      description: input.description ?? "",
      main_model_asset_id: input.main_model_asset_id,
      main_model_id: main?.model_id ?? "", main_model_name: main?.display_name ?? "",
      sub_model_asset_id: input.sub_model_asset_id,
      sub_model_id: sub?.model_id ?? "", sub_model_name: sub?.display_name ?? "",
      access_scope: input.access_scope ?? "all", grant_count: 0,
      is_default: Boolean(input.is_default), status: "active",
    });
    return delay(undefined as unknown as void);
  },
  adminUpdateModelTemplate: (id, input) => {
    const t = M.mockModelTemplates.find((x) => x.model_template_id === id);
    if (t) {
      if (input.display_name !== undefined) t.display_name = input.display_name;
      if (input.description !== undefined) t.description = input.description;
      const find = (aid: string) => M.mockModelAssets.find((a) => a.model_asset_id === aid);
      if (input.main_model_asset_id) {
        const m = find(input.main_model_asset_id);
        t.main_model_asset_id = input.main_model_asset_id;
        t.main_model_id = m?.model_id ?? ""; t.main_model_name = m?.display_name ?? "";
      }
      if (input.sub_model_asset_id) {
        const s = find(input.sub_model_asset_id);
        t.sub_model_asset_id = input.sub_model_asset_id;
        t.sub_model_id = s?.model_id ?? ""; t.sub_model_name = s?.display_name ?? "";
      }
    }
    return delay(undefined as unknown as void);
  },
  adminSetModelTemplateStatus: (id, status) => {
    const t = M.mockModelTemplates.find((x) => x.model_template_id === id);
    if (t) t.status = status;
    return delay(undefined as unknown as void);
  },
  adminSetModelTemplateDefault: (id) => {
    M.mockModelTemplates.forEach((t) => { t.is_default = t.model_template_id === id; });
    return delay(undefined as unknown as void);
  },
  adminDeleteModelTemplate: (id) => {
    const i = M.mockModelTemplates.findIndex((t) => t.model_template_id === id);
    if (i >= 0) M.mockModelTemplates.splice(i, 1);
    delete M.mockModelTemplateGrants[id];
    return delay(undefined as unknown as void);
  },
  adminDeleteModelAsset: (id) => {
    // 镜像后端 fail-closed：被 mock 模板槽位引用则拒删
    const refs = M.mockModelTemplates.filter((t) => t.main_model_asset_id === id || t.sub_model_asset_id === id);
    if (refs.length) {
      return Promise.reject(new Error(
        `该模型被模型模板引用（${refs.map((t) => t.display_name).join("、")}），请先在模板中更换主/子槽位或删除对应模板`));
    }
    const i = M.mockModelAssets.findIndex((a) => a.model_asset_id === id);
    if (i >= 0) M.mockModelAssets.splice(i, 1);
    const r = M.adminTables["model-assets"].rows.findIndex((row) => row.id === id);
    if (r >= 0) M.adminTables["model-assets"].rows.splice(r, 1);
    return delay(undefined as unknown as void);
  },
  getTemplates: () => delay(M.mockTemplates),
  getWorkspaces: () => delay(M.mockWorkspaces),
  getWorkspaceStatus: (workspaceId) => {
    const w = M.mockWorkspaces.find((x) => x.workspace_id === workspaceId) ?? M.mockWorkspaces[0];
    return delay({ sync_status: w?.sync_status ?? "ready", scope_revision: w?.scope_revision ?? "", app_ids: ["APP-A", "APP-B", "APP-C"] });
  },
  getScopeApps: () => delay(M.mockScopeApps),
  createWorkspace: (name, apps) => {
    // 有状态：push 到 mockWorkspaces + 记应用（否则创建后卡片选中态显示「已选：—」；真实语义也应落库）
    const id = "ws_mock_" + Math.random().toString(36).slice(2, 8);
    M.mockWorkspaces.push({ workspace_id: id, name, scope_revision: "rev-" + Math.random().toString(36).slice(2, 8), sync_status: "ready", updated: "" });
    M.mockWorkspaceApps[id] = apps.map((a) => a.app_id);
    return delay({ workspace_id: id });
  },
  adminCreateWorkspace: (name, apps) => mockApi.createWorkspace(name, apps), // 演示模式：reason 不落 mock

  getWorkspace: (workspaceId) => {
    const ws = M.mockWorkspaces.find((w) => w.workspace_id === workspaceId);
    if (!ws) return Promise.reject(new Error("workspace 不存在"));
    return delay({
      workspace_id: ws.workspace_id, name: ws.name,
      app_ids: M.mockWorkspaceApps[workspaceId] ?? [],
      scope_revision: ws.scope_revision, sync_status: ws.sync_status,
    });
  },
  updateWorkspace: (workspaceId, name, apps) => {
    const ws = M.mockWorkspaces.find((w) => w.workspace_id === workspaceId);
    if (ws) { ws.name = name; ws.scope_revision = "rev-" + Math.random().toString(36).slice(2, 8); }
    M.mockWorkspaceApps[workspaceId] = apps.map((a) => a.app_id);
    return delay({ workspace_id: workspaceId });
  },
  deleteWorkspace: (workspaceId) => {
    const i = M.mockWorkspaces.findIndex((w) => w.workspace_id === workspaceId);
    if (i >= 0) M.mockWorkspaces.splice(i, 1);
    delete M.mockWorkspaceApps[workspaceId];
    return delay(undefined as unknown as void);
  },
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
      const mtpl = input.model_template_id
        ? M.mockModelTemplates.find((t) => t.model_template_id === input.model_template_id) : undefined;
      ag.model = input.user_llm_config_id ? "自定义模型"
        : mtpl ? mtpl.display_name
        : input.platform_model_id || "平台默认模型";
    }
    // 互斥写入优先级与后端 _update_body 同序：BYO > 模板 > legacy > 全空
    mockOverlays.set(instanceId,
      input.user_llm_config_id ? { user_llm_config_id: input.user_llm_config_id }
      : input.model_template_id ? { model_template_id: input.model_template_id }
      : input.platform_model_id ? { platform_model_id: input.platform_model_id }
      : {});
    return delay(undefined as unknown as void);
  },
  demoState: () => M.mockWorkbenchState(),
};

export const api: OpenOpsApi = API_MODE === "real" ? realApi : mockApi;
export { demoIdentity };
