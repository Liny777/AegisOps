import type { ChatMessage } from "../lib/api/types";

/**
 * 会话恢复错误必须带归属，避免旧 Run 的 resync 成功把新 target 的切换错误清掉。
 */
export type WorkbenchRecoveryIssue =
  | {
      owner: "transition";
      generation: number;
      targetKey: string;
      message: string;
    }
  | {
      owner: "run";
      runId: string;
      message: string;
    };

/** 仅清除指定 Run 自己的恢复错误；新 target 的切换错误必须原样保留。 */
export function clearRunRecoveryIssue(
  issue: WorkbenchRecoveryIssue | null,
  runId: string,
): WorkbenchRecoveryIssue | null {
  return issue?.owner === "run" && issue.runId === runId ? null : issue;
}

/**
 * 初始化请求只有在本 effect 的 controller 被取消，或它已不是当前代次时才可静默退出。
 * 浏览器/共享请求意外抛出的 AbortError 不满足这两个条件，调用方应进入可重试失败态。
 */
export function isOwnedOrStaleInitializationCancellation(
  controllerAborted: boolean,
  attemptGeneration: number,
  currentGeneration: number,
): boolean {
  return controllerAborted || attemptGeneration !== currentGeneration;
}

/**
 * Run 切换时同步清理“加载更早事件”的请求与 UI 状态。
 *
 * 旧请求的 finally 会因 request ref 已换代而跳过复位，因此必须在换代点主动
 * 清除 loading；否则新会话会永久停留在 loadingMore=true，后续分页也会被挡住。
 */
export function resetActivityPageRequest(
  controller: AbortController | null,
  setLoading: (loading: boolean) => void,
): null {
  controller?.abort();
  setLoading(false);
  return null;
}

export function workbenchTransitionUi(input: {
  hasDisplayedRun: boolean;
  routeTransitionPending: boolean;
  transitionKey: string;
  targetKey: string;
  transitionStatus: "loading" | "ready" | "error";
}): {
  failedCurrentTarget: boolean;
  showSwitchOverlay: boolean;
  showFailureOverlay: boolean;
  blockInput: boolean;
} {
  const failedCurrentTarget = input.transitionKey === input.targetKey &&
    input.transitionStatus === "error";
  const showSwitchOverlay = input.hasDisplayedRun && !failedCurrentTarget && (
    input.routeTransitionPending ||
    input.transitionKey !== input.targetKey ||
    input.transitionStatus === "loading"
  );
  const showFailureOverlay = input.hasDisplayedRun && failedCurrentTarget;
  return {
    failedCurrentTarget,
    showSwitchOverlay,
    showFailureOverlay,
    // 失败后 URL 已指向新 target，但画面仍是旧 Run；必须保持全部旧 Run 操作禁用。
    blockInput: showSwitchOverlay || showFailureOverlay,
  };
}

/** Portal 浮层不受 Workbench 局部遮罩层级约束，切换或失败时必须直接卸载。 */
export function shouldShowWorkbenchPortal(visible: boolean, inputBlocked: boolean): boolean {
  return visible && !inputBlocked;
}

/** 仅当前可见 Run 的最新流可以写 UI；旧 Run/旧 token 的后台回调保持静默。 */
export function isCurrentRunStream(
  activeRunId: string | null,
  streamRunId: string,
  activeToken: symbol | null,
  streamToken: symbol,
): boolean {
  return activeRunId === streamRunId && activeToken === streamToken;
}

/** Run 级写请求完成后，只有目标仍是当前可见 Run 才能回写本地 UI。 */
export function isCurrentRunAction(activeRunId: string | null, actionRunId: string): boolean {
  return activeRunId === actionRunId;
}

type UnknownRow = Record<string, unknown>;

const asRow = (value: unknown): UnknownRow => (
  value && typeof value === "object" && !Array.isArray(value)
    ? value as UnknownRow
    : {}
);

const text = (value: unknown): string => typeof value === "string" ? value.trim() : "";

const terminalKind = (eventType: string): "completed" | "failed" | "cancelled" | "interrupted" | null => {
  const normalized = eventType.toLowerCase().replace(/^openops\./, "");
  if (normalized === "task.completed") return "completed";
  if (normalized === "task.failed") return "failed";
  if (normalized === "task.cancelled" || normalized === "task.canceled") return "cancelled";
  if (normalized === "task.interrupted") return "interrupted";
  return null;
};

function terminalMessage(event: UnknownRow, kind: NonNullable<ReturnType<typeof terminalKind>>): string {
  const payload = asRow(event.payload_redacted_json);
  if (kind === "completed") {
    // conclusion 是后端脱敏后专门允许持久化的终态正文；summary 是安全的短回退。
    return text(payload.conclusion) || text(payload.summary);
  }
  // recent_events 只信任 payload_redacted_json；不读取未来可能混入的 raw message 字段。
  return text(payload.summary) || text(payload.error_summary);
}

/**
 * 从 `/state` 的安全持久化字段恢复 fallback transcript。
 *
 * Agent 的流式 delta 不落审计，因此这里不臆造完整历史；仅恢复最新任务输入，以及同一
 * task_id 的最后一个持久化终态（conclusion/summary/error_summary）。event_id 直接作为
 * 消息 id，后续 SSE/AG-UI 重放可按同一 id 去重。
 */
export function recoverSnapshotMessages(snapshot: UnknownRow, runId: string): ChatMessage[] {
  const activeTask = asRow(snapshot.active_task);
  const taskId = text(activeTask.task_id);
  const input = text(activeTask.input_text);
  const messages: ChatMessage[] = input
    ? [{ id: `u-${runId}`, role: "user", text: input }]
    : [];

  // 没有最新任务身份时不猜测 recent_events 属于哪一轮，避免把旧任务结论串进当前会话。
  if (!taskId || !Array.isArray(snapshot.recent_events)) return messages;

  let terminal: { event: UnknownRow; kind: NonNullable<ReturnType<typeof terminalKind>> } | null = null;
  for (const value of snapshot.recent_events) {
    const event = asRow(value);
    if (text(event.task_id) !== taskId) continue;
    const kind = terminalKind(text(event.event_type));
    if (kind) terminal = { event, kind };
  }
  if (!terminal) return messages;

  const eventId = text(terminal.event.event_id) || text(terminal.event.audit_event_id);
  const assistantText = terminalMessage(terminal.event, terminal.kind);
  if (!eventId || !assistantText) return messages;

  messages.push({
    id: eventId,
    role: "bot",
    text: assistantText,
    showCopy: terminal.kind === "completed",
  });
  return messages;
}
