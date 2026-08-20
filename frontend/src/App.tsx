import { lazy, Suspense, useEffect, useRef } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import type { ReactNode } from "react";
import { AppProvider, useApp } from "./lib/appState";
import { AppShell } from "./layout/AppShell";
import { NotWhitelisted, Forbidden, Loading, NotFound } from "./pages/states";
import { api } from "./lib/api";
import { captureAutoQuestion, peekAutoQuestion } from "./lib/autosend";
import { clearReturnPath, consumeReturnPath } from "./lib/loginReturn";
import { captureAlertContext } from "./lib/alertEntry";
import { studioRoutes } from "./studio/entry";  // 垂直切片自注册路由（懒加载在切片内部）
import { alertsRoutes } from "./alerts/entry";  // 7x24 告警接管切片（同律：入口只有 lazy 与常量）

// 外链 ?q= / 告警直达三参捕获必须先于首个 fetch 的 401→IAM 重定向（b5c5c79 时序教训）——模块加载即执行。
// 带这些实参进来是**新外部意图**：先废弃上次未完成登录往返残留的深链回位存根，否则本次 ?q=
// 会被旧 run 劫持吞问题（本页面若随后 401，captureReturnPath 会重新存当前路径，清早了无损）。
if (typeof window !== "undefined" && /[?&](q|entry_source)=/.test(window.location.search)) clearReturnPath();
captureAutoQuestion();
captureAlertContext();

// 路由级 code splitting（S1）：对话主链路（Workbench/AppShell）留主包，
// 低频面（设置/管理台/初始化向导）按需加载——主 chunk 曾 >500KB。
const SettingsPage = lazy(() => import("./settings/SettingsPage").then((m) => ({ default: m.SettingsPage })));
const SettingsHome = lazy(() => import("./settings/SettingsHome").then((m) => ({ default: m.SettingsHome })));
const OmodelPage = lazy(() => import("./settings/OmodelPage").then((m) => ({ default: m.OmodelPage })));
const AdminConsole = lazy(() => import("./admin/AdminConsole").then((m) => ({ default: m.AdminConsole })));
const InitWizard = lazy(() => import("./init/InitWizard").then((m) => ({ default: m.InitWizard })));
const ProductIntro = lazy(() => import("./pages/intro/ProductIntro").then((m) => ({ default: m.ProductIntro })));

/** 首页分流：按 me 落到最近实例对话 / 初始化 / 白名单拦截。
 *  外链 ?q= 三态：无权限→引导页（问题保留）；未初始化→向导（完成后自动发送）；
 *  已初始化→为该问题专门新建 run（防污染既有会话）再进对话。 */
function HomeRedirect() {
  const { me, loading, currentAgentId } = useApp();
  // IAM 往返深链回位（lib/loginReturn.ts）：懒 consume + ref 缓存——StrictMode 双 render 只
  // consume 一次且两次结果一致；放在 loading 判定之后才取，getMe 再次 401（AUTH_REDIRECT 保持
  // Loading）时存储未被消费，下一轮登录成功仍可回位。
  const returnPathRef = useRef<string | null | undefined>(undefined);
  if (loading || !me) return <Loading />;
  if (!me.whitelisted) return <Navigate to="/not-whitelisted" replace />;
  if (returnPathRef.current === undefined) returnPathRef.current = consumeReturnPath();
  // 回位优先于 ?q= 分流：语义等价于用户当初直接打开该深链（直达 /agent-runs/:id 本就不经过
  // has_instances 检查）；ExternalJump 会新建 run，对显式指向既有 run 的深链是错误行为。
  if (returnPathRef.current) return <Navigate to={returnPathRef.current} replace />;
  if (!me.has_instances) return <Navigate to="/init" replace />;
  const agentId = me.recent_instance_id ?? currentAgentId;
  if (peekAutoQuestion()) return <ExternalJump instanceId={agentId} />;
  return <Navigate to={`/agent-teams/${agentId}/chat`} replace />;
}

/** 外链落地跳板：为带入的问题**专门新建 run**（老项目 82e40e6 教训：对"最近会话"直发会污染
 *  旧会话/发错线程），成功进 /agent-runs/:id 由 Workbench 自动发送；失败兜底常规 chat 入口
 *  （Workbench 的 ensureRun 路径仍会消费 pending 自动发送，只是可能落在既有会话）。 */
let externalJumpRun: Promise<string> | null = null; // 模块级单飞：StrictMode 双跑 effect 不重复建 run
function ExternalJump({ instanceId }: { instanceId: string }) {
  const nav = useNavigate();
  useEffect(() => {
    let alive = true;
    if (!externalJumpRun) {
      console.info("[autosend] creating dedicated run for external question");
      externalJumpRun = api.createRun(instanceId);
    }
    externalJumpRun
      .then((rid) => { if (alive) nav(`/agent-runs/${rid}`, { replace: true }); })
      .catch((e) => {
        console.error("[autosend] createRun failed, fallback to chat entry:", e);
        externalJumpRun = null; // 失败允许下次重试
        if (alive) nav(`/agent-teams/${instanceId}/chat`, { replace: true });
      });
    return () => { alive = false; };
  }, [instanceId, nav]);
  return <Loading />;
}

/** /agent-runs/:runId/chat 尾段归一：workbenchSession 已把两种形态解析为同一 run（不闪冷启动），
 *  这里只负责把地址栏 replace 成规范形态（e2e 的 URL 断言习惯 `$` 锚定，脏尾段不能长期存在）。 */
function RunChatAlias() {
  const { runId = "" } = useParams();
  const { search } = useLocation();
  return <Navigate to={`/agent-runs/${encodeURIComponent(runId)}${search}`} replace />;
}

function WhitelistGuard({ children }: { children: ReactNode }) {
  const { me, loading } = useApp();
  if (loading || !me) return <Loading />;
  if (!me.whitelisted) return <Navigate to="/not-whitelisted" replace />;
  return <>{children}</>;
}

/** 初始化向导守卫：白名单 + **已有 Agent 就跳工作台**（裸 /init：刷新/书签/向导完成后 URL 残留，
 * 只在真无实例时才显示向导，避免老用户重复走初始化）。例外：显式带 `?new=1`（清单页「新建
 * Agent」按钮）时放行——老用户主动建第二个实例是合法动作，不能被弹回。 */
function InitGuard({ children }: { children: ReactNode }) {
  const { me, loading, currentAgentId } = useApp();
  const [sp] = useSearchParams();
  if (loading || !me) return <Loading />;
  if (!me.whitelisted) return <Navigate to="/not-whitelisted" replace />;
  if (me.has_instances && sp.get("new") !== "1")
    return <Navigate to={`/agent-teams/${me.recent_instance_id ?? currentAgentId}/chat`} replace />;
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
      <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, "") || "/"}>
        <Suspense fallback={<Loading />}>
          <Routes>
            <Route path="/" element={<HomeRedirect />} />
            <Route path="/not-whitelisted" element={<NotWhitelisted />} />
            {/* 产品介绍页：守卫外全屏（无需白名单/初始化；未登录仍走全局 SSO） */}
            <Route path="/intro" element={<ProductIntro />} />
            <Route path="/init" element={<InitGuard><InitWizard /></InitGuard>} />
            {/* 编辑向导（清单页「编辑」入口）：复用 InitWizard 的编辑态，全屏壳外；
                只需白名单（编辑者必然已有实例，套 InitGuard 会被弹走） */}
            <Route path="/agent-teams/:instanceId/edit" element={<WhitelistGuard><InitWizard /></WhitelistGuard>} />

            {/* 鉴权区外壳（左导航 + 主区） */}
            <Route element={<WhitelistGuard><AppShell /></WhitelistGuard>}>
              {/* 对话工作台由 AppShell 常驻托管；叶路由只负责参与匹配。 */}
              <Route path="/agent-teams/:instanceId/chat" element={null} />
              <Route path="/agent-teams/:instanceId/settings" element={<SettingsPage />} />
              {/* 全部 Agent 清单（picker「全部 Agents」入口）：同一 SettingsPage，无 instanceId → 列表态 */}
              <Route path="/agents" element={<SettingsPage />} />
              {/* 设置（侧栏「设置」入口）：用户级配置二级菜单，V1 仅 OModel 占位（禁用） */}
              <Route path="/settings/:section?" element={<SettingsHome />} />
              <Route path="/omodel" element={<OmodelPage />} />
              {/* 按 run 恢复（30.7）：Workbench 用 :runId 直接 GET /state，不新建实例 */}
              <Route path="/agent-runs/:runId" element={null} />
              {/* 旧版部署/外部拼接的 /chat 尾段容错：workbenchSession 正则已等价解析同一 run
                  （零闪动），此处仅 replace 归一 URL，避免脏尾段被复制传播 */}
              <Route path="/agent-runs/:runId/chat" element={<RunChatAlias />} />
              {/* Agent Studio 切片自注册路由（/replay/*）：路径与懒加载都由切片决定 */}
              {studioRoutes}
              {/* 7x24 告警接管切片自注册路由（/alerts/*） */}
              {alertsRoutes}
              <Route path="/admin" element={<RoleGuard><Navigate to="/admin/templates" replace /></RoleGuard>} />
              <Route path="/admin/:page" element={<RoleGuard><AdminConsole /></RoleGuard>} />
            </Route>

            {/* 未知路径显式 404（曾静默弹回首页，把 WeLink 深链故障掩成「莫名到了 chat」） */}
            <Route path="*" element={<NotFound />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </AppProvider>
  );
}
