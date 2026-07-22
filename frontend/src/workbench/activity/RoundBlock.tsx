import { useState } from "react";
import type { DispatchRound } from "../../lib/api/types";
import { color, radius } from "../../theme/tokens";
import { Icon } from "../../ui";
import { OrchestrationGraph } from "./OrchestrationGraph";
import { TechnicalTrack } from "./TechnicalTrack";
import { formatClock, roleVisual, roundDisplay, STATUS_VISUALS, trackStatus } from "./visuals";

/** 单轮派发块：恒为技术视图（编排图 + 各子 Agent 技术轨；业务视角已随内网反馈下线）。 */
export function RoundBlock({
  round,
  defaultExpanded,
}: {
  round: DispatchRound;
  defaultExpanded: boolean;
}) {
  const [open, setOpen] = useState<boolean | null>(null);
  const expanded = open ?? defaultExpanded;
  const display = roundDisplay(round);
  const status = STATUS_VISUALS[display.status];
  const settled = round.counts.completed + round.counts.failed + round.counts.timeout + round.counts.cancelled;

  return (
    <section className="oa-round-block" aria-label={round.label}>
      <button type="button" className="oa-round-head" onClick={() => setOpen(!expanded)} aria-expanded={expanded}>
        <span className="oa-round-label">{round.label}</span>
        <span className="oa-round-role-icons" aria-label={`${round.tracks.length} 个子 Agent`}>
          {round.tracks.slice(0, 4).map((track) => {
            const role = roleVisual(track.delegation.agentKey);
            return (
              <span
                key={track.delegation.delegationId}
                title={track.delegation.agentLabel ?? track.delegation.agentKey}
                style={{ borderColor: `${role.color}42`, background: `${role.color}12` }}
              >
                <Icon name={role.icon} size={11} color={role.color} />
              </span>
            );
          })}
          {round.tracks.length > 4 ? <small>+{round.tracks.length - 4}</small> : null}
        </span>
        <time className="oa-round-time" dateTime={round.startedAt}>{formatClock(round.startedAt)}</time>
        <span className="oa-round-count">{settled}/{round.counts.total}</span>
        <span className="oa-round-status" style={{ color: status.color }}>
          <Icon name={status.icon} size={12} color={status.color} spin={status.spin} />
          {display.label}
        </span>
        <Icon name={expanded ? "chevron-down" : "chevron-right"} size={13} color={color.textSubtle} />
      </button>

      <div className="oa-round-progress" aria-label={`已结束 ${settled}，共 ${round.counts.total}`}>
        {round.tracks.map((track) => {
          const visual = STATUS_VISUALS[trackStatus(track)];
          return <span key={track.delegation.delegationId} title={`${track.delegation.agentLabel ?? track.delegation.agentKey}：${visual.label}`} style={{ background: visual.color }} />;
        })}
      </div>

      {expanded ? (
        <div className="oa-round-body">
          <div className="oa-round-toolbar">
            <span>
              {round.counts.total} 位专家 · {settled} 已结束
              {round.counts.waitingApproval ? ` · ${round.counts.waitingApproval} 待审批` : ""}
            </span>
          </div>
          <div className="oa-technical-view">
            <OrchestrationGraph round={round} />
            {round.tracks.map((track) => (
              <TechnicalTrack key={track.delegation.delegationId} round={round} track={track} />
            ))}
            {round.unassignedEvents.length ? (
              <div className="oa-unassigned-note" style={{ borderRadius: radius.sm }}>
                <Icon name="info-circle" size={13} color={color.textMuted} />
                {round.unassignedEvents.length} 条历史事件无法无歧义归属到具体派发，未强行并轨。
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}
