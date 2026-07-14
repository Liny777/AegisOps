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

  const runMatch = pathname.match(/^\/agent-runs\/([^/]+)\/?$/);
  if (runMatch) {
    return {
      instanceId: "",
      explicitRunId: decodePathSegment(runMatch[1]),
    };
  }

  return null;
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
