import type { DispatchRound, DelegationTrack } from "../../lib/api/types";
import { color } from "../../theme/tokens";

export type ActivityDisplayStatus =
  | "running"
  | "waiting_approval"
  | "done"
  | "failed"
  | "timeout"
  | "cancelled"
  | "unknown";

export const ROLE_VISUALS: Record<string, { icon: string; color: string }> = {
  inspect: { icon: "radar", color: "#1683ff" },
  diagnose: { icon: "stethoscope", color: "#7c3aed" },
  recover: { icon: "first-aid-kit", color: "#1fa463" },
  main: { icon: "route", color: "#7c3aed" },
};

export const STATUS_VISUALS: Record<
  ActivityDisplayStatus,
  { icon: string; color: string; label: string; spin?: boolean }
> = {
  running: { icon: "loader-2", color: "#c18a20", label: "运行中", spin: true },
  waiting_approval: { icon: "hourglass", color: color.brand, label: "待审批" },
  done: { icon: "circle-check", color: color.good, label: "已汇报" },
  failed: { icon: "alert-triangle", color: color.danger, label: "异常" },
  timeout: { icon: "clock-exclamation", color: "#e07b25", label: "已超时" },
  cancelled: { icon: "ban", color: color.textMuted, label: "已取消" },
  unknown: { icon: "history", color: color.textSubtle, label: "历史派发" },
};

export function trackStatus(track: DelegationTrack): ActivityDisplayStatus {
  if (track.waitingApproval && track.delegation.status === "running") return "waiting_approval";
  switch (track.delegation.status) {
    case "completed":
      return "done";
    case "running":
    case "failed":
    case "timeout":
    case "cancelled":
      return track.delegation.status;
    default:
      return "unknown";
  }
}
export function roundDisplay(round: DispatchRound): {
  status: ActivityDisplayStatus;
  label: string;
} {
  if (round.counts.running > 0) {
    if (round.counts.waitingApproval === round.counts.running) {
      return { status: "waiting_approval", label: "等待审批" };
    }
    return { status: "running", label: "进行中" };
  }
  if (round.status === "completed_with_errors") {
    return { status: "failed", label: "已结束，有异常" };
  }
  if (round.status === "completed") return { status: "done", label: "已完成" };
  return { status: "unknown", label: "历史派发" };
}

export function roleVisual(roleKey: string) {
  return ROLE_VISUALS[roleKey] ?? { icon: "robot", color: color.brand };
}

export function formatClock(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function orderedRounds(rounds: DispatchRound[]): DispatchRound[] {
  return rounds
    .map((round, index) => ({ round, index }))
    .sort((a, b) => {
      const noA = a.round.dispatchBatchNo;
      const noB = b.round.dispatchBatchNo;
      if (noA !== undefined && noB !== undefined && noA !== noB) return noB - noA;
      const timeA = Date.parse(a.round.latestAt ?? a.round.startedAt ?? "");
      const timeB = Date.parse(b.round.latestAt ?? b.round.startedAt ?? "");
      if (!Number.isNaN(timeA) && !Number.isNaN(timeB) && timeA !== timeB) return timeB - timeA;
      return a.index - b.index;
    })
    .map(({ round }) => round);
}
