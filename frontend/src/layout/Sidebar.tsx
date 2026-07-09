import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { color, radius, shadow } from "../theme/tokens";
import { Icon, Interactive } from "../ui";
import { useApp } from "../lib/appState";
import { api } from "../lib/api";

const EXPANDED = 248;
const COLLAPSED = 60;

interface NavItem {
  key: string;
  label: string;
  icon: string;
  to?: string;
  locked?: boolean;
}

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
  const { me, agents, currentAgentId, setCurrentAgentId, toggleRole } = useApp();
  const [collapsed, setCollapsed] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  const isAdmin = loc.pathname.startsWith("/admin");
  const showText = !collapsed;
  const currentAgent = agents.find((a) => a.instance_id === currentAgentId) ?? agents[0];

  // 32 号导航项：新对话（主操作）+ 知识 / 插件 / 自动化 / 本体（V1 先全部置灰，无对应页面）
  const userNav: NavItem[] = [
    { key: "knowledge", label: "知识", icon: "book-2", locked: true },
    { key: "plugins", label: "插件", icon: "puzzle", locked: true },
    { key: "automation", label: "自动化", icon: "robot", locked: true },
    { key: "ontology", label: "本体", icon: "sitemap", locked: true },
  ];

  const newChat = async () => {
    if (!currentAgentId) return nav("/init");
    try {
      const runId = await api.createRun(currentAgentId);
      nav(`/agent-teams/${currentAgentId}/chat?run_id=${encodeURIComponent(runId)}`);
    } catch {
      nav(`/agent-teams/${currentAgentId}/chat`);
    }
  };
  // B7a IA（30.6 2026-07-09 拍板）：Tool 标注/资产治理并入模板管理 drill；新增模型资产
  const adminNav: NavItem[] = [
    { key: "templates", label: "模板管理", icon: "layout-grid", to: "/admin/templates" },
    { key: "model-assets", label: "模型资产", icon: "cpu", to: "/admin/model-assets" },
    { key: "users", label: "用户与白名单", icon: "users", to: "/admin/users" },
    { key: "sandbox", label: "沙箱与容量", icon: "box", to: "/admin/sandbox" },
    { key: "audit", label: "审计回放", icon: "history", to: "/admin/audit" },
  ];

  const activeKey = (() => {
    if (isAdmin) return loc.pathname.split("/")[2] ?? "templates";
    if (loc.pathname.includes("/settings")) return "settings";
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
      {/* brand */}
      <div style={{ padding: "16px 16px 12px", display: "flex", alignItems: "center", gap: 10 }}>
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: 9,
            background: color.brandGrad,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flex: "0 0 32px",
            boxShadow: shadow.brand,
          }}
        >
          <Icon name="robot" size={19} color="#fff" />
        </div>
        {showText ? (
          <div style={{ lineHeight: 1.15 }}>
            <div style={{ fontSize: 15, fontWeight: 800 }}>OpenOps</div>
            <div style={{ fontSize: 10.5, fontWeight: 600, color: color.textSubtle, letterSpacing: 0.4 }}>
              SRE Agent 工作台
            </div>
          </div>
        ) : null}
      </div>

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
                border: `1px solid ${color.borderInput}`,
                background: "#fff",
                cursor: "pointer",
              }}
              hoverStyle={{ borderColor: color.brandTintBorder, background: "#f9fbff" }}
            >
              <Icon name="robot" size={17} color={color.brand} />
              {showText ? (
                <>
                  <span style={{ flex: 1, fontSize: 13, fontWeight: 700, color: color.textStrong, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {currentAgent?.name ?? "选择 Agent"}
                  </span>
                  <Icon name="selector" size={15} color={color.textSubtle} />
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
                      onClick={() => {
                        setCurrentAgentId(ag.instance_id);
                        setPickerOpen(false);
                        nav(`/agent-teams/${ag.instance_id}/chat`);
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
                  onClick={() => { setPickerOpen(false); nav("/init"); }}
                  baseStyle={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 13px", borderTop: `1px solid ${color.borderInner}`, cursor: "pointer", fontSize: 13, fontWeight: 600, color: color.textStrong }}
                  hoverStyle={{ background: "#f5f8ff" }}
                >
                  <span style={{ flex: 1 }}>新建 Agent</span>
                  <Icon name="plus" size={15} color={color.brand} />
                </Interactive>
              </div>
            ) : null}
          </div>
          {/* 新对话（32 号主操作：白底 + 轻阴影） */}
          <div style={{ padding: "6px 10px 2px" }}>
            <Interactive
              title="新对话"
              onClick={newChat}
              baseStyle={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", borderRadius: radius.lg, cursor: "pointer", background: "#fff", border: `1px solid ${color.border}`, boxShadow: shadow.card, fontSize: 13.5, fontWeight: 700, color: color.textStrong }}
              hoverStyle={{ borderColor: color.brandTintBorder, background: "#f9fbff" }}
            >
              <Icon name="edit" size={18} color={color.brand} />
              {showText ? <span style={{ flex: 1 }}>新对话</span> : null}
            </Interactive>
          </div>
          <nav style={{ padding: "6px 10px", display: "flex", flexDirection: "column", gap: 2 }}>
            {userNav.map((item) => (
              <NavRow key={item.key} item={item} active={activeKey === item.key} showText={showText} onClick={() => item.to && nav(item.to)} />
            ))}
          </nav>
          {showText ? (
            <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "6px 10px 10px" }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: color.textLabel, letterSpacing: 0.5, padding: "10px 8px 6px", display: "flex", alignItems: "center", gap: 6 }}>
                <Icon name="clock" size={13} />历史会话
              </div>
              {["支付延迟突增定界", "对账任务超时排查", "Redis 连接池告警"].map((t, i) => (
                <Interactive
                  key={i}
                  onClick={() => nav(`/agent-teams/${currentAgentId}/chat`)}
                  baseStyle={{ display: "flex", alignItems: "center", padding: "8px 10px", borderRadius: radius.md, cursor: "pointer", fontSize: 13, color: color.textNav }}
                  hoverStyle={{ background: "#e9ecf1" }}
                >
                  <span style={{ flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{t}</span>
                </Interactive>
              ))}
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
        {!isAdmin ? (
          <NavRow
            item={{ key: "settings", label: "设置", icon: "settings" }}
            active={activeKey === "settings"}
            showText={showText}
            onClick={() => nav(`/agent-teams/${currentAgentId}/settings`)}
          />
        ) : null}
        <Interactive
          title={isAdmin ? "返回工作台" : "进入管理台"}
          onClick={() => {
            toggleRole();
            nav(isAdmin ? `/agent-teams/${currentAgentId}/chat` : "/admin/templates");
          }}
          baseStyle={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", borderRadius: radius.lg, cursor: "pointer", fontSize: 13.5, color: color.textNav, fontWeight: 500 }}
          hoverStyle={{ background: "#e9ecf1" }}
        >
          <Icon name={isAdmin ? "arrow-back-up" : "shield-lock"} size={18} />
          {showText ? <span style={{ flex: 1 }}>{isAdmin ? "返回工作台" : "进入管理台"}</span> : null}
        </Interactive>
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
            <Icon name="logout" size={15} color={color.textFaint} />
          </>
        ) : null}
      </div>
    </aside>
  );
}
