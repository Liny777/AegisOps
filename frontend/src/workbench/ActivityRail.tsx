import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import type { ActivityEvent, ActivityGroup, ActivityNode, DispatchRound, RcaCardData } from "../lib/api/types";
import { color, radius, toneColor } from "../theme/tokens";
import { Icon } from "../ui";
import { RcaCard, type RcaCardAction } from "./RcaCard";
import { RoundBlock, type WorkerViewMode } from "./activity/RoundBlock";
import { eventIcon, eventSummary, eventTitle, eventTone, eventTool, isOpsDiagnosticEvent } from "./activity/eventPresentation";
import { formatClock, orderedRounds } from "./activity/visuals";
import "./activity/ActivityRail.css";

const WORKER_VIEW_KEY = "openops.workerView";

type RailTab = "rca" | "all" | "subagents";

/** 五步法固定骨架（与后端 rca_contract.derive_steps 的 label 同源）；空态骨架用。 */
const RCA_STEP_LABELS = ["范围", "证据", "假设", "验证", "结论"] as const;

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
  /** 定界面板数据（openops.rca.updated / /state 恢复）；无数据时定界 tab 显示空态骨架。 */
  rca?: RcaCardData;
  /** 任务运行中且未闭环：卡片 active 步与相位 chip 脉冲。 */
  rcaLive?: boolean;
  /** false = 卡片按钮禁用（任务运行中 / 程序化发送挂起）。 */
  rcaActionsEnabled?: boolean;
  /** 卡片按钮回调（以用户身份发消息）；缺省（run closed）时卡片 footer 不渲染。 */
  onRcaAction?: (action: RcaCardAction) => void;
  /** 用户拖拽后的面板宽度（px）；null/缺省 = CSS 默认 clamp(420px, 42vw, 760px)。 */
  width?: number | null;
  /** 左缘拖拽手柄回调；缺省不渲染手柄。 */
  onResize?: (width: number) => void;
}

function GeneralActivityEvents({ events: rawEvents }: { events: ActivityEvent[] }) {
  // 装配缺口诊断事件（tool.skipped / skill.skipped）不进「全部动态」：上一轮只在业务时间线
  // 剔除，主 Agent 发的 tool.skipped 走的是本组件（generalEvents，无任何过滤），照样糊在
  // 用户眼前。按事件类型滤（而非消息内容），历史库里的旧事件同样消失；运维排障走
  // run 审计回放页与审计表 payload.tools，那两处不受影响。
  const events = rawEvents.filter((event) => !isOpsDiagnosticEvent(event));
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

/** 定界空态：五步全灰骨架 stepper + 引导文案（对齐 .oa-activity-empty 风格）。
 *  模型漏调上报工具时就停在这里——不做任何服务端/前端伪造。 */
function RcaEmptyState() {
  return (
    <div className="oa-activity-empty oa-rca-empty" data-testid="rca-empty">
      <div className="oa-rca-empty-stepper" aria-hidden>
        {RCA_STEP_LABELS.map((label, index) => (
          <div key={label} style={{ display: "flex", alignItems: "center", flex: index < RCA_STEP_LABELS.length - 1 ? 1 : "0 0 auto" }}>
            <div className="oa-rca-empty-step">
              <span>{index + 1}</span>{label}
            </div>
            {index < RCA_STEP_LABELS.length - 1 ? <div className="oa-rca-empty-line" /> : null}
          </div>
        ))}
      </div>
      <Icon name="report-search" size={24} color={color.textFaint} />
      <strong>定界尚未开始</strong>
      <span>Agent 按五步法（范围 → 证据 → 假设 → 验证 → 结论）推进定界时，这里会实时点亮当前步骤，并沉淀成可交互的定界卡片。</span>
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
  rca,
  rcaLive = false,
  rcaActionsEnabled = true,
  onRcaAction,
  width = null,
  onResize,
}: ActivityRailProps) {
  const ordered = useMemo(() => orderedRounds(rounds), [rounds]);
  const hasLegacySubagents = groups.some((group) => Boolean(group.roleKey));
  const hasSubagents = ordered.length > 0 || hasLegacySubagents;
  // 定界优先：挂载即有定界数据（恢复/mock）直接落在定界 tab。
  const [tab, setTab] = useState<RailTab>(() => (rca ? "rca" : hasSubagents ? "subagents" : "all"));
  const [workerView, setWorkerView] = useState<WorkerViewMode>(() => loadWorkerView());
  const [resizing, setResizing] = useState(false);
  const manualTabChoice = useRef(false);
  const previouslyHadSubagents = useRef(hasSubagents);
  const previouslyHadRca = useRef(Boolean(rca));
  const railRef = useRef<HTMLElement | null>(null);
  // 未读基线：挂载时已有的 revision 不算未读，只对之后的更新亮点。
  const [seenRcaRevision, setSeenRcaRevision] = useState<number>(() => rca?.revision ?? 0);

  // 定界数据从无到有且用户未手动选过 tab：自动切「定界」。
  useEffect(() => {
    if (!previouslyHadRca.current && rca && !manualTabChoice.current) setTab("rca");
    previouslyHadRca.current = Boolean(rca);
  }, [rca]);

  // 子 Agent 自动切换加 tab==="all" 前置：定界优先，不互抢。!rca 防同一 commit 内
  // 定界与子 Agent 同时从无到有时（mock 初始 hydrate 即如此）两个 effect 先后 setTab 互踩。
  useEffect(() => {
    if (!previouslyHadSubagents.current && hasSubagents && !manualTabChoice.current && tab === "all" && !rca) {
      setTab("subagents");
    }
    previouslyHadSubagents.current = hasSubagents;
  }, [hasSubagents, tab, rca]);

  // 停在定界 tab 即视为已读；离开后 revision 变化才亮未读徽标。基线记「最后已读的
  // revision」而非 Math.max 高水位：同 run 第二次定界任务 revision 从 1 重计，高水位
  // 会把新任务的全部更新永久判成已读。Workbench 守卫保证展示值只前进或换任务重置，
  // 不会来回摆，因此「不等即未读」成立。
  const rcaRevision = rca?.revision ?? 0;
  useEffect(() => {
    if (tab === "rca") setSeenRcaRevision(rcaRevision);
  }, [tab, rcaRevision]);
  const rcaUnread = Boolean(rca) && tab !== "rca" && rcaRevision !== seenRcaRevision;

  const switchTab = (next: RailTab) => {
    manualTabChoice.current = true;
    setTab(next);
  };

  const switchWorkerView = (mode: WorkerViewMode) => {
    setWorkerView(mode);
    saveWorkerView(mode);
  };

  // 左缘拖拽调宽：宽度 = 面板右缘 - 指针位置；clamp 交给 Workbench（与持久化一处收口）。
  const startResize = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!onResize) return;
    event.preventDefault();
    const right = railRef.current?.getBoundingClientRect().right ?? window.innerWidth;
    setResizing(true);
    const onMove = (moveEvent: PointerEvent) => onResize(right - moveEvent.clientX);
    const onUp = () => {
      setResizing(false);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, [onResize]);

  return (
    <aside
      ref={railRef}
      className={`openops-activity-rail${resizing ? " is-resizing" : ""}`}
      aria-label="活动 · 调查时间线"
      style={width != null ? { "--oa-rail-width": `${width}px` } as CSSProperties : undefined}
    >
      {onResize ? (
        <div
          className="oa-rail-resize-handle"
          role="separator"
          aria-orientation="vertical"
          aria-label="拖拽调整面板宽度"
          onPointerDown={startResize}
        />
      ) : null}
      <div className="oa-activity-title">
        <Icon name="timeline-event" size={16} color={color.brand} />
        <span>活动 · 调查时间线</span>
      </div>

      <div className="oa-activity-tabs" role="tablist" aria-label="活动类型">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "rca"}
          className={tab === "rca" ? "is-active" : ""}
          onClick={() => switchTab("rca")}
        >
          定界
          {rcaUnread ? <span className="oa-tab-dot" aria-label="有新的定界更新" /> : null}
        </button>
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
        {tab === "rca" ? (
          rca
            ? <RcaCard rca={rca} live={rcaLive} actionsEnabled={rcaActionsEnabled} onAction={onRcaAction} />
            : <RcaEmptyState />
        ) : tab === "all" ? (
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
