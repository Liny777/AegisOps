/** 7x24 告警接管切片的 API 客户端（与 core 的 lib/api 门面平级但独立）。
 *
 * 只依赖 core 传输层的公开物：`lib/api/client` 的 `apiFetch` + `API_MODE` + `crid`
 * （envelope 解析 / 401→IAM 跳转 / mock 身份头都由它统一处理）。切片新增端点只改本文件，
 * 不动 core 那个大接口。
 *
 * ⚠ `realAlertsApi` / `mockAlertsApi` 都必须显式标注 `: AlertsApi`——这是 mock/real
 *   方法集不漂移的**唯一编译期保障**（studio/api.ts 与 core 的 realApi/mockApi 同做法）。
 */
import { API_MODE, apiFetch, crid } from "../lib/api/client";
import * as M from "./mockData";
import type {
  AlertEventRow,
  AlertEventsPage,
  AlertHistoryPreviewPage,
  AlertEventStatus,
  AlertEventWire,
  AlertIncidentDetail,
  AlertIncidentsPage,
  AlertRuleBatchAction,
  AlertRulePatch,
  AlertRulesConfig,
  AlertRuleTemplatesPayload,
  AlertSeverity,
  AlertSummary,
  AlertTakeoverStatus,
  EnsureRuleInput,
  EnsureRuleOutcome,
  EnsureRuleResult,
  IncidentStatus,
  NewRuleInput,
  UserFeedback,
} from "./types";

export interface ListIncidentsParams {
  instanceId?: string;
  status?: IncidentStatus[];
  severity?: AlertSeverity[];
  search?: string;
  page?: number;
  pageSize?: number;
  signal?: AbortSignal;
}

/** 事件清单筛选（四个下拉都是单值；空/undefined = 全部）。 */
export interface ListEventsParams {
  instanceId?: string;
  alertStatus?: AlertEventStatus;
  /** 单值或多值（多值 CSV 下沉服务端）；配置弹窗预览按已勾级别组传数组。 */
  severity?: AlertSeverity | AlertSeverity[];
  takeover?: AlertTakeoverStatus;
  category?: string;
  search?: string;
  /** 只看最近 N 天（配置弹窗「最近 3 天同类告警预览」用；清单默认不限）。 */
  sinceDays?: number;
  page?: number;
  pageSize?: number;
  signal?: AbortSignal;
}

export interface AlertsApi {
  /** 告警事件清单（事件视角主表；服务端分页/筛选，mock 档本地完成同名筛选）。 */
  listEvents(params?: ListEventsParams): Promise<AlertEventsPage>;
  /** incident 系列端点保留（页面暂不消费，契约不删）。 */
  listIncidents(params?: ListIncidentsParams): Promise<AlertIncidentsPage>;
  getIncident(id: string, signal?: AbortSignal): Promise<AlertIncidentDetail>;
  /** 忽略（仅 queued）/ 重试（仅 failed/skipped）：状态机由后端把关。 */
  ignoreIncident(id: string): Promise<void>;
  retryIncident(id: string): Promise<void>;
  feedbackIncident(id: string, body: { feedback: UserFeedback; note: string }): Promise<void>;
  getSummary(instanceId: string): Promise<AlertSummary>;
  /** 规则清单（订阅总开关已下线 2026-08-15：规则启停是唯一开关面）。 */
  getRules(instanceId: string): Promise<AlertRulesConfig>;
  /** 单规则创建（v2：编辑器一次建一条，策略勾选清单在 strategies 里）。 */
  createRule(instanceId: string, input: NewRuleInput): Promise<void>;
  /** 幂等建规（告警直达链）：已覆盖零改动 / 级别并进既有规则 / 否则新建（rules:ensure）。 */
  ensureRule(instanceId: string, input: EnsureRuleInput, signal?: AbortSignal): Promise<EnsureRuleResult>;
  /** 批量启用/禁用/删除（rules:batch）。 */
  batchRules(ruleIds: string[], action: AlertRuleBatchAction): Promise<void>;
  updateRule(ruleId: string, patch: AlertRulePatch): Promise<void>;
  deleteRule(ruleId: string): Promise<void>;
  /** 启停开关走 :update 只传 {enabled}（独立方法是为了调用点语义清晰）。 */
  toggleRule(ruleId: string, enabled: boolean): Promise<void>;
  /** 模板 payload：templates + 可选级别 + 默认提示词。 */
  getRuleTemplates(): Promise<AlertRuleTemplatesPayload>;
  /** 功能开通探询（白名单闸）：false 时设置页/清单页渲染「未开通」引导空态。 */
  getAccess(signal?: AbortSignal): Promise<{ granted: boolean }>;
  /** 规则编辑器第二步预览：平台历史接口主路径（真全量历史），失败降级本地库（source 区分）。 */
  historyPreview(params: {
    instanceId: string;
    categories: string[];
    severity: AlertSeverity[];
    sinceDays: number;
    pageSize?: number;
    signal?: AbortSignal;
  }): Promise<AlertHistoryPreviewPage>;
}

/** duration_s(int) → "N秒/N分"（<60s 秒、否则四舍五入到分；展示语义归前端投影层）。 */
const durationText = (s: unknown): string | null =>
  typeof s === "number" ? (s < 60 ? `${s}秒` : `${Math.round(s / 60)}分`) : null;

/** 后端行 → 前端 AlertIncident 展示形态的补投影：duration_s → duration 文案；
 *  instance_name/fingerprint 清单行可能缺省（详情才带 fingerprint）→ 补空串保类型完整。 */
function projectIncident<T extends { duration?: string | null }>(raw: Record<string, unknown>): T {
  return {
    ...(raw as object),
    duration: durationText(raw["duration_s"]),
    instance_name: (raw["instance_name"] as string) ?? "",
    fingerprint: (raw["fingerprint"] as string) ?? "",
  } as unknown as T;
}

/** 事件行投影：wire 形态（duration_s）→ 展示形态（补 duration 文案）。real/mock 共用。 */
const projectEvent = (raw: AlertEventWire): AlertEventRow => ({
  ...raw,
  duration: durationText(raw.duration_s),
});

interface AlertEventsWirePage {
  items: AlertEventWire[];
  total: number;
  page: number;
  page_size: number;
}

/** rules:ensure 的 wire 形态：outcome 按开放字符串收（灰度期新旧后端可能多出档位）。 */
type EnsureRuleWire = Omit<EnsureRuleResult, "outcome" | "merge_detail"> &
  { outcome: string; merge_detail?: EnsureRuleResult["merge_detail"] };

const ENSURE_OUTCOMES: readonly EnsureRuleOutcome[] = ["created", "merged", "already_covered"];

/** ensure 响应收窄单点：未知 outcome 一律按 created 展示兜底（规则本体照常可用，不炸 UI）。 */
const projectEnsureResult = (raw: EnsureRuleWire): EnsureRuleResult => ({
  ...raw,
  outcome: (ENSURE_OUTCOMES as readonly string[]).includes(raw.outcome)
    ? (raw.outcome as EnsureRuleOutcome) : "created",
  merge_detail: raw.merge_detail ?? null,
});

const eventsQuery = (params?: ListEventsParams): URLSearchParams => {
  const s = new URLSearchParams();
  if (params?.instanceId) s.set("instance_id", params.instanceId);
  if (params?.alertStatus) s.set("alert_status", params.alertStatus);
  if (params?.severity) s.set("severity", Array.isArray(params.severity) ? params.severity.join(",") : params.severity);
  if (params?.takeover) s.set("takeover", params.takeover);
  if (params?.category) s.set("category", params.category);
  if (params?.search) s.set("search", params.search);
  if (params?.sinceDays) s.set("since_days", String(params.sinceDays));
  s.set("page", String(params?.page ?? 1));
  s.set("page_size", String(params?.pageSize ?? 20));
  return s;
};

const realAlertsApi: AlertsApi = {
  async listEvents(params) {
    const page = await apiFetch<AlertEventsWirePage>(
      `/openops/v1/alerts/events?${eventsQuery(params).toString()}`, { signal: params?.signal });
    return { ...page, items: page.items.map(projectEvent) };
  },
  async listIncidents(params) {
    const s = new URLSearchParams();
    if (params?.instanceId) s.set("instance_id", params.instanceId);
    if (params?.status?.length) s.set("state", params.status.join(","));
    if (params?.severity?.length) s.set("severity", params.severity.join(","));
    if (params?.search) s.set("search", params.search);
    s.set("page", String(params?.page ?? 1));
    s.set("page_size", String(params?.pageSize ?? 20));
    const page = await apiFetch<AlertIncidentsPage>(
      `/openops/v1/alerts/incidents?${s.toString()}`, { signal: params?.signal });
    return { ...page, items: page.items.map((it) => projectIncident(it as unknown as Record<string, unknown>)) };
  },
  async getIncident(id, signal) {
    const raw = await apiFetch<AlertIncidentDetail>(
      `/openops/v1/alerts/incidents/${encodeURIComponent(id)}`, { signal });
    return projectIncident<AlertIncidentDetail>(raw as unknown as Record<string, unknown>);
  },
  async ignoreIncident(id) {
    await apiFetch<void>(`/openops/v1/alerts/incidents/${encodeURIComponent(id)}:ignore`, {
      method: "POST",
      body: { client_request_id: crid() },
    });
    invalidateAlerts();
  },
  async retryIncident(id) {
    await apiFetch<void>(`/openops/v1/alerts/incidents/${encodeURIComponent(id)}:retry`, {
      method: "POST",
      body: { client_request_id: crid() },
    });
    invalidateAlerts();
  },
  async feedbackIncident(id, body) {
    await apiFetch<void>(`/openops/v1/alerts/incidents/${encodeURIComponent(id)}:feedback`, {
      method: "POST",
      body: { client_request_id: crid(), feedback: body.feedback, note: body.note },
    });
    invalidateAlerts();
  },
  async getSummary(instanceId) {
    const s = new URLSearchParams({ instance_id: instanceId });
    return apiFetch<AlertSummary>(`/openops/v1/alerts/summary?${s.toString()}`);
  },
  async getRules(instanceId) {
    const s = new URLSearchParams({ instance_id: instanceId });
    const envelope = await apiFetch<{ rules: AlertRulesConfig["rules"] }>(
      `/openops/v1/alerts/rules?${s.toString()}`);
    return { rules: envelope.rules };
  },
  async createRule(instanceId, input) {
    await apiFetch<{ rule: unknown }>(`/openops/v1/alerts/rules`, {
      method: "POST",
      body: {
        client_request_id: crid(),
        agent_team_instance_id: instanceId,
        name: input.name,
        categories: input.categories,
        severities: input.severities,
        strategies: input.strategies ?? [],  // 空=该类型全部策略（后端 matcher 空数组短路）
        owner_only: input.owner_only ?? false,
        prompt: input.prompt,
        enabled: input.enabled ?? true,
      },
    });
    invalidateAlerts(instanceId);
  },
  async ensureRule(instanceId, input, signal) {
    const raw = await apiFetch<EnsureRuleWire>(`/openops/v1/alerts/rules:ensure`, {
      method: "POST",
      signal,
      body: {
        client_request_id: crid(),
        agent_team_instance_id: instanceId,
        name: input.name,
        categories: input.categories,
        severities: input.severities,
        prompt: input.prompt,
      },
    });
    invalidateAlerts(instanceId);
    return projectEnsureResult(raw);
  },
  async batchRules(ruleIds, action) {
    await apiFetch<{ updated: number }>(`/openops/v1/alerts/rules:batch`, {
      method: "POST",
      body: { client_request_id: crid(), rule_ids: ruleIds, action },
    });
    invalidateAlerts();
  },
  async updateRule(ruleId, patch) {
    await apiFetch<void>(`/openops/v1/alerts/rules/${encodeURIComponent(ruleId)}:update`, {
      method: "POST",
      body: { client_request_id: crid(), ...patch },
    });
    invalidateAlerts();
  },
  async deleteRule(ruleId) {
    await apiFetch<void>(`/openops/v1/alerts/rules/${encodeURIComponent(ruleId)}:delete`, {
      method: "POST",
      body: { client_request_id: crid() },
    });
    invalidateAlerts();
  },
  async toggleRule(ruleId, enabled) {
    await apiFetch<void>(`/openops/v1/alerts/rules/${encodeURIComponent(ruleId)}:update`, {
      method: "POST",
      body: { client_request_id: crid(), enabled },
    });
    invalidateAlerts();
  },
  async getRuleTemplates() {
    return apiFetch<AlertRuleTemplatesPayload>(`/openops/v1/alerts/rule-templates`);
  },
  async getAccess(signal) {
    return apiFetch<{ granted: boolean }>(`/openops/v1/alerts/access`, { signal });
  },
  async historyPreview(params) {
    const s = new URLSearchParams({
      instance_id: params.instanceId,
      categories: params.categories.join(","),
      since_days: String(params.sinceDays),
      page_size: String(params.pageSize ?? 20),
    });
    if (params.severity.length) s.set("severity", params.severity.join(","));
    const page = await apiFetch<Omit<AlertHistoryPreviewPage, "items"> & { items: AlertEventWire[] }>(
      `/openops/v1/alerts/history-preview?${s.toString()}`, { signal: params.signal });
    return { ...page, items: page.items.map(projectEvent) };
  },
};

const delay = <T,>(v: T, ms = 120): Promise<T> => new Promise((r) => setTimeout(() => r(v), ms));

const mockEventsPage = (params?: ListEventsParams): AlertEventsPage => {
  const page = M.mockListEvents({
    instanceId: params?.instanceId,
    alertStatus: params?.alertStatus,
    severity: params?.severity,
    takeover: params?.takeover,
    category: params?.category,
    search: params?.search,
    sinceDays: params?.sinceDays,  // 此前漏传：规则弹窗预览的时间窗在 mock 档从未生效
    page: params?.page,
    pageSize: params?.pageSize,
  });
  return { ...page, items: page.items.map(projectEvent) };
};

const mockAlertsApi: AlertsApi = {
  listEvents: (params) => delay(undefined).then(() => mockEventsPage(params)),
  listIncidents: (params) =>
    delay(M.mockListIncidents({
      instanceId: params?.instanceId,
      status: params?.status,
      severity: params?.severity,
      search: params?.search,
      page: params?.page,
      pageSize: params?.pageSize,
    })),
  // 读/写都把 M.* 放进 then 里**延迟求值**：mock 状态机守卫会 throw（如「仅排队中的告警
  // 可忽略」），必须变成 rejected Promise 走调用方 .catch，不能同步抛穿 UI。
  getIncident: (id) => delay(undefined).then(() => M.mockGetIncident(id)),
  // mock 写操作真实修改内存数组后广播失效——清单/规则页跟着重拉，状态变化肉眼可见
  ignoreIncident: (id) => delay(undefined).then(() => { M.mockIgnoreIncident(id); invalidateAlerts(); }),
  retryIncident: (id) => delay(undefined).then(() => { M.mockRetryIncident(id); invalidateAlerts(); }),
  feedbackIncident: (id, body) =>
    delay(undefined).then(() => { M.mockFeedbackIncident(id, body.feedback, body.note); invalidateAlerts(); }),
  getSummary: (instanceId) => delay(M.mockGetSummary(instanceId)),
  getRules: (instanceId) => delay(M.mockGetRules(instanceId)),
  createRule: (instanceId, input) =>
    delay(undefined).then(() => { M.mockCreateRule(instanceId, input); invalidateAlerts(instanceId); }),
  // 未开通（alertGranted='0'）时 mockEnsureRule 同步 throw → 延迟求值转 rejected Promise
  ensureRule: (instanceId, input) =>
    delay(undefined).then(() => {
      const result = M.mockEnsureRule(instanceId, input);
      invalidateAlerts(instanceId);
      return result;
    }),
  batchRules: (ruleIds, action) =>
    delay(undefined).then(() => { M.mockBatchRules(ruleIds, action); invalidateAlerts(); }),
  updateRule: (ruleId, patch) =>
    delay(undefined).then(() => { M.mockUpdateRule(ruleId, patch); invalidateAlerts(); }),
  deleteRule: (ruleId) =>
    delay(undefined).then(() => { M.mockDeleteRule(ruleId); invalidateAlerts(); }),
  toggleRule: (ruleId, enabled) =>
    delay(undefined).then(() => { M.mockUpdateRule(ruleId, { enabled }); invalidateAlerts(); }),
  getRuleTemplates: () => delay(M.mockGetRuleTemplates()),
  // mock 缝：localStorage['openops.mock.alertGranted']='0' 可演示未开通态（默认开通）
  getAccess: () => delay({ granted: localStorage.getItem("openops.mock.alertGranted") !== "0" }),
  historyPreview: (params) => delay(undefined).then(() => {
    const page = M.mockHistoryPreview(params);
    return { ...page, items: page.items.map(projectEvent) };
  }),
};

export const alertsApi: AlertsApi = API_MODE === "real" ? realAlertsApi : mockAlertsApi;

/* ------------------- 失效广播（照 lib/api/index.ts 的 conversationHistory 样板） ------------------- */
const alertListeners = new Set<(instanceId?: string) => void>();

/** 告警数据失效通知；写操作成功后自动调用，清单/规则页订阅后无需自行跟踪写入点。 */
export function invalidateAlerts(instanceId?: string): void {
  for (const listener of alertListeners) {
    try {
      listener(instanceId);
    } catch (error) {
      console.warn("[OpenOps] 告警失效订阅执行失败：", error);
    }
  }
}

export function subscribeAlerts(listener: (instanceId?: string) => void): () => void {
  alertListeners.add(listener);
  return () => {
    alertListeners.delete(listener);
  };
}
