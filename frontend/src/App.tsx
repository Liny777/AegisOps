import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import type { ReactNode } from "react";
import { AppProvider, useApp } from "./lib/appState";
import { AppShell } from "./layout/AppShell";
import { Workbench } from "./workbench/Workbench";
import { SettingsPage } from "./settings/SettingsPage";
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

/** /agent-runs/:id → 恢复对话（P1 后按 30.7 补 run owner 反查）。 */
function ChatFromRun() {
  const { id } = useParams();
  return <Navigate to={`/agent-teams/${id}/chat`} replace />;
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
            <Route path="/admin" element={<RoleGuard><Navigate to="/admin/templates" replace /></RoleGuard>} />
            <Route path="/admin/:page" element={<RoleGuard><AdminConsole /></RoleGuard>} />
          </Route>

          <Route path="/agent-runs/:id" element={<ChatFromRun />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AppProvider>
  );
}
