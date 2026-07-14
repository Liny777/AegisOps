import { Suspense, useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { color } from "../theme/tokens";
import { Loading } from "../pages/states";
import { Workbench } from "../workbench/Workbench";
import { Sidebar } from "./Sidebar";
import {
  parseWorkbenchTarget,
  shouldReuseWorkbench,
  type ResolvedWorkbenchSession,
  type WorkbenchTarget,
} from "./workbenchSession";

interface RetainedWorkbench {
  /** 每次真正切换会话递增；同一 Run 跨路由返回时保持不变。 */
  hostId: number;
  target: WorkbenchTarget;
  session: ResolvedWorkbenchSession | null;
}

function RetainedWorkbenchHost({
  entry,
  visible,
  onSessionResolved,
}: {
  entry: RetainedWorkbench;
  visible: boolean;
  onSessionResolved: (hostId: number, session: ResolvedWorkbenchSession) => void;
}) {
  const reportSession = useCallback(
    (session: ResolvedWorkbenchSession) => onSessionResolved(entry.hostId, session),
    [entry.hostId, onSessionResolved],
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
        visible={visible}
        onSessionResolved={reportSession}
      />
    </section>
  );
}

/** 鉴权区外壳：左导航 + 主区（Outlet）。初始化向导在壳外全屏。 */
export function AppShell() {
  const location = useLocation();
  const nextHostId = useRef(1);
  const routeTarget = useMemo(
    () => parseWorkbenchTarget(location.pathname, location.search),
    [location.pathname, location.search],
  );
  const [retained, setRetained] = useState<RetainedWorkbench | null>(() => (
    routeTarget
      ? { hostId: 0, target: routeTarget, session: null }
      : null
  ));

  // 只在进入一个 chat 历史项时决定是否切换。session 状态在当前页面内变为 closed
  // 不应立刻重建；离开后再次进入通用 Agent chat 才由 shouldReuseWorkbench 判定。
  useLayoutEffect(() => {
    if (!routeTarget) return;
    setRetained((current) => {
      if (current && shouldReuseWorkbench(current.target, current.session, routeTarget)) return current;
      return {
        hostId: nextHostId.current++,
        target: routeTarget,
        session: null,
      };
    });
  }, [location.key, routeTarget]);

  const handleSessionResolved = useCallback((hostId: number, session: ResolvedWorkbenchSession) => {
    setRetained((current) => {
      if (!current || current.hostId !== hostId) return current;
      if (
        current.session?.runId === session.runId &&
        current.session.instanceId === session.instanceId &&
        current.session.runStatus === session.runStatus
      ) return current;
      return { ...current, session };
    });
  }, []);

  const chatVisible = routeTarget !== null;

  return (
    <div style={{ height: "100vh", display: "flex", background: color.pageBg, color: color.ink, overflow: "hidden" }}>
      <Sidebar />
      <main style={{ flex: 1, minWidth: 0, height: "100vh", display: "flex", flexDirection: "column", overflow: "hidden", background: color.pageBg }}>
        {retained ? (
          <RetainedWorkbenchHost
            key={retained.hostId}
            entry={retained}
            visible={chatVisible}
            onSessionResolved={handleSessionResolved}
          />
        ) : null}
        {/* 低频页面的懒加载只暂停 Outlet，不影响后台运行中的 Workbench。 */}
        <Suspense fallback={<Loading />}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}
