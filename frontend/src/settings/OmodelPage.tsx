import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { useApp } from "../lib/appState";
import { color, radius } from "../theme/tokens";
import { Icon, Interactive, Pill } from "../ui";

/** OModel 页（/omodel，侧栏一级导航）：嵌入 wesee omodel 控制台。
 *
 * 2026-08-10 从设置页二级菜单迁出为一级页面（设置只留 告警接管配置/通知配置）。
 * iframe 的 workspace 取所选 Agent 绑定的 workspace_id；页面前缀由后端下发
 * （GET /omodel/console-page——环境差异在后端 env，前端镜像两环境共用）。
 * ⚠iframe 可用性取决于对端 X-Frame-Options/CSP frame-ancestors 与浏览器三方
 * cookie 策略（console 登录态 SameSite 严格时跨站 iframe 会丢）——「新窗口打开」为兜底。 */
export function OmodelPage() {
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
        <div style={{ fontSize: 15, fontWeight: 700 }}>OModel</div>
        <span style={{ fontSize: 12, color: color.textSubtle }}>按 Agent 绑定的数据空间查看 omodel 控制台</span>
      </header>

      <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
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
