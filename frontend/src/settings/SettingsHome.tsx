import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useApp } from "../lib/appState";
import { color, radius } from "../theme/tokens";
import { Icon, Interactive, Pill } from "../ui";

/** 设置页（/settings）：二级菜单骨架。原「Agent 配置」内容已迁到侧栏「插件」；
 * 此处承载与单 Agent 无关的用户级配置。「OModel」= 嵌入 wesee omodel 控制台
 * （iframe，workspace 取所选 Agent 绑定的 workspace_id）；页面前缀由后端下发
 * （GET /omodel/console-page——环境差异在后端 env，前端镜像两环境共用）。
 * ⚠iframe 可用性取决于对端 X-Frame-Options/CSP frame-ancestors 与浏览器三方
 * cookie 策略（console 登录态 SameSite 严格时跨站 iframe 会丢）——「新窗口打开」为兜底。 */
export function SettingsHome() {
  const nav = useNavigate();
  const { agents, currentAgentId } = useApp();
  const [pageBase, setPageBase] = useState<string | null>(null); // null=加载中
  const [selectedId, setSelectedId] = useState<string>(currentAgentId);

  useEffect(() => {
    let alive = true;
    api.getOmodelPageBase().then(
      (b) => { if (alive) setPageBase(b); },
      () => { if (alive) setPageBase(""); },
    );
    return () => { alive = false; };
  }, []);

  const selected = useMemo(
    () => agents.find((a) => a.instance_id === selectedId) ?? agents[0] ?? null,
    [agents, selectedId],
  );
  const iframeSrc = pageBase && selected?.workspace_id
    ? `${pageBase}${encodeURIComponent(selected.workspace_id)}`
    : "";

  return (
    <>
      <header style={{ flex: "0 0 auto", height: 56, borderBottom: `1px solid ${color.border}`, background: "#fff", display: "flex", alignItems: "center", padding: "0 24px", gap: 12 }}>
        <Interactive as="button" onClick={() => nav(-1)}
          baseStyle={{ border: `1px solid ${color.border}`, background: "#fff", cursor: "pointer", width: 32, height: 32, borderRadius: radius.md, display: "inline-flex", alignItems: "center", justifyContent: "center", color: "#697283" }}
          hoverStyle={{ background: color.pageBg }}>
          <Icon name="arrow-left" size={17} />
        </Interactive>
        <div style={{ fontSize: 15, fontWeight: 700 }}>设置</div>
        <span style={{ fontSize: 12, color: color.textSubtle }}>用户级配置</span>
      </header>

      <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
        {/* 二级菜单（当前唯一项 OModel，常驻选中态） */}
        <div style={{ flex: "0 0 250px", borderRight: `1px solid ${color.border}`, background: "#fff", padding: "14px 10px", display: "flex", flexDirection: "column", gap: 2 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", borderRadius: radius.lg, cursor: "default", fontSize: 13.5, fontWeight: 600, color: color.brand, background: color.brandTintBg }}>
            <Icon name="topology-star-3" size={18} />
            <span style={{ flex: 1 }}>OModel</span>
          </div>
        </div>

        {/* OModel 面板：Agent 选择 + iframe */}
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          {pageBase === null ? (
            <Empty icon="loader-2" title="加载中…" desc="正在获取 OModel 控制台地址。" />
          ) : agents.length === 0 ? (
            <Empty icon="robot" title="还没有 Agent" desc="先在初始化向导创建 Agent 并绑定数据空间，再回到这里查看其 OModel 内容。" />
          ) : !pageBase ? (
            <Empty icon="plug-x" title="OModel 控制台未配置" desc="该功能在内网环境可用：需后端配置 OPENOPS_OMODEL_BASE_URL（或 OPENOPS_OMODEL_PAGE_URL）后生效。" />
          ) : (
            <>
              <div style={{ flex: "0 0 auto", display: "flex", alignItems: "center", gap: 10, padding: "10px 16px", borderBottom: `1px solid ${color.border}`, background: "#fff" }}>
                <span style={{ fontSize: 12.5, color: color.textMuted }}>数据空间</span>
                <select
                  value={selected?.instance_id ?? ""}
                  onChange={(e) => setSelectedId(e.target.value)}
                  style={{ fontSize: 13, padding: "6px 10px", borderRadius: radius.md, border: `1px solid ${color.border}`, background: "#fff", color: "inherit", maxWidth: 360 }}
                >
                  {agents.map((a) => (
                    <option key={a.instance_id} value={a.instance_id}>
                      {a.name} · {a.workspace_label || a.workspace_id || "未绑定"}
                    </option>
                  ))}
                </select>
                <span style={{ flex: 1 }} />
                {iframeSrc && (
                  <Interactive as="button" onClick={() => window.open(iframeSrc, "_blank", "noopener")}
                    baseStyle={{ display: "inline-flex", alignItems: "center", gap: 6, border: `1px solid ${color.border}`, background: "#fff", cursor: "pointer", padding: "6px 12px", borderRadius: radius.md, fontSize: 12.5, color: color.textMuted }}
                    hoverStyle={{ background: color.pageBg }}>
                    <Icon name="external-link" size={14} /> 新窗口打开
                  </Interactive>
                )}
              </div>
              {selected && !selected.workspace_id ? (
                <Empty icon="unlink" title="该 Agent 未绑定数据空间" desc="此 Agent 缺少 workspace 绑定，请在 Agent 配置里重新绑定后查看。" />
              ) : (
                <iframe
                  key={iframeSrc} /* workspace 切换时强制重载，避免对端 SPA 内部路由残留 */
                  src={iframeSrc}
                  title={`OModel · ${selected?.workspace_label || selected?.workspace_id || ""}`}
                  style={{ flex: 1, width: "100%", border: "none", background: "#fff" }}
                />
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

function Empty({ icon, title, desc }: { icon: string; title: string; desc: string }) {
  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ textAlign: "center", maxWidth: 460, padding: 24 }}>
        <div style={{ width: 56, height: 56, borderRadius: 16, background: color.pageBg, display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 14 }}>
          <Icon name={icon} size={28} color={color.textFaint} />
        </div>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6, display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}>
          {title} <Pill tone="neutral">OModel</Pill>
        </div>
        <div style={{ fontSize: 12.5, color: color.textMuted, lineHeight: 1.7 }}>{desc}</div>
      </div>
    </div>
  );
}
