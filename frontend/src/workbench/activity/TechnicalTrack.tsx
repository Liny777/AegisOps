import { useState } from "react";
import type { ActivityEvent, DelegationTrack, DispatchRound } from "../../lib/api/types";
import { color, radius, toneColor } from "../../theme/tokens";
import { Icon } from "../../ui";
import {
  eventIcon,
  eventSummary,
  eventTitle,
  eventTone,
  eventTool,
  technicalMeta,
} from "./eventPresentation";
import { formatClock, roleVisual, STATUS_VISUALS, trackStatus } from "./visuals";
import { trackDomId } from "./OrchestrationGraph";

function TechnicalEvent({ event, last }: { event: ActivityEvent; last: boolean }) {
  const tone = toneColor[eventTone(event)];
  const tool = eventTool(event);
  const summary = eventSummary(event);
  const meta = technicalMeta(event);
  return (
    <li className="oa-technical-event">
      <span className="oa-technical-event-rail" aria-hidden>
        <span className="oa-technical-event-icon" style={{ background: tone.dot }}>
          <Icon name={eventIcon(event)} size={10} color="#fff" />
        </span>
        {!last ? <span className="oa-technical-event-line" /> : null}
      </span>
      <div className="oa-technical-event-body">
        <div className="oa-technical-event-heading">
          <strong>{eventTitle(event)}</strong>
          <time dateTime={event.occurredAt}>{formatClock(event.occurredAt)}</time>
        </div>
        <code className="oa-technical-event-type">{event.eventType}</code>
        {tool ? <div className="oa-technical-event-tool">{tool}</div> : null}
        {summary ? <p>{summary}</p> : null}
        {meta.length ? (
          <dl className="oa-technical-event-meta">
            {meta.map(({ label, value }) => (
              <div key={`${event.eventId}-${label}`}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </div>
    </li>
  );
}
export function TechnicalTrack({ round, track }: { round: DispatchRound; track: DelegationTrack }) {
  const status = trackStatus(track);
  const visual = STATUS_VISUALS[status];
  const role = roleVisual(track.delegation.agentKey);
  const label = track.delegation.agentLabel ?? track.delegation.agentKey;
  const [open, setOpen] = useState<boolean | null>(null);
  const expanded = open ?? status !== "done";

  return (
    <section
      id={trackDomId(round.id, track.delegation.delegationId)}
      className="oa-technical-track"
      aria-label={`${label} 活动轨迹`}
    >
      <button type="button" className="oa-technical-track-head" onClick={() => setOpen(!expanded)} aria-expanded={expanded}>
        <span style={{ width: 24, height: 24, display: "inline-flex", alignItems: "center", justifyContent: "center", borderRadius: radius.full, background: `${role.color}16` }}>
          <Icon name={role.icon} size={14} color={role.color} />
        </span>
        <span className="oa-technical-track-label">
          <strong>{label}</strong>
          <small title={track.delegation.delegationId}>#{track.delegation.delegationId.slice(0, 8)}</small>
        </span>
        <span className="oa-technical-track-status" style={{ color: visual.color }}>
          <Icon name={visual.icon} size={12} color={visual.color} spin={visual.spin} />
          {track.events.length} 步 · {visual.label}
        </span>
        <Icon name={expanded ? "chevron-down" : "chevron-right"} size={13} color={color.textSubtle} />
      </button>
      {expanded ? (
        track.events.length ? (
          <ol className="oa-technical-events">
            {track.events.map((event, index) => (
              <TechnicalEvent key={event.eventId} event={event} last={index === track.events.length - 1} />
            ))}
          </ol>
        ) : (
          <div className="oa-activity-empty-inline">等待活动事件…</div>
        )
      ) : null}
    </section>
  );
}
