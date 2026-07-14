import type { DispatchRound } from "../../lib/api/types";
import { color, radius, toneColor } from "../../theme/tokens";
import { Icon } from "../../ui";
import {
  eventSummary,
  eventTitle,
  eventTone,
  eventTool,
  showInBusinessTimeline,
} from "./eventPresentation";
import { formatClock, roleVisual } from "./visuals";

type TimelineEntry = {
  id: string;
  title: string;
  summary: string;
  tool?: string;
  occurredAt: string;
  sequence?: number;
  roleKey: string;
  roleLabel: string;
  tone: "neutral" | "good" | "warning" | "danger";
};

function entriesForRound(round: DispatchRound): TimelineEntry[] {
  const entries: TimelineEntry[] = round.tracks.flatMap((track) =>
    track.events.filter(showInBusinessTimeline).map((event) => ({
      id: event.eventId,
      title: eventTitle(event),
      summary: eventSummary(event),
      tool: eventTool(event),
      occurredAt: event.occurredAt,
      sequence: event.sequence,
      roleKey: track.delegation.agentKey,
      roleLabel: track.delegation.agentLabel ?? track.delegation.agentKey,
      tone: eventTone(event),
    })),
  );
  entries.push(
    ...round.unassignedEvents.filter(showInBusinessTimeline).map((event) => ({
      id: event.eventId,
      title: eventTitle(event),
      summary: eventSummary(event),
      tool: eventTool(event),
      occurredAt: event.occurredAt,
      sequence: event.sequence,
      roleKey: event.agentKey || "main",
      roleLabel: event.agentLabel ?? event.agentKey ?? "主控",
      tone: eventTone(event),
    })),
  );

  for (const track of round.tracks) {
    const delegation = track.delegation;
    const roleLabel = delegation.agentLabel ?? delegation.agentKey;
    if (delegation.taskSummary && !track.events.some((event) => event.eventType.includes("subagent.dispatched"))) {
      entries.push({
        id: `ledger-dispatch-${delegation.delegationId}`,
        title: "已派发调查任务",
        summary: delegation.taskSummary,
        occurredAt: delegation.createdAt ?? round.startedAt ?? round.latestAt ?? "",
        roleKey: delegation.agentKey,
        roleLabel,
        tone: "neutral",
      });
    }
    if (delegation.reportSummary && !track.events.some((event) => event.eventType.includes("subagent.reported"))) {
      entries.push({
        id: `ledger-report-${delegation.delegationId}`,
        title: "已向主控汇报",
        summary: delegation.reportSummary,
        occurredAt: delegation.updatedAt ?? round.latestAt ?? round.startedAt ?? "",
        roleKey: delegation.agentKey,
        roleLabel,
        tone: "good",
      });
    }
  }

  return entries.sort((a, b) => {
    const timeA = Date.parse(a.occurredAt);
    const timeB = Date.parse(b.occurredAt);
    if (!Number.isNaN(timeA) && !Number.isNaN(timeB) && timeA !== timeB) return timeA - timeB;
    return (a.sequence ?? 0) - (b.sequence ?? 0);
  });
}

export function BusinessTimeline({ round }: { round: DispatchRound }) {
  const entries = entriesForRound(round);
  if (!entries.length) {
    return <div className="oa-activity-empty-inline">本轮暂无可展示的业务里程碑</div>;
  }

  return (
    <ol className="oa-business-timeline" aria-label={`${round.label}业务时间线`}>
      {entries.map((entry, index) => {
        const tone = toneColor[entry.tone];
        const { roleKey, roleLabel } = entry;
        const role = roleVisual(roleKey);
        return (
          <li key={entry.id} className="oa-business-event">
            <span className="oa-business-event-rail" aria-hidden>
              <span className="oa-business-event-dot" style={{ background: tone.dot }} />
              {index < entries.length - 1 ? <span className="oa-business-event-line" /> : null}
            </span>
            <div className="oa-business-event-body">
              <div className="oa-business-event-title-row">
                <span
                  title={roleLabel}
                  style={{ width: 20, height: 20, display: "inline-flex", alignItems: "center", justifyContent: "center", borderRadius: radius.full, background: `${role.color}16` }}
                >
                  <Icon name={role.icon} size={12} color={role.color} />
                </span>
                <strong>{entry.title}</strong>
                <time dateTime={entry.occurredAt}>{formatClock(entry.occurredAt)}</time>
              </div>
              <div className="oa-business-event-role">{roleLabel}</div>
              {entry.tool ? <code className="oa-business-event-tool">{entry.tool}</code> : null}
              {entry.summary ? <p style={{ color: entry.tone === "danger" ? color.dangerText : undefined }}>{entry.summary}</p> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
