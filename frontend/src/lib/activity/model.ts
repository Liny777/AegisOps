/**
 * 多 Agent 活动统一投影器。
 *
 * 输入可以来自 /state、审计分页、AG-UI CUSTOM 或备用 SSE；所有通道先归一化，
 * 再按 event_id 去重。轮次只认 dispatch_batch_id，轨迹只认 delegation_id；旧数据
 * 缺少批次时进入“历史派发”，不会恢复老项目的时间窗口猜测。
 */
import type {
  ActivityEvent,
  ActivityEventSource,
  ActivityEventsPage,
  ActivityStoreState,
  Delegation,
  DelegationStatus,
  DelegationTrack,
  DispatchRound,
  DispatchRoundStatus,
  OpenOpsEvent,
  RailModel,
} from "../api/types";

type UnknownRow = Record<string, unknown>;
export type ActivityEventInput = UnknownRow | OpenOpsEvent | ActivityEvent;
export type DelegationInput = UnknownRow | Delegation;

const row = (value: unknown): UnknownRow =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as UnknownRow : {};

const stringValue = (...values: unknown[]): string | undefined => {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return undefined;
};

const numberValue = (...values: unknown[]): number | undefined => {
  for (const value of values) {
    const parsed = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
};

const booleanValue = (value: unknown): boolean =>
  value === true || value === "true" || value === 1 || value === "1";

function hashText(text: string): string {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

function unwrapEvent(input: ActivityEventInput): UnknownRow {
  const outer = row(input);
  if (outer.type === "CUSTOM" && row(outer.value).event_type) return row(outer.value);
  return outer;
}

/** thinking、reasoning 与文本 token 只属于聊天/模型内部流，不进入活动栏。 */
export function isActivityEventVisible(eventType: string, payload: Readonly<UnknownRow> = {}): boolean {
  const type = eventType.toLowerCase();
  const kind = String(payload.kind ?? payload.event_kind ?? "").toLowerCase();
  if (["thinking", "reasoning", "text"].includes(kind)) return false;
  return !(
    /(^|[._-])(thinking|reasoning)([._-]|$)/.test(type)
    || /(^|[._-])text[_-]?message([._-]|$)/.test(type)
    || /(^|[._-])(text|message)[._-](delta|content)([._-]|$)/.test(type)
  );
}

/** 单条 state/audit/live 记录 → 统一事件；缺 event_id 的旧行使用确定性兼容 ID。 */
export function normalizeActivityEvent(
  input: ActivityEventInput,
  source: ActivityEventSource,
): ActivityEvent | undefined {
  if ("eventId" in input && "redactedPayload" in input) {
    const event = input as ActivityEvent;
    if (!isActivityEventVisible(event.eventType, event.redactedPayload)) return undefined;
    return { ...event, sources: Array.from(new Set([...event.sources, source])) };
  }

  const value = unwrapEvent(input);
  const payload = { ...row(value.payload_redacted_json ?? value.redactedPayload) };
  const eventType = stringValue(value.event_type, value.eventType, value.name) ?? "unknown";
  if (!isActivityEventVisible(eventType, payload)) return undefined;

  const runId = stringValue(value.agent_run_id, value.run_id, value.runId);
  const taskId = stringValue(value.task_id, value.taskId);
  const occurredAt = stringValue(value.occurred_at, value.occurredAt, value.creation_date, value.created_at) ?? "";
  const sequence = numberValue(value.sequence);
  const delegationId = stringValue(payload.delegation_id, value.delegation_id);
  const dispatchBatchId = stringValue(payload.dispatch_batch_id, value.dispatch_batch_id);
  const legacySeed = [runId, taskId, eventType, occurredAt, sequence, delegationId,
    value.action, value.message].map((v) => String(v ?? "")).join("|");
  const eventId = stringValue(value.event_id, value.audit_event_id, value.eventId)
    ?? `legacy_${hashText(legacySeed)}`;

  return {
    eventId,
    eventType,
    runId,
    taskId,
    sequence,
    occurredAt,
    severity: stringValue(value.severity) ?? "info",
    message: stringValue(value.message, payload.summary, payload.report_summary, value.action, value.reason_code) ?? "",
    reasonCode: stringValue(value.reason_code, value.reasonCode),
    action: stringValue(value.action),
    auditTraceId: stringValue(value.audit_trace_id, value.auditTraceId),
    externalRequestId: stringValue(
      value.external_request_id,
      value.externalRequestId,
      payload.external_request_id,
    ),
    agentKey: stringValue(payload.agent_key, value.agent_key) ?? "main",
    agentLabel: stringValue(payload.agent_label, value.agent_label),
    leaderTaskId: stringValue(payload.leader_task_id, value.leader_task_id),
    childTaskId: stringValue(payload.child_task_id, value.child_task_id),
    delegationId,
    dispatchBatchId,
    dispatchBatchNo: numberValue(payload.dispatch_batch_no, payload.batch_no, payload.no,
      value.dispatch_batch_no, value.batch_no),
    displayLabel: stringValue(payload.display_label, value.display_label),
    redactedPayload: payload,
    sources: [source],
  };
}

function eventTime(event: ActivityEvent): number {
  const parsed = Date.parse(event.occurredAt);
  return Number.isFinite(parsed) ? parsed : 0;
}

/** 稳定顺序：跨恢复周期以 occurred_at 为准；同一时刻以实时 sequence 决胜。 */
export function compareActivityEvents(a: ActivityEvent, b: ActivityEvent): number {
  const timeDiff = eventTime(a) - eventTime(b);
  if (timeDiff) return timeDiff;
  if (a.sequence !== undefined && b.sequence !== undefined && a.sequence !== b.sequence) {
    return a.sequence - b.sequence;
  }
  return a.eventId.localeCompare(b.eventId);
}

function mergeEvent(previous: ActivityEvent, next: ActivityEvent): ActivityEvent {
  const prefer = (incoming: string | undefined, current: string | undefined) => incoming || current;
  const eventType = previous.eventType.startsWith("openops.")
    ? previous.eventType
    : next.eventType.startsWith("openops.")
      ? next.eventType
      : previous.eventType !== "unknown" ? previous.eventType : next.eventType;
  const severityRank = (value: string): number => {
    const normalized = value.toLowerCase();
    if (normalized === "critical") return 4;
    if (["error", "danger", "fatal"].includes(normalized)) return 3;
    if (normalized === "warning") return 2;
    if (["success", "good"].includes(normalized)) return 1;
    return 0;
  };
  const severity = severityRank(previous.severity) >= severityRank(next.severity)
    ? previous.severity : next.severity;
  const occurredAt = eventTime(previous) && eventTime(next)
    ? (eventTime(previous) <= eventTime(next) ? previous.occurredAt : next.occurredAt)
    : previous.occurredAt || next.occurredAt;
  return {
    ...previous,
    ...next,
    eventType,
    runId: prefer(next.runId, previous.runId),
    taskId: prefer(next.taskId, previous.taskId),
    sequence: next.sequence ?? previous.sequence,
    occurredAt,
    severity,
    message: prefer(next.message, previous.message) ?? "",
    reasonCode: prefer(next.reasonCode, previous.reasonCode),
    action: prefer(next.action, previous.action),
    auditTraceId: prefer(next.auditTraceId, previous.auditTraceId),
    externalRequestId: prefer(next.externalRequestId, previous.externalRequestId),
    agentKey: next.agentKey !== "main" ? next.agentKey : previous.agentKey,
    agentLabel: prefer(next.agentLabel, previous.agentLabel),
    leaderTaskId: prefer(next.leaderTaskId, previous.leaderTaskId),
    childTaskId: prefer(next.childTaskId, previous.childTaskId),
    delegationId: prefer(next.delegationId, previous.delegationId),
    dispatchBatchId: prefer(next.dispatchBatchId, previous.dispatchBatchId),
    dispatchBatchNo: next.dispatchBatchNo ?? previous.dispatchBatchNo,
    displayLabel: prefer(next.displayLabel, previous.displayLabel),
    redactedPayload: { ...previous.redactedPayload, ...next.redactedPayload },
    sources: Array.from(new Set([...previous.sources, ...next.sources])),
  };
}

export function mergeActivityEvents(
  current: readonly ActivityEvent[],
  inputs: readonly ActivityEventInput[],
  source: ActivityEventSource,
): ActivityEvent[] {
  const byId = new Map(current.map((event) => [event.eventId, event]));
  for (const input of inputs) {
    const event = normalizeActivityEvent(input, source);
    if (!event) continue;
    const previous = byId.get(event.eventId);
    byId.set(event.eventId, previous ? mergeEvent(previous, event) : event);
  }
  return [...byId.values()].sort(compareActivityEvents);
}

export function normalizeDelegationStatus(value: unknown): DelegationStatus {
  switch (String(value ?? "").toLowerCase()) {
    case "running":
    case "pending":
    case "queued": return "running";
    case "completed":
    case "reported":
    case "succeeded": return "completed";
    case "failed":
    case "failed_no_report": return "failed";
    case "timeout":
    case "timed_out": return "timeout";
    case "cancelled":
    case "canceled": return "cancelled";
    default: return "unknown";
  }
}

export function normalizeDelegation(input: DelegationInput): Delegation | undefined {
  if ("delegationId" in input) return input as Delegation;
  const value = row(input);
  const delegationId = stringValue(value.delegation_id, value.delegationId);
  if (!delegationId) return undefined;
  return {
    delegationId,
    runId: stringValue(value.run_id, value.agent_run_id),
    leaderTaskId: stringValue(value.leader_task_id),
    childTaskId: stringValue(value.child_task_id),
    agentKey: stringValue(value.agent_key) ?? "unknown",
    agentLabel: (() => {
      const label = stringValue(value.agent_label);
      const key = stringValue(value.agent_key) ?? "unknown";
      return label && label !== key ? label : undefined;
    })(),
    dispatchBatchId: stringValue(value.dispatch_batch_id),
    dispatchBatchNo: numberValue(value.dispatch_batch_no, value.batch_no),
    status: normalizeDelegationStatus(value.delegation_status ?? value.status),
    // state 只允许返回后端生成的安全摘要；不兼容读取账本 raw task_text/report_text。
    taskSummary: stringValue(value.task_summary),
    reportSummary: stringValue(value.report_summary),
    hadFinalReport: booleanValue(value.had_final_report),
    deadline: stringValue(value.deadline),
    createdAt: stringValue(value.creation_date, value.created_at),
    updatedAt: stringValue(value.last_update_date, value.updated_at),
    authoritative: true,
  };
}

export function mergeDelegations(
  current: readonly Delegation[],
  inputs: readonly DelegationInput[],
): Delegation[] {
  const byId = new Map(current.map((item) => [item.delegationId, item]));
  for (const input of inputs) {
    const item = normalizeDelegation(input);
    if (!item) continue;
    const previous = byId.get(item.delegationId);
    byId.set(item.delegationId, previous ? {
      ...previous,
      ...item,
      agentLabel: item.agentLabel || previous.agentLabel,
      childTaskId: item.childTaskId || previous.childTaskId,
      dispatchBatchId: item.dispatchBatchId || previous.dispatchBatchId,
      dispatchBatchNo: item.dispatchBatchNo ?? previous.dispatchBatchNo,
      taskSummary: item.taskSummary || previous.taskSummary,
      reportSummary: item.reportSummary || previous.reportSummary,
      authoritative: previous.authoritative || item.authoritative,
    } : item);
  }
  return [...byId.values()];
}

export function createActivityState(): ActivityStoreState {
  return { events: [], delegations: [], nextCursor: null, hasMore: false };
}

/** GET /state → reducer 初始状态；兼容 events_cursor/events_next_cursor 两版字段名。 */
export function activityStateFromSnapshot(snapshot: unknown): ActivityStoreState {
  const value = row(snapshot);
  const eventInputs = Array.isArray(value.recent_events) ? value.recent_events as UnknownRow[] : [];
  const delegationInputs = Array.isArray(value.delegations) ? value.delegations as UnknownRow[] : [];
  return {
    events: mergeActivityEvents([], eventInputs, "state"),
    delegations: mergeDelegations([], delegationInputs),
    nextCursor: stringValue(value.events_next_cursor, value.events_cursor) ?? null,
    hasMore: booleanValue(value.events_has_more),
  };
}

export function normalizeActivityPage(page: unknown): ActivityEventsPage {
  const value = row(page);
  const inputs = Array.isArray(value.items) ? value.items as ActivityEventInput[] : [];
  return {
    items: mergeActivityEvents([], inputs, "audit"),
    next_cursor: stringValue(value.next_cursor) ?? null,
    has_more: booleanValue(value.has_more),
  };
}

export function prependActivityPage(state: ActivityStoreState, page: ActivityEventsPage): ActivityStoreState {
  return {
    ...state,
    events: mergeActivityEvents(state.events, page.items, "audit"),
    nextCursor: page.next_cursor,
    hasMore: page.has_more,
  };
}

export type ActivityAction =
  | { type: "hydrate"; snapshot: unknown }
  | { type: "merge_events"; events: ActivityEventInput[]; source: ActivityEventSource }
  | { type: "merge_delegations"; delegations: DelegationInput[] }
  | { type: "prepend_page"; page: ActivityEventsPage }
  | { type: "reset" };

export function activityReducer(state: ActivityStoreState, action: ActivityAction): ActivityStoreState {
  switch (action.type) {
    case "hydrate": {
      const restored = activityStateFromSnapshot(action.snapshot);
      const alreadyHydrated = state.events.some((event) =>
        event.sources.includes("state") || event.sources.includes("audit"));
      return {
        ...restored,
        // AG-UI/SSE 可能比 /state 先到；恢复只能补齐，不能覆盖已接收的实时事件。
        events: mergeActivityEvents(state.events, restored.events, "state"),
        delegations: mergeDelegations(state.delegations, restored.delegations),
        // 重连的最新页快照只补事件，不把用户已经向前翻到的历史游标拨回去。
        nextCursor: alreadyHydrated ? state.nextCursor : restored.nextCursor,
        hasMore: alreadyHydrated ? state.hasMore : restored.hasMore,
      };
    }
    case "merge_events": return { ...state, events: mergeActivityEvents(state.events, action.events, action.source) };
    case "merge_delegations": return { ...state, delegations: mergeDelegations(state.delegations, action.delegations) };
    case "prepend_page": return prependActivityPage(state, action.page);
    case "reset": return createActivityState();
  }
}

function lifecycleStatus(events: readonly ActivityEvent[]): DelegationStatus {
  let status: DelegationStatus = "unknown";
  for (const event of events) {
    const type = event.eventType.toLowerCase();
    if (type.includes("subagent.reported")) status = "completed";
    else if (type.includes("subagent.timeout")) status = "timeout";
    else if (type.includes("subagent.failed")) status = "failed";
    else if (type.includes("subagent.cancelled") || type.includes("subagent.canceled")) status = "cancelled";
    else if (type.includes("subagent.dispatched") || type.includes("subagent.started")) status = "running";
  }
  return status;
}

function hasPendingApproval(events: readonly ActivityEvent[]): boolean {
  const pending = new Set<string>();
  for (const event of events) {
    const type = event.eventType.toLowerCase();
    const id = stringValue(event.redactedPayload.approval_request_id) ?? "__unknown__";
    if (type.includes("approval.required")) pending.add(id);
    if (type.includes("approval.approved") || type.includes("approval.rejected")
      || type.includes("approval.timeout") || type.includes("approval.cancelled")
      || type.includes("approval.canceled")) {
      if (id === "__unknown__") pending.clear();
      else pending.delete(id);
    }
  }
  return pending.size > 0;
}

const isSubagentEvent = (event: ActivityEvent): boolean =>
  event.agentKey !== "main" || Boolean(event.delegationId || event.dispatchBatchId)
  || event.eventType.toLowerCase().includes("subagent.");

function roundStatus(tracks: readonly DelegationTrack[]): DispatchRoundStatus {
  if (tracks.some((track) => track.delegation.status === "running")) return "running";
  if (!tracks.length || tracks.every((track) => track.delegation.status === "unknown")) return "unknown";
  if (tracks.some((track) => ["failed", "timeout", "cancelled"].includes(track.delegation.status))) {
    return "completed_with_errors";
  }
  return "completed";
}

function timeBounds(events: readonly ActivityEvent[], delegations: readonly Delegation[]): [string?, string?] {
  const values = [
    ...events.map((event) => event.occurredAt),
    ...delegations.flatMap((item) => [item.createdAt, item.updatedAt]).filter((v): v is string => Boolean(v)),
  ].filter(Boolean).sort((a, b) => Date.parse(a) - Date.parse(b));
  return [values[0], values.at(-1)];
}

/** reducer 状态 → 活动栏模型。账本终态覆盖事件推导，waiting approval 仅作 running 子态。 */
export function projectRailModel(state: ActivityStoreState): RailModel {
  const events = [...state.events].sort(compareActivityEvents);
  const eventByDelegation = new Map<string, ActivityEvent[]>();
  for (const event of events) {
    if (!event.delegationId) continue;
    const list = eventByDelegation.get(event.delegationId) ?? [];
    list.push(event);
    eventByDelegation.set(event.delegationId, list);
  }

  const delegationById = new Map(state.delegations.map((item) => [item.delegationId, item]));
  for (const [delegationId, linked] of eventByDelegation) {
    const previous = delegationById.get(delegationId);
    const sample = linked.find((event) => event.dispatchBatchId || event.agentLabel) ?? linked[0];
    if (previous) {
      const inferredStatus = lifecycleStatus(linked);
      const terminalStatuses: DelegationStatus[] = ["completed", "failed", "timeout", "cancelled"];
      delegationById.set(delegationId, {
        ...previous,
        childTaskId: previous.childTaskId || sample.childTaskId,
        agentLabel: previous.agentLabel && previous.agentLabel !== previous.agentKey
          ? previous.agentLabel
          : sample.agentLabel || previous.agentLabel,
        dispatchBatchId: previous.dispatchBatchId || sample.dispatchBatchId,
        dispatchBatchNo: previous.dispatchBatchNo ?? sample.dispatchBatchNo,
        // 已落账的终态最权威；但 /state 里的 running 快照可能早于随后到达的实时终态，
        // 此时必须让 reported/failed/timeout/cancelled 及时收口，避免轨迹永久转圈。
        status: terminalStatuses.includes(previous.status)
          ? previous.status
          : inferredStatus !== "unknown" ? inferredStatus : previous.status,
      });
    } else {
      delegationById.set(delegationId, {
        delegationId,
        runId: sample.runId,
        leaderTaskId: sample.leaderTaskId,
        childTaskId: sample.childTaskId,
        agentKey: sample.agentKey,
        agentLabel: sample.agentLabel,
        dispatchBatchId: sample.dispatchBatchId,
        dispatchBatchNo: sample.dispatchBatchNo,
        status: lifecycleStatus(linked),
        taskSummary: stringValue(sample.redactedPayload.task_summary, sample.redactedPayload.summary),
        reportSummary: stringValue(sample.redactedPayload.report_summary),
        hadFinalReport: linked.some((event) => event.eventType.includes("subagent.reported")),
        authoritative: false,
      });
    }
  }

  const tracks: DelegationTrack[] = [...delegationById.values()].map((delegation) => {
    const linked = [...(eventByDelegation.get(delegation.delegationId) ?? [])].sort(compareActivityEvents);
    return {
      delegation,
      events: linked,
      waitingApproval: delegation.status === "running" && hasPendingApproval(linked),
    };
  });

  // 新事件应始终带 delegation_id；旧事件只在能唯一匹配时归轨，否则留在轮次级别。
  const unassigned = events.filter((event) => isSubagentEvent(event) && !event.delegationId);
  for (const event of [...unassigned]) {
    const candidates = tracks.filter((track) => {
      const d = track.delegation;
      if (event.dispatchBatchId && d.dispatchBatchId !== event.dispatchBatchId) return false;
      if (event.agentKey !== "main" && d.agentKey !== event.agentKey) return false;
      return Boolean(event.dispatchBatchId || event.agentKey !== "main");
    });
    if (candidates.length === 1) candidates[0].events = [...candidates[0].events, event].sort(compareActivityEvents);
  }

  const roundTracks = new Map<string, DelegationTrack[]>();
  for (const track of tracks) {
    const d = track.delegation;
    const key = d.dispatchBatchId
      ? `batch:${d.dispatchBatchId}`
      : `legacy:${d.leaderTaskId ?? d.runId ?? "history"}`;
    const list = roundTracks.get(key) ?? [];
    list.push(track);
    roundTracks.set(key, list);
  }
  for (const event of unassigned) {
    const key = event.dispatchBatchId
      ? `batch:${event.dispatchBatchId}`
      : `legacy:${event.leaderTaskId ?? event.runId ?? "history"}`;
    if (!roundTracks.has(key)) roundTracks.set(key, []);
  }

  const rounds: DispatchRound[] = [...roundTracks].map(([id, currentTracks]) => {
    const trackIds = new Set(currentTracks.map((track) => track.delegation.delegationId));
    const roundUnassigned = unassigned.filter((event) => {
      const key = event.dispatchBatchId
        ? `batch:${event.dispatchBatchId}`
        : `legacy:${event.leaderTaskId ?? event.runId ?? "history"}`;
      if (key !== id) return false;
      return !currentTracks.some((track) => track.events.some((item) => item.eventId === event.eventId));
    });
    const allRoundEvents = events.filter((event) => event.delegationId && trackIds.has(event.delegationId));
    const sampleTrack = currentTracks[0]?.delegation;
    const sampleEvent = roundUnassigned[0] ?? allRoundEvents[0];
    const dispatchBatchId = sampleTrack?.dispatchBatchId ?? sampleEvent?.dispatchBatchId;
    const dispatchBatchNo = sampleTrack?.dispatchBatchNo ?? sampleEvent?.dispatchBatchNo;
    const legacy = !dispatchBatchId;
    const counts = {
      total: currentTracks.length,
      running: currentTracks.filter((track) => track.delegation.status === "running").length,
      waitingApproval: currentTracks.filter((track) => track.waitingApproval).length,
      completed: currentTracks.filter((track) => track.delegation.status === "completed").length,
      failed: currentTracks.filter((track) => track.delegation.status === "failed").length,
      timeout: currentTracks.filter((track) => track.delegation.status === "timeout").length,
      cancelled: currentTracks.filter((track) => track.delegation.status === "cancelled").length,
    };
    const [startedAt, latestAt] = timeBounds([...allRoundEvents, ...roundUnassigned],
      currentTracks.map((track) => track.delegation));
    return {
      id,
      dispatchBatchId,
      dispatchBatchNo,
      leaderTaskId: sampleTrack?.leaderTaskId ?? sampleEvent?.leaderTaskId,
      label: legacy ? "历史派发" : dispatchBatchNo !== undefined ? `第 ${dispatchBatchNo} 轮` : "派发批次",
      legacy,
      status: roundStatus(currentTracks),
      startedAt,
      latestAt,
      tracks: currentTracks.sort((a, b) => {
        if (a.delegation.status === "running" && b.delegation.status !== "running") return -1;
        if (b.delegation.status === "running" && a.delegation.status !== "running") return 1;
        return a.delegation.delegationId.localeCompare(b.delegation.delegationId);
      }),
      unassignedEvents: roundUnassigned,
      counts,
    };
  }).sort((a, b) => {
    if (a.status === "running" && b.status !== "running") return -1;
    if (b.status === "running" && a.status !== "running") return 1;
    return Date.parse(b.latestAt ?? "") - Date.parse(a.latestAt ?? "")
      || (b.dispatchBatchNo ?? -1) - (a.dispatchBatchNo ?? -1);
  });

  return {
    events,
    generalEvents: events.filter((event) => !isSubagentEvent(event)),
    rounds,
    nextCursor: state.nextCursor,
    hasMore: state.hasMore,
  };
}
