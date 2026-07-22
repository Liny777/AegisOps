import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import type { ActivityGroup, ActivityNode, DispatchRound, RcaCardData } from "../lib/api/types";
import { color, toneColor } from "../theme/tokens";
import { Icon } from "../ui";
import { DiagnosisTimeline, DiagnosisTimelineSkeleton, type RcaCardAction } from "./DiagnosisTimeline";
import { RoundBlock } from "./activity/RoundBlock";
import { orderedRounds } from "./activity/visuals";
import "./activity/ActivityRail.css";

type RailTab = "rca" | "subagents";

export interface ActivityRailProps {
  /** 旧投影兼容入口；子 Agent tab 的 LegacySubagentGroups 渲染用。 */
  groups?: ActivityGroup[];
  /** 后端批次 ID 投影出的权威轮次；同角色的多次 delegation 仍保持独立。 */
  rounds?: DispatchRound[];
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void | Promise<void>;
  /** 诊断面板数据（openops.rca.updated / /state 恢复）；无数据时诊断 tab 显示空态骨架。 */
  rca?: RcaCardData;
  /** 任务运行中且未闭环：时间线 active 步与相位 chip 脉冲。 */
  rcaLive?: boolean;
  /** false = 时间线按钮禁用（任务运行中 / 程序化发送挂起）。 */
  rcaActionsEnabled?: boolean;
  /** 时间线按钮回调（以用户身份发消息）；缺省（run closed）时 footer 不渲染。 */
  onRcaAction?: (action: RcaCardAction) => void;
  /** 用户拖拽后的面板宽度（px）；null/缺省 = CSS 默认 clamp(420px, 33.333%, 760px)（内容区 1/3）。 */
  width?: number | null;
  /** 左缘拖拽手柄回调；缺省不渲染手柄。 */
  onResize?: (width: number) => void;
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

/** 诊断空态：五步 waiting 骨架时间线 + 引导文案（对齐 .oa-activity-empty 风格）。
 *  模型漏调上报工具时就停在这里——不做任何服务端/前端伪造。 */
function RcaEmptyState() {
  return (
    <div className="oa-activity-empty oa-rca-empty" data-testid="rca-empty">
      <DiagnosisTimelineSkeleton />
      <Icon name="report-search" size={24} color={color.textFaint} />
      <strong>诊断尚未开始</strong>
      <span>Agent 按五步法（诊断范围 → 证据收集 → 假设生成 → 验证 → 根因报告）推进时，这里会实时点亮当前步骤，每一步都可展开查看证据与结论。</span>
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
  // 诊断优先：有诊断数据（恢复/mock）或尚无子 Agent 都落在诊断 tab（空态骨架也比空列表有引导性）。
  const [tab, setTab] = useState<RailTab>(() => (rca || !hasSubagents ? "rca" : "subagents"));
  const [resizing, setResizing] = useState(false);
  const manualTabChoice = useRef(false);
  const previouslyHadRca = useRef(Boolean(rca));
  const railRef = useRef<HTMLElement | null>(null);
  // 未读基线：挂载时已有的 revision 不算未读，只对之后的更新亮点。
  const [seenRcaRevision, setSeenRcaRevision] = useState<number>(() => rca?.revision ?? 0);

  // 诊断数据从无到有且用户未手动选过 tab：自动切「诊断」。
  useEffect(() => {
    if (!previouslyHadRca.current && rca && !manualTabChoice.current) setTab("rca");
    previouslyHadRca.current = Boolean(rca);
  }, [rca]);

  // 子 Agent 轮次从无到有且始终没有诊断数据：自动切「子 Agent」。覆盖「数据晚于挂载」
  // 的深链/刷新路径——key=runId 首帧 rounds 恒为空、初值落在诊断空骨架，事件拉回后若
  // 没有这条 effect,历史子 Agent 活动会藏在未选中 tab 后。诊断优先:rca 在场不切。
  const previouslyHadSubagents = useRef(hasSubagents);
  useEffect(() => {
    if (!previouslyHadSubagents.current && hasSubagents && !manualTabChoice.current && !rca && tab === "rca") {
      setTab("subagents");
    }
    previouslyHadSubagents.current = hasSubagents;
  }, [hasSubagents, tab, rca]);

  // 停在诊断 tab 即视为已读；离开后 revision 变化才亮未读徽标。基线记「最后已读的
  // revision」而非 Math.max 高水位：同 run 第二次诊断任务 revision 从 1 重计，高水位
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
          诊断
          {rcaUnread ? <span className="oa-tab-dot" aria-label="有新的诊断更新" /> : null}
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
            ? <DiagnosisTimeline rca={rca} live={rcaLive} actionsEnabled={rcaActionsEnabled} onAction={onRcaAction} />
            : <RcaEmptyState />
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
