import type { DispatchRound } from "../../lib/api/types";
import { Icon } from "../../ui";
import { roleVisual, STATUS_VISUALS, trackStatus } from "./visuals";

const WIDTH = 320;
const MAIN_X = 52;
const WORKER_X = 188;
const ROW_HEIGHT = 54;
const PAD = 14;

function trackDomId(roundId: string, delegationId: string): string {
  return `oa-track-${roundId}-${delegationId}`;
}

export function OrchestrationGraph({ round }: { round: DispatchRound }) {
  const height = Math.max(102, round.tracks.length * ROW_HEIGHT + PAD * 2);
  const mainY = height / 2;
  const y = (index: number) => PAD + ROW_HEIGHT * index + ROW_HEIGHT / 2;
  const roleTotals = round.tracks.reduce((totals, track) => {
    const key = track.delegation.agentKey;
    totals.set(key, (totals.get(key) ?? 0) + 1);
    return totals;
  }, new Map<string, number>());
  const roleIndexes = new Map<string, number>();

  const selectTrack = (delegationId: string) => {
    document.getElementById(trackDomId(round.id, delegationId))?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  return (
    <svg
      className="oa-orchestration-graph"
      viewBox={`0 0 ${WIDTH} ${height}`}
      role="img"
      aria-label={`${round.label}编排图：主控派发 ${round.tracks.length} 个子 Agent`}
    >
      {round.tracks.map((track, index) => {
        const status = trackStatus(track);
        const statusVisual = STATUS_VISUALS[status];
        return (
          <line
            key={`line-${track.delegation.delegationId}`}
            x1={MAIN_X + 20}
            y1={mainY}
            x2={WORKER_X - 18}
            y2={y(index)}
            stroke={status === "running" ? statusVisual.color : "#d7dae0"}
            strokeWidth="1.5"
            strokeDasharray={status === "running" ? "5 4" : undefined}
          />
        );
      })}

      <g aria-label="主控 Agent">
        <circle cx={MAIN_X} cy={mainY} r="20" fill="#f3efff" stroke="#7c3aed" strokeWidth="1.4" />
        <foreignObject x={MAIN_X - 9} y={mainY - 9} width="18" height="18" style={{ pointerEvents: "none" }}>
          <span className="oa-svg-icon"><Icon name="route" size={17} color="#7c3aed" /></span>
        </foreignObject>
        <text x={MAIN_X} y={mainY + 34} textAnchor="middle" className="oa-graph-main-label">主控</text>
      </g>

      {round.tracks.map((track, index) => {
        const status = trackStatus(track);
        const statusVisual = STATUS_VISUALS[status];
        const role = roleVisual(track.delegation.agentKey);
        const baseLabel = track.delegation.agentLabel ?? track.delegation.agentKey;
        const roleIndex = (roleIndexes.get(track.delegation.agentKey) ?? 0) + 1;
        roleIndexes.set(track.delegation.agentKey, roleIndex);
        const label = (roleTotals.get(track.delegation.agentKey) ?? 0) > 1
          ? `${baseLabel} · ${roleIndex}`
          : baseLabel;
        const nodeY = y(index);
        return (
          <g
            key={track.delegation.delegationId}
            role="button"
            tabIndex={0}
            aria-label={`查看 ${label} 的活动轨迹`}
            onClick={() => selectTrack(track.delegation.delegationId)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                selectTrack(track.delegation.delegationId);
              }
            }}
            className="oa-graph-worker"
          >
            <circle cx={WORKER_X} cy={nodeY} r="17" fill={`${statusVisual.color}14`} stroke={statusVisual.color} strokeWidth="1.4" />
            <foreignObject x={WORKER_X - 8} y={nodeY - 8} width="16" height="16" style={{ pointerEvents: "none" }}>
              <span className="oa-svg-icon"><Icon name={role.icon} size={15} color={role.color} /></span>
            </foreignObject>
            <circle cx={WORKER_X + 12} cy={nodeY - 13} r="4" fill={statusVisual.color} />
            <text x={WORKER_X + 26} y={nodeY - 1} className="oa-graph-role-label">{label}</text>
            <text x={WORKER_X + 26} y={nodeY + 13} fill={statusVisual.color} className="oa-graph-status-label">
              {statusVisual.label} · {track.events.length} 步
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export { trackDomId };
