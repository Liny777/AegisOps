import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";
import { AppProvider, useApp } from "./lib/appState";
import { AppShell } from "./layout/AppShell";
import { Workbench } from "./workbench/Workbench";
import { SettingsPage } from "./settings/SettingsPage";
import { SettingsHome } from "./settings/SettingsHome";
import { AdminConsole } from "./admin/AdminConsole";
import { InitWizard } from "./init/InitWizard";
import { NotWhitelisted, Forbidden, Loading } from "./pages/states";

/** 首页分流：按 me 落到最近实例对话 / 初始化 / 白名单拦截。 */
function HomeRedirect() {
  const { me, loading, currentAgentId } = useApp();
  if (loading || !me) return <Loading />;
  if (!me.whitelisted) return <Navigate to="/not-whitelisted" replace />;
  if (!me.has_instances) return <Navigate to="/init" replace />;
  return <Navigate to={`/agent-teams/${me.recent_instance_id ?? currentAgentId}/chat`} replace />;
}

function WhitelistGuard({ children }: { children: ReactNode }) {
  const { me, loading } = useApp();
  if (loading || !me) return <Loading />;
  if (!me.whitelisted) return <Navigate to="/not-whitelisted" replace />;
  return <>{children}</>;
}

function RoleGuard({ children }: { children: ReactNode }) {
  const { me, loading } = useApp();
  if (loading || !me) return <Loading />;
  if (!me.whitelisted) return <Navigate to="/not-whitelisted" replace />;
  if (me.role !== "platform_admin") return <Forbidden />;
  return <>{children}</>;
}

export default function App() {
  return (
    <AppProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<HomeRedirect />} />
          <Route path="/not-whitelisted" element={<NotWhitelisted />} />
          <Route path="/init" element={<WhitelistGuard><InitWizard /></WhitelistGuard>} />

          {/* 鉴权区外壳（左导航 + 主区） */}
          <Route element={<WhitelistGuard><AppShell /></WhitelistGuard>}>
            <Route path="/agent-teams/:instanceId/chat" element={<Workbench />} />
            <Route path="/agent-teams/:instanceId/settings" element={<SettingsPage />} />
            {/* 全部 Agent 清单（picker「全部 Agents」入口）：同一 SettingsPage，无 instanceId → 列表态 */}
            <Route path="/agents" element={<SettingsPage />} />
            {/* 设置（侧栏「设置」入口）：用户级配置二级菜单，V1 仅 OModel 占位（禁用） */}
            <Route path="/settings" element={<SettingsHome />} />
            {/* 按 run 恢复（30.7）：Workbench 用 :runId 直接 GET /state，不新建实例 */}
            <Route path="/agent-runs/:runId" element={<Workbench />} />
            <Route path="/admin" element={<RoleGuard><Navigate to="/admin/templates" replace /></RoleGuard>} />
            <Route path="/admin/:page" element={<RoleGuard><AdminConsole /></RoleGuard>} />
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AppProvider>
  );
}
