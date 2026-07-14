import { useEffect, useMemo, useRef, useState } from "react";
import type { ActivityEvent, ActivityGroup, ActivityNode, DispatchRound } from "../lib/api/types";
import { color, radius, toneColor } from "../theme/tokens";
import { Icon } from "../ui";
import { RoundBlock, type WorkerViewMode } from "./activity/RoundBlock";
import { eventIcon, eventSummary, eventTitle, eventTone, eventTool } from "./activity/eventPresentation";
import { formatClock, orderedRounds } from "./activity/visuals";
import "./activity/ActivityRail.css";

const WORKER_VIEW_KEY = "openops.workerView";

type RailTab = "all" | "subagents";

export interface ActivityRailProps {
  /** 旧投影兼容入口；接入统一 reducer 后传「全部动态」分组。 */
  groups?: ActivityGroup[];
  /** 统一 reducer 输出的主控/全局事件；存在时优先于旧 groups 渲染。 */
  generalEvents?: ActivityEvent[];
  /** 后端批次 ID 投影出的权威轮次；同角色的多次 delegation 仍保持独立。 */
  rounds?: DispatchRound[];
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void | Promise<void>;
}

function GeneralActivityEvents({ events }: { events: ActivityEvent[] }) {
  if (!events.length) return <ActivityNodes groups={[]} />;
  const lifecycleKey = (event: ActivityEvent): string | undefined => {
    const type = event.eventType.toLowerCase();
    const payload = event.redactedPayload;
    if (type.includes("approval.")) {
      return `approval:${String(payload.approval_request_id ?? event.taskId ?? event.eventId)}`;
    }
    if (type.includes("subagent.")) return `subagent:${event.delegationId ?? event.childTaskId ?? event.eventId}`;
    if (type.includes("tool.call")) {
      return `tool:${event.taskId ?? ""}:${event.delegationId ?? ""}:${String(payload.tool_call_id ?? payload.tool ?? event.action ?? "tool")}`;
    }
    if (type.includes("skill.call")) {
      return `skill:${event.taskId ?? ""}:${event.delegationId ?? ""}:${String(payload.skill ?? event.action ?? "skill")}`;
    }
    if (type.includes("model.call")) return `model:${event.taskId ?? ""}:${event.delegationId ?? "main"}`;
    if (type.includes("task.")) return `task:${event.taskId ?? event.runId ?? "run"}`;
    return undefined;
  };
  const lastByLifecycle = new Map<string, string>();
  for (const event of events) {
    const key = lifecycleKey(event);
    if (key) lastByLifecycle.set(key, event.eventId);
  }
  const ongoing = (event: ActivityEvent): boolean => {
    const key = lifecycleKey(event);
    if (!key || lastByLifecycle.get(key) !== event.eventId) return false;
    return /(\.started|\.dispatched|approval\.required)$/.test(event.eventType.toLowerCase());
  };
  const latestFirst = [...events].sort((a, b) =>
    Number(ongoing(b)) - Number(ongoing(a))
    || Date.parse(b.occurredAt) - Date.parse(a.occurredAt)
    || (b.sequence ?? 0) - (a.sequence ?? 0));
  return (
    <div className="oa-general-events" aria-label="全部动态">
      {latestFirst.map((event, index) => {
        const tone = toneColor[eventTone(event)];
        const tool = eventTool(event);
        const summary = eventSummary(event);
        return (
          <div key={event.eventId} className="oa-activity-node">
            <span className="oa-activity-node-rail" aria-hidden>
              <span className="oa-activity-node-icon" style={{ background: tone.dot }}>
                <Icon name={eventIcon(event)} size={11} color="#fff" />
              </span>
              {index < latestFirst.length - 1 ? <span className="oa-activity-node-line" /> : null}
            </span>
            <div className="oa-activity-node-body">
              <div><strong>{eventTitle(event)}</strong></div>
              {tool ? <code>{tool}</code> : null}
              {summary ? <p>{summary}</p> : null}
              <time dateTime={event.occurredAt}>{formatClock(event.occurredAt)}</time>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function loadWorkerView(): WorkerViewMode {
  try {
    return window.localStorage.getItem(WORKER_VIEW_KEY) === "technical" ||
      window.localStorage.getItem(WORKER_VIEW_KEY) === "tech"
      ? "technical"
      : "business";
  } catch {
    return "business";
  }
}

function saveWorkerView(mode: WorkerViewMode): void {
  try {
    window.localStorage.setItem(WORKER_VIEW_KEY, mode);
  } catch {
    // Safari 隐私模式或禁用存储时仍可在当前会话切换。
  }
}

function runningFirst(items: ActivityNode[]): ActivityNode[] {
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => Number(Boolean(b.item.running)) - Number(Boolean(a.item.running)) || a.index - b.index)
    .map(({ item }) => item);
}

function GroupHeader({ group }: { group: ActivityGroup }) {
  if (!group.roleKey) {
    return <div className="oa-activity-group-label">{group.label}</div>;
  }
  const status = group.status === "running"
    ? { icon: "loader-2", color: "#c18a20", label: "运行中", spin: true }
    : group.status === "failed"
      ? { icon: "alert-triangle", color: color.danger, label: "异常/超时", spin: false }
      : { icon: "circle-check", color: color.good, label: "已汇报", spin: false };
  return (
    <div className="oa-activity-legacy-group-head">
      <Icon name="robot" size={13} color={color.brand} />
      <span>{group.label}</span>
      <small style={{ color: status.color }}>
        <Icon name={status.icon} size={11} color={status.color} spin={status.spin} />
        {status.label}
      </small>
    </div>
  );
}

function ActivityNodes({ groups }: { groups: ActivityGroup[] }) {
  if (!groups.length) {
    return (
      <div className="oa-activity-empty">
        <Icon name="timeline-event" size={24} color={color.textFaint} />
        <strong>暂无活动</strong>
        <span>任务开始后，运行里程碑会实时显示在这里。</span>
      </div>
    );
  }

  return (
    <div className="oa-all-activity-list">
      {groups.map((group, groupIndex) => {
        const items = runningFirst(group.items);
        return (
          <section key={`${group.roleKey ?? "main"}-${group.label}-${groupIndex}`} className="oa-activity-group">
            <GroupHeader group={group} />
            {items.map((node, nodeIndex) => {
              const tone = toneColor[node.tone];
              const last = nodeIndex === items.length - 1;
              return (
                <div key={node.id} className="oa-activity-node">
                  <span className="oa-activity-node-rail" aria-hidden>
                    <span className="oa-activity-node-icon" style={{ background: tone.dot }}>
                      <Icon name={node.icon} size={11} color="#fff" spin={node.running} />
                    </span>
                    {!last ? <span className="oa-activity-node-line" /> : null}
                  </span>
                  <div className="oa-activity-node-body">
                    <div>
                      <strong>{node.title}</strong>
                      {node.running ? <Icon name="loader-2" size={12} color={color.brand} spin /> : null}
                    </div>
                    {node.tool ? <code>{node.tool}</code> : null}
                    {node.detail ? <p>{node.detail}</p> : null}
                    <time>{node.time}</time>
                  </div>
                </div>
              );
            })}
          </section>
        );
      })}
    </div>
  );
}

/** 新批次契约尚未恢复的兼容展示；不会用时间间隔猜测轮次。 */
function LegacySubagentGroups({ groups }: { groups: ActivityGroup[] }) {
  const subagentGroups = groups.filter((group) => group.roleKey);
  if (!subagentGroups.length) {
    return (
      <div className="oa-activity-empty">
        <Icon name="affiliate" size={24} color={color.textFaint} />
        <strong>尚未派发子 Agent</strong>
        <span>主控派发任务后，这里会按轮次展示各子 Agent 的进展。</span>
      </div>
    );
  }
  return (
    <div className="oa-legacy-subagents">
      <div className="oa-legacy-note">
        <Icon name="history" size={13} color={color.textMuted} />
        历史活动未带派发批次，按原始角色分组展示。
      </div>
      <ActivityNodes groups={subagentGroups} />
    </div>
  );
}

export function ActivityRail({
  groups = [],
  generalEvents = [],
  rounds = [],
  hasMore = false,
  loadingMore = false,
  onLoadMore,
}: ActivityRailProps) {
  const ordered = useMemo(() => orderedRounds(rounds), [rounds]);
  const hasLegacySubagents = groups.some((group) => Boolean(group.roleKey));
  const hasSubagents = ordered.length > 0 || hasLegacySubagents;
  const [tab, setTab] = useState<RailTab>(() => (hasSubagents ? "subagents" : "all"));
  const [workerView, setWorkerView] = useState<WorkerViewMode>(() => loadWorkerView());
  const manualTabChoice = useRef(false);
  const previouslyHadSubagents = useRef(hasSubagents);

  useEffect(() => {
    if (!previouslyHadSubagents.current && hasSubagents && !manualTabChoice.current) setTab("subagents");
    previouslyHadSubagents.current = hasSubagents;
  }, [hasSubagents]);

  const switchTab = (next: RailTab) => {
    manualTabChoice.current = true;
    setTab(next);
  };

  const switchWorkerView = (mode: WorkerViewMode) => {
    setWorkerView(mode);
    saveWorkerView(mode);
  };

  return (
    <aside className="openops-activity-rail" aria-label="活动 · 调查时间线">
      <div className="oa-activity-title">
        <Icon name="timeline-event" size={16} color={color.brand} />
        <span>活动 · 调查时间线</span>
      </div>

      <div className="oa-activity-tabs" role="tablist" aria-label="活动类型">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "all"}
          className={tab === "all" ? "is-active" : ""}
          onClick={() => switchTab("all")}
        >
          全部动态
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "subagents"}
          className={tab === "subagents" ? "is-active" : ""}
          onClick={() => switchTab("subagents")}
        >
          子 Agent
          {hasSubagents ? <span className="oa-tab-count">{ordered.length || groups.filter((group) => group.roleKey).length}</span> : null}
        </button>
      </div>

      <div className="oa-activity-scroll" role="tabpanel">
        {tab === "all" ? (
          generalEvents.length ? <GeneralActivityEvents events={generalEvents} /> : <ActivityNodes groups={groups} />
        ) : ordered.length ? (
          <div className="oa-round-list">
            <div className="oa-round-list-title">
              <Icon name="affiliate" size={13} color={color.brand} />
              子 Agent 编排 · {ordered.length} 轮
            </div>
            {ordered.map((round, index) => (
              <RoundBlock
                key={round.id}
                round={round}
                defaultExpanded={index === 0 || round.status === "running" || round.counts.waitingApproval > 0}
                viewMode={workerView}
                onViewModeChange={switchWorkerView}
              />
            ))}
          </div>
        ) : (
          <LegacySubagentGroups groups={groups} />
        )}

        {hasMore && onLoadMore ? (
          <button
            type="button"
            className="oa-load-earlier"
            disabled={loadingMore}
            onClick={() => void onLoadMore()}
          >
            <Icon name={loadingMore ? "loader-2" : "history"} size={13} color={color.brand} spin={loadingMore} />
            {loadingMore ? "正在加载…" : "显示更早"}
          </button>
        ) : null}
      </div>
    </aside>
  );
}
