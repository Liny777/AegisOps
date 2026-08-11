import { useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { color, radius, shadow } from "../theme/tokens";
import { Icon, Interactive } from "../ui";
import { useApp } from "../lib/appState";
import { api } from "../lib/api";
import type { Conversation } from "../lib/api/types";
import type { NavItem } from "./navTypes";
import { STUDIO_ADMIN_NAV, STUDIO_USER_NAV, studioActiveKey } from "../studio/entry";
import { ALERTS_USER_NAV, alertsActiveKey } from "../alerts/entry";

const EXPANDED = 248;
const COLLAPSED = 60;



function NavRow({
  item,
  active,
  showText,
  onClick,
}: {
  item: NavItem;
  active: boolean;
  showText: boolean;
  onClick: () => void;
}) {
  return (
    <Interactive
      title={item.label}
      onClick={item.locked ? undefined : onClick}
      baseStyle={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "9px 11px",
        borderRadius: radius.lg,
        cursor: item.locked ? "not-allowed" : "pointer",
        fontSize: 13.5,
        fontWeight: active ? 700 : 500,
        color: active ? color.brand : item.locked ? color.textFaint : color.textNav,
        background: active ? color.brandTintBg : "transparent",
      }}
      hoverStyle={item.locked || active ? {} : { background: "#e9ecf1" }}
    >
      <Icon name={item.icon} size={18} />
      {showText ? <span style={{ flex: 1 }}>{item.label}</span> : null}
      {showText && item.locked ? <Icon name="lock" size={13} color={color.textFainter} /> : null}
    </Interactive>
  );
}

export function Sidebar() {
  const nav = useNavigate();
  const loc = useLocation();
  // 当前会话 id 取自 query（导航时带的 ?run_id=）：路径上不一定含 run_id（如 /chat?run_id=xxx），
  // 故会话列表高亮 = 路径命中 ∪ query 命中（见下方 shownConvs 的 cur）。
  const [searchParams] = useSearchParams();
  const activeRunId = searchParams.get("run_id");
  const {
    me,
    agents,
    conversations: convs,
    conversationsLoading,
    currentAgentId,
    setCurrentAgentId,
  } = useApp();
  const [collapsed, setCollapsed] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  const isAdmin = loc.pathname.startsWith("/admin");
  const isPlatformAdmin = me?.role === "platform_admin"; // 管理台入口仅平台管理员可见（真身份，非路由）
  const showText = !collapsed;
  const currentAgent = agents.find((a) => a.instance_id === currentAgentId) ?? agents[0];

  // 历史会话由 AppProvider single-flight 拉取；路由切换只改变高亮，不再重发全量列表请求。
  // 显示时按当前 Agent 过滤（会话绑定实例，切 Agent 列表跟着切）再截断。
  const shownConvs = convs
    .filter((c) => !currentAgentId || !c.instance_id || c.instance_id === currentAgentId)
    .slice(0, 20);

  const deleteConv = async (c: Conversation) => {
    if (!confirm(`删除会话「${c.title}」？`)) return;
    try {
      await api.deleteRun(c.id);
      // 删的是当前打开的会话 → 回当前 Agent 的对话入口（ensureRun 会开新会话）
      if (loc.pathname.includes(c.id) && currentAgentId) nav(`/agent-teams/${currentAgentId}/chat`);
    } catch (e) {
      alert(`删除失败：${(e as Error).message}`);
    }
  };

  // 32 号导航项：会话（主操作）+ 知识 / 插件 / 自动化 / 本体（V1 除插件外置灰，无对应页面）。
  // 插件=当前 Agent 的配置视图（Skill/MCP/模型/提示词，原「设置」内容迁此）；「设置」改挂 /settings（OModel 占位）。
  const userNav: NavItem[] = [
    ALERTS_USER_NAV,                       // 告警清单（7x24 告警接管切片自带；位置归 core，内容归切片）
    { key: "knowledge", label: "知识", icon: "book-2", locked: true },
    { key: "plugins", label: "插件", icon: "puzzle", to: currentAgentId ? `/agent-teams/${currentAgentId}/settings` : undefined, locked: !currentAgentId },
    { key: "automation", label: "自动化", icon: "robot", locked: true },
    STUDIO_USER_NAV,                       // 回放（Agent Studio 切片自带；位置归 core，内容归切片）
    { key: "ontology",  label: "OModel", icon: "sitemap", to: "/omodel" },  // 2026-08-10 从设置迁出为一级页
  ];

  const newChat = async () => {
    if (!currentAgentId) return nav("/init");
    try {
      const runId = await api.createRun(currentAgentId);
      nav(`/agent-teams/${currentAgentId}/chat?run_id=${encodeURIComponent(runId)}`);
    } catch (e) {
      // 容量满（SANDBOX_CAPACITY_FULL/429）等错误直接提示用户——原静默 nav 会把繁忙提醒藏进 Workbench 二次尝试
      alert((e as Error).message);
    }
  };
  // B7a IA（30.6 2026-07-09 拍板）：Tool 标注/资产治理并入模板管理 drill；新增模型资产
  const adminNav: NavItem[] = [
    { key: "templates", label: "模板管理", icon: "layout-grid", to: "/admin/templates" },
    { key: "model-assets", label: "模型资产", icon: "cpu", to: "/admin/model-assets" },
    { key: "model-templates", label: "模型模板", icon: "versions", to: "/admin/model-templates" },
    { key: "skills", label: "Skill 基线", icon: "file-code", to: "/admin/skills" },
    { key: "users", label: "用户与白名单", icon: "users", to: "/admin/users" },
    { key: "sandbox", label: "沙箱与容量", icon: "box", to: "/admin/sandbox" },
    { key: "audit", label: "审计回放", icon: "history", to: "/admin/audit" },
    STUDIO_ADMIN_NAV,                      // Agent Studio（切片自带）
  ];

  const activeKey = (() => {
    if (isAdmin) return loc.pathname.split("/")[2] ?? "templates";
    const studioKey = studioActiveKey(loc.pathname);   // 切片自己判定高亮
    if (studioKey) return studioKey;
    const alertsKey = alertsActiveKey(loc.pathname);
    if (alertsKey) return alertsKey;
    if (loc.pathname.startsWith("/omodel")) return "ontology";
    if (loc.pathname.startsWith("/settings")) return "settings";  // 底部「设置」入口页（OModel 已迁出），不亮任何一级导航
    if (loc.pathname.includes("/settings")) return "plugins";  // /agent-teams/:id/settings = 插件（Agent 配置）
    return "chat";
  })();

  return (
    <aside
      style={{
        width: showText ? EXPANDED : COLLAPSED,
        flex: `0 0 ${showText ? EXPANDED : COLLAPSED}px`,
        height: "100vh",
        background: color.sidebarBg,
        borderRight: `1px solid ${color.border}`,
        display: "flex",
        flexDirection: "column",
        transition: "width .16s",
      }}
    >

      {/* USER MODE */}
      {!isAdmin ? (
        <>
          <div style={{ padding: "6px 10px 2px", position: "relative" }}>
            <Interactive
              title="选择 Agent"
              onClick={() => setPickerOpen((v) => !v)}
              baseStyle={{
                display: "flex",
                alignItems: "center",
                gap: 9,
                padding: "9px 11px",
                borderRadius: radius.lg,
                background: "#fff",
                cursor: "pointer",
              }}
              hoverStyle={{ borderColor: color.brandTintBorder, background: "#e9ecf1" }}
            >
              <Icon name="robot" size={17} color={color.brand} />
              {showText ? (
                <>
                  <span style={{ flex: 1, fontSize: 13, fontWeight: 700, color: color.textStrong, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {currentAgent?.name ?? "选择 Agent"}
                  </span>
                  {
                    pickerOpen 
                    ?
                    <Icon name="chevron-up" size={15} color={color.textSubtle} />      
                    :
                    <Icon name="chevron-down" size={15} color={color.textSubtle} /> 
                  }
                </>
              ) : null}
            </Interactive>
            {pickerOpen && showText ? (
              <div
                style={{
                  position: "absolute",
                  top: "calc(100% + 6px)",
                  left: 10,
                  right: 10,
                  background: "#fff",
                  border: `1px solid ${color.border}`,
                  borderRadius: radius.xl,
                  boxShadow: shadow.menu,
                  zIndex: 50,
                  overflow: "hidden",
                  animation: "omPop .16s ease",
                }}
              >
                <div style={{ fontSize: 11, fontWeight: 700, color: color.textLabel, padding: "9px 13px 4px" }}>选择 Agent</div>
                {agents.map((ag) => {
                  const cur = ag.instance_id === currentAgentId;
                  return (
                    <Interactive
                      key={ag.instance_id}
                      onClick={async() => {
                        setCurrentAgentId(ag.instance_id);
                        setPickerOpen(false);
                        try {
                          const runId = await api.ensureRun(ag.instance_id);
                          nav(`/agent-teams/${ag.instance_id}/chat?run_id=${encodeURIComponent(runId)}`);
                        } catch {
                          nav(`/agent-teams/${ag.instance_id}/chat`);
                        }
                      }}
                      baseStyle={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 13px", cursor: "pointer", fontSize: 13, fontWeight: 600, color: color.textStrong }}
                      hoverStyle={{ background: "#f5f8ff" }}
                    >
                      <span style={{ flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{ag.name}</span>
                      {cur ? <Icon name="check" size={15} color={color.brand} /> : null}
                    </Interactive>
                  );
                })}
                <Interactive
                  onClick={() => { setPickerOpen(false); nav("/agents"); }}
                  baseStyle={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 13px", borderTop: `1px solid ${color.borderInner}`, cursor: "pointer", fontSize: 13, fontWeight: 600, color: color.textStrong }}
                  hoverStyle={{ background: "#f5f8ff" }}
                >
                  <span style={{ flex: 1 }}>全部 Agents</span>
                  <Icon name="layout-grid" size={15} color={color.textFainter} />
                </Interactive>
              </div>
            ) : null}
          </div>
          {/* 会话（32 号主操作：白底 + 轻阴影） */}
          <div style={{ padding: "6px 10px 2px" }}>
            <Interactive
              title="会话"
              data-testid="new-conversation"
              onClick={newChat}
              baseStyle={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", cursor: "pointer", fontSize: 13.5, fontWeight: 700, color: color.textStrong, borderRadius: radius.lg}}
              hoverStyle={{ borderColor: color.brandTintBorder, background: "rgb(233,236,241)" }}
            >
              <Icon name="message" size={18} color={color.textNav} />
              {showText ? <span style={{ flex: 1 }}>会话</span> : null}
            </Interactive>
          </div>
          <nav style={{ padding: "6px 10px", display: "flex", flexDirection: "column", gap: 2 }}>
            {userNav.map((item) => (
              <NavRow key={item.key} item={item} active={activeKey === item.key} showText={showText} onClick={() => item.to && nav(item.to)} />
            ))}
          </nav>
          {showText ? (
            <div data-testid="conversation-list" style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "6px 10px 10px" }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: color.textLabel, letterSpacing: 0.5, padding: "10px 8px 6px", display: "flex", alignItems: "center", gap: 6 }}>
                <Icon name="clock" size={13} />历史会话
              </div>
              {shownConvs.length === 0 ? (
                <div style={{ padding: "8px 10px", fontSize: 12.5, color: color.textFaint }}>
                  {conversationsLoading ? "正在加载…" : "暂无会话"}
                </div>
              ) : (
                shownConvs.map((c) => {
                  const cur = loc.pathname.includes(c.id) || activeRunId === c.id;
                  return (
                    <Interactive
                      key={c.id}
                      data-testid="conversation-row"
                      data-run-id={c.id}
                      title={c.title}
                      onClick={() => nav(`/agent-runs/${c.id}`)}
                      baseStyle={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 10px", borderRadius: radius.md, cursor: "pointer", fontSize: 13, color: cur ? color.brand : c.status === "closed" ? color.textFaint : color.textNav, fontWeight: cur ? 600 : 400, background: cur ? color.brandTintBg : "transparent" }}
                      hoverStyle={cur ? {} : { background: "#e9ecf1" }}
                    >
                      <span style={{ flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.title}</span>
                      {c.status === "closed" ? <Icon name="lock" size={12} color={color.textFainter} /> : null}
                      {/* stopPropagation：点删除不触发行跳转（Icon onClick 不带 event，包 span） */}
                      <span onClick={(e) => { e.stopPropagation(); void deleteConv(c); }} style={{ display: "inline-flex" }}>
                        <Icon name="trash" size={12} color={color.textFainter} title="删除会话" />
                      </span>
                    </Interactive>
                  );
                })
              )}
            </div>
          ) : (
            <div style={{ flex: 1 }} />
          )}
        </>
      ) : (
        <nav style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "8px 10px", display: "flex", flexDirection: "column", gap: 2 }}>
          {adminNav.map((item) => (
            <NavRow key={item.key} item={item} active={activeKey === item.key} showText={showText} onClick={() => item.to && nav(item.to)} />
          ))}
        </nav>
      )}

      {/* footer */}
      <div style={{ borderTop: `1px solid ${color.border}`, padding: "8px 10px" }}>
        <Interactive
          title="产品介绍"
          onClick={() => nav("/intro")}
          baseStyle={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", borderRadius: radius.lg, cursor: "pointer", fontSize: 13.5, color: color.textNav, fontWeight: 500 }}
          hoverStyle={{ background: "#e9ecf1" }}
        >
          <Icon name="sparkles" size={18} />
          {showText ? <span style={{ flex: 1 }}>产品介绍</span> : null}
        </Interactive>
        {isPlatformAdmin ? (
          <Interactive
            title={isAdmin ? "返回工作台" : "进入管理台"}
            onClick={() => nav(isAdmin ? `/agent-teams/${currentAgentId}/chat` : "/admin/templates")}
            baseStyle={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", borderRadius: radius.lg, cursor: "pointer", fontSize: 13.5, color: color.textNav, fontWeight: 500 }}
            hoverStyle={{ background: "#e9ecf1" }}
          >
            <Icon name={isAdmin ? "arrow-back-up" : "shield-lock"} size={18} />
            {showText ? <span style={{ flex: 1 }}>{isAdmin ? "返回工作台" : "进入管理台"}</span> : null}
          </Interactive>
        ) : null}
        {/* 设置（2026-08-07 对齐原型：告警接管配置迁入 /settings/alerts，入口在管理台项之下） */}
        {!isAdmin ? (
          <Interactive
            title="设置"
            onClick={() => nav("/settings/alerts")}
            baseStyle={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", borderRadius: radius.lg, cursor: "pointer", fontSize: 13.5, color: color.textNav, fontWeight: 500 }}
            hoverStyle={{ background: "#e9ecf1" }}
          >
            <Icon name="settings" size={18} />
            {showText ? <span style={{ flex: 1 }}>设置</span> : null}
          </Interactive>
        ) : null}
        <Interactive
          onClick={() => setCollapsed((v) => !v)}
          baseStyle={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", borderRadius: radius.lg, cursor: "pointer", fontSize: 13.5, color: color.textNav }}
          hoverStyle={{ background: "#e9ecf1" }}
        >
          <Icon name={collapsed ? "layout-sidebar-left-expand" : "layout-sidebar-left-collapse"} size={18} />
          {showText ? <span style={{ flex: 1 }}>收起</span> : null}
        </Interactive>
      </div>

      {/* user card */}
      <div style={{ borderTop: `1px solid ${color.border}`, padding: "11px 12px", display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ width: 32, height: 32, borderRadius: "50%", background: "#dfe6f5", color: color.brandStrong, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 700, flex: "0 0 32px" }}>
          {me?.initials ?? "·"}
        </div>
        {showText ? (
          <>
            <div style={{ lineHeight: 1.25, minWidth: 0, flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: color.textStrong }}>{me?.display_name ?? "…"}</div>
              <div style={{ fontSize: 11, color: color.textSubtle, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{me?.meta ?? ""}</div>
            </div>
            <Interactive as="button" title="退出登录"
              onClick={() => { void performLogout(); }}
              baseStyle={{ border: "none", background: "transparent", cursor: "pointer", padding: 2, display: "inline-flex", color: color.textFaint }}
              hoverStyle={{ color: color.danger }}>
              <Icon name="logout" size={15} />
            </Interactive>
          </>
        ) : null}
      </div>
    </aside>
  );
}

// 读浏览器 cookie（给 IAM signout 取 CSRF token 用；老项目 App.tsx readCookie 口径）。
function readCookie(name: string): string {
  const escaped = name.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&");
  const match = document.cookie.match(new RegExp("(?:^|;\\s*)" + escaped + "=([^;]*)"));
  return match ? decodeURIComponent(match[1]) : "";
}

// 退出登录（老项目 App.tsx handleLogout 口径）：后端登出 → **后台 POST** IAM signout（带 CSRF 头，
// 同源、清 SSO cookie）→ 浏览器只 assign 到回跳/登录地址。**绝不导航到 signout_url**——它只收
// POST + CSRF，GET 跳过去必 404 白页（就是本次修复的根因）。
async function performLogout(): Promise<void> {
  let cfg: Awaited<ReturnType<typeof api.logout>>;
  try {
    cfg = await api.logout();
  } catch {
    window.location.reload(); // mock/网络失败：刷新回到登录判定
    return;
  }
  // ① 后台 POST 清 IAM SSO（带 cookie + CSRF 头）；失败不阻断本地清理 + 跳转
  if (cfg.signout_url) {
    const url = cfg.signout_url.replaceAll("{host}", window.location.host);
    const headerName = cfg.csrf_header_name || "IAM-Csrf-Token";
    const token = readCookie(cfg.csrf_cookie_name || "IAM-Csrf-Token");
    try {
      await fetch(url, {
        method: "POST",
        credentials: "include",
        headers: token ? { [headerName]: token } : {},
      });
    } catch {
      // 同源 signout 失败也照常跳转（IAM 那边可能已注销，本端 TokenCache 也清了）
    }
  }
  // ② 只跳回跳地址（清 SSO 后再访问触发重新登录）；未配回跳 → login_url；都没有 → reload 兜底
  const dest = cfg.redirect_url || cfg.login_url;
  if (dest) window.location.assign(dest.replaceAll("{host}", window.location.host));
  else window.location.reload();
}
