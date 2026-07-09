import { Outlet } from "react-router-dom";
import { color } from "../theme/tokens";
import { Sidebar } from "./Sidebar";

/** 鉴权区外壳：左导航 + 主区（Outlet）。初始化向导在壳外全屏。 */
export function AppShell() {
  return (
    <div style={{ height: "100vh", display: "flex", background: color.pageBg, color: color.ink, overflow: "hidden" }}>
      <Sidebar />
      <main style={{ flex: 1, minWidth: 0, height: "100vh", display: "flex", flexDirection: "column", overflow: "hidden", background: color.pageBg }}>
        <Outlet />
      </main>
    </div>
  );
}
