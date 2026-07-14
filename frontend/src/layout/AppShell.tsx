import { Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { color, radius } from "../theme/tokens";
import { Loading } from "../pages/states";
import { Icon } from "../ui";
import { Workbench } from "../workbench/Workbench";
import { Sidebar } from "./Sidebar";
import {
  createLatestWorkbenchTargetScheduler,
  parseWorkbenchTarget,
  shouldShowWorkbenchColdStart,
  shouldReuseWorkbench,
  type ResolvedWorkbenchSession,
  type WorkbenchTarget,
} from "./workbenchSession";

interface RetainedWorkbench {
  /** 每次真正切换会话递增；同一 Run 跨路由返回时保持不变。 */
  generation: number;
  target: WorkbenchTarget;
  session: ResolvedWorkbenchSession | null;
}

/**
 * 用户从设置/管理页首次进入对话时，latest-wins debounce 尚未创建 Workbench。
 * 这段稳定占位立即填满主区，避免 120ms 窗口及快速连点期间出现空白页。
 */
function WorkbenchColdStart() {
  return (
    <section
      data-testid="workbench-cold-start"
      role="status"
      aria-live="polite"
      style={{ flex: 1, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden", background: color.pageBg }}
    >
      <header style={{ flex: "0 0 56px", borderBottom: `1px solid ${color.border}`, background: color.surface, display: "flex", alignItems: "center", gap: 12, padding: "0 20px" }}>
        <span style={{ width: 132, height: 13, borderRadius: radius.pill, background: color.borderInner }} />
        <span style={{ width: 168, height: 24, borderRadius: radius.pill, background: color.neutralBg, border: `1px solid ${color.border}` }} />
      </header>
      <div style={{ flex: 1, minHeight: 0, display: "grid", placeItems: "center" }}>
        <div style={{ display: "inline-flex", alignItems: "center", gap: 8, color: color.textMuted, fontSize: 12.5 }}>
          <Icon name="loader-2" size={15} color={color.brand} spin />正在打开最后选择的会话…
        </div>
      </div>
    </section>
  );
}

function RetainedWorkbenchHost({
  entry,
  visible,
  routeTransitionPending,
  onSessionResolved,
}: {
  entry: RetainedWorkbench;
  visible: boolean;
  routeTransitionPending: boolean;
  onSessionResolved: (generation: number, session: ResolvedWorkbenchSession) => void;
}) {
  const reportSession = useCallback(
    (session: ResolvedWorkbenchSession) => onSessionResolved(entry.generation, session),
    [entry.generation, onSessionResolved],
  );

  return (
    <section
      className="oa-retained-workbench"
      aria-hidden={!visible}
      style={{
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        display: visible ? "flex" : "none",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <Workbench
        target={entry.target}
        sessionGeneration={entry.generation}
        visible={visible}
        routeTransitionPending={routeTransitionPending}
        onSessionResolved={reportSession}
      />
    </section>
  );
}

/** 鉴权区外壳：左导航 + 主区（Outlet）。初始化向导在壳外全屏。 */
export function AppShell() {
  const location = useLocation();
  const nextGeneration = useRef(1);
  const routeTarget = useMemo(
    () => parseWorkbenchTarget(location.pathname, location.search),
    [location.pathname, location.search],
  );
  const [retained, setRetained] = useState<RetainedWorkbench | null>(() => (
    routeTarget
      ? { generation: 0, target: routeTarget, session: null }
      : null
  ));
  const retainedRef = useRef(retained);
  retainedRef.current = retained;
  const targetScheduler = useMemo(() => createLatestWorkbenchTargetScheduler(), []);
  const [routeTransitionPending, setRouteTransitionPending] = useState(false);

  // URL 和 Sidebar 由 Router 立即更新；昂贵的 /state、SSE、Copilot 初始化采用
  // 120ms trailing latest-wins，只应用 burst 中的最后一个目标。
  useLayoutEffect(() => {
    if (!routeTarget) {
      targetScheduler.cancel();
      setRouteTransitionPending(false);
      return;
    }

    const current = retainedRef.current;
    const queued = targetScheduler.request(
      current?.target ?? null,
      current?.session ?? null,
      routeTarget,
      (latestTarget) => {
        setRetained((latestCurrent) => {
          // Session resolution may have completed during the debounce window.
          // Recheck canonical identity before starting any new work.
          if (
            latestCurrent &&
            shouldReuseWorkbench(latestCurrent.target, latestCurrent.session, latestTarget)
          ) return latestCurrent;

          return {
            generation: nextGeneration.current++,
            target: latestTarget,
            session: null,
          };
        });
        setRouteTransitionPending(false);
      },
    );
    setRouteTransitionPending(queued);
  }, [routeTarget, targetScheduler]);

  useEffect(() => () => targetScheduler.cancel(), [targetScheduler]);

  const handleSessionResolved = useCallback((generation: number, session: ResolvedWorkbenchSession) => {
    setRetained((current) => {
      if (!current || current.generation !== generation) return current;
      if (
        current.session?.runId === session.runId &&
        current.session.instanceId === session.instanceId &&
        current.session.runStatus === session.runStatus
      ) return current;
      return { ...current, session };
    });
  }, []);

  const chatVisible = routeTarget !== null;
  const showColdStart = shouldShowWorkbenchColdStart(routeTarget, retained !== null);

  return (
    <div style={{ height: "100vh", display: "flex", background: color.pageBg, color: color.ink, overflow: "hidden" }}>
      <Sidebar />
      <main style={{ flex: 1, minWidth: 0, height: "100vh", display: "flex", flexDirection: "column", overflow: "hidden", background: color.pageBg }}>
        {retained ? (
          <RetainedWorkbenchHost
            entry={retained}
            visible={chatVisible}
            routeTransitionPending={routeTransitionPending}
            onSessionResolved={handleSessionResolved}
          />
        ) : showColdStart ? <WorkbenchColdStart /> : null}
        {/* 低频页面的懒加载只暂停 Outlet，不影响后台运行中的 Workbench。 */}
        <Suspense fallback={<Loading />}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}
