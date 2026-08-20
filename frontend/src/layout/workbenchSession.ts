export type WorkbenchRunStatus = "active" | "closed";

/** A chat destination expressed by the current route. */
export interface WorkbenchTarget {
  /** Empty for /agent-runs/:runId until the run state resolves its owning instance. */
  instanceId: string;
  /** A run selected by the route or query string; null means ensure the instance's active run. */
  explicitRunId: string | null;
}

/** Canonical identity; may be reported as provisional active before /state later corrects its status. */
export interface ResolvedWorkbenchSession {
  runId: string;
  instanceId: string;
  runStatus: WorkbenchRunStatus;
}

const decodePathSegment = (value: string): string => {
  try {
    return decodeURIComponent(value);
  } catch {
    // Let the API/router surface malformed identifiers instead of crashing AppShell.
    return value;
  }
};

/**
 * Convert either supported chat route into a common target. Non-chat routes do
 * not own a Workbench and therefore return null.
 */
export function parseWorkbenchTarget(pathname: string, search: string): WorkbenchTarget | null {
  const instanceMatch = pathname.match(/^\/agent-teams\/([^/]+)\/chat\/?$/);
  if (instanceMatch) {
    const query = new URLSearchParams(search);
    return {
      instanceId: decodePathSegment(instanceMatch[1]),
      explicitRunId: query.get("run_id") || null,
    };
  }

  // 容忍旧版/外部拼接的 /chat 尾段（WeLink 深链实测形态）：等价解析到同一 run。
  // 其他尾段（/events 等）仍不匹配——交由通配 404 显式暴露而非静默吞掉。
  const runMatch = pathname.match(/^\/agent-runs\/([^/]+)(?:\/chat)?\/?$/);
  if (runMatch) {
    return {
      instanceId: "",
      explicitRunId: decodePathSegment(runMatch[1]),
    };
  }

  return null;
}

/** 从非对话页首次进入聊天时，debounce 尚未创建 retained Workbench 也必须占满主区。 */
export function shouldShowWorkbenchColdStart(
  routeTarget: WorkbenchTarget | null,
  hasRetainedWorkbench: boolean,
): boolean {
  return routeTarget !== null && !hasRetainedWorkbench;
}

const sameRawTarget = (left: WorkbenchTarget, right: WorkbenchTarget): boolean =>
  left.instanceId === right.instanceId && left.explicitRunId === right.explicitRunId;

/**
 * Decide whether an incoming chat route can keep using the one retained
 * Workbench. Once initialized, the resolved run is canonical across both route
 * shapes. A generic instance route may only reuse an active run belonging to
 * that instance; a closed run must go through ensureRun again.
 */
export function shouldReuseWorkbench(
  retainedTarget: WorkbenchTarget | null,
  resolvedSession: ResolvedWorkbenchSession | null,
  incomingTarget: WorkbenchTarget,
): boolean {
  if (!retainedTarget) return false;

  if (!resolvedSession) return sameRawTarget(retainedTarget, incomingTarget);

  if (incomingTarget.explicitRunId) {
    return incomingTarget.explicitRunId === resolvedSession.runId;
  }

  return resolvedSession.runStatus === "active" &&
    incomingTarget.instanceId === resolvedSession.instanceId;
}

export const WORKBENCH_TARGET_SWITCH_DELAY_MS = 120;

type TimerHandle = unknown;

export interface LatestWorkbenchTargetScheduler {
  /**
   * Queue a real Workbench target change. Returns false when the incoming route
   * already identifies the retained canonical session; any older queued change
   * is cancelled in that case.
   */
  request(
    retainedTarget: WorkbenchTarget | null,
    resolvedSession: ResolvedWorkbenchSession | null,
    incomingTarget: WorkbenchTarget,
    apply: (target: WorkbenchTarget) => void,
  ): boolean;
  cancel(): void;
  hasPending(): boolean;
}

interface LatestWorkbenchTargetSchedulerOptions {
  delayMs?: number;
  setTimer?: (callback: () => void, delayMs: number) => TimerHandle;
  clearTimer?: (handle: TimerHandle) => void;
}

/**
 * Trailing latest-wins scheduler for expensive Workbench switches.
 *
 * React Router still owns the URL immediately, so Sidebar selection remains
 * responsive. Only the target that initializes /state, SSE and CopilotKit is
 * delayed and coalesced.
 */
export function createLatestWorkbenchTargetScheduler(
  options: LatestWorkbenchTargetSchedulerOptions = {},
): LatestWorkbenchTargetScheduler {
  const delayMs = options.delayMs ?? WORKBENCH_TARGET_SWITCH_DELAY_MS;
  const setTimer = options.setTimer ?? ((callback, delay) => window.setTimeout(callback, delay));
  const clearTimer = options.clearTimer ?? ((handle) => window.clearTimeout(handle as number));
  let timer: TimerHandle | null = null;

  const cancel = () => {
    if (timer === null) return;
    clearTimer(timer);
    timer = null;
  };

  return {
    request(retainedTarget, resolvedSession, incomingTarget, apply) {
      if (shouldReuseWorkbench(retainedTarget, resolvedSession, incomingTarget)) {
        cancel();
        return false;
      }

      cancel();
      timer = setTimer(() => {
        timer = null;
        apply(incomingTarget);
      }, delayMs);
      return true;
    },
    cancel,
    hasPending: () => timer !== null,
  };
}
