import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { color, radius } from "../theme/tokens";
import { toneColor } from "../theme/tokens";
import { Icon, Button, Dot } from "../ui";
import { api } from "../lib/api";
import type { AdminTableData, SandboxCfg, AuditNode } from "../lib/api/types";
import { ToolAnnotationSlideIn } from "./ToolAnnotationSlideIn";
import { TemplateEditorModal } from "./TemplateEditorModal";

const TITLES: Record<string, string> = {
  templates: "模板管理",
  "mcp-tools": "MCP Tool 标注",
  assets: "平台资产治理",
  users: "用户与白名单",
  sandbox: "沙箱与容量",
  audit: "审计与 Trace 回放",
};

/** 管理台（isAdminPage）：通用表 / 沙箱 / 审计 + 模板编辑模态 + Tool 标注抽屉。 */
export function AdminConsole() {
  const { page = "templates" } = useParams();
  const [table, setTable] = useState<AdminTableData | null>(null);
  const [sandbox, setSandbox] = useState<SandboxCfg[]>([]);
  const [audit, setAudit] = useState<AuditNode[]>([]);
  const [tab, setTab] = useState(0);
  const [tplOpen, setTplOpen] = useState<string | null>(null);
  const [annotTool, setAnnotTool] = useState<string | null>(null);

  const isTable = ["templates", "mcp-tools", "assets", "users"].includes(page);

  useEffect(() => {
    setTab(0);
    if (isTable) api.getAdminTable(page).then(setTable);
    else if (page === "sandbox") api.getSandboxCfg().then(setSandbox);
    else if (page === "audit") api.getAuditTimeline().then(setAudit);
  }, [page, isTable]);

  const gridCols = useMemo(() => (table ? table.cols.map((c) => c.width ?? "1fr").join(" ") : ""), [table]);

  const onCellAction = (key?: string, rowId?: string) => {
    if (key === "edit-template") setTplOpen(rowId ?? "模板");
    else if (key === "annotate") setAnnotTool(rowId ?? "tool");
  };

  return (
    <>
      <header style={{ flex: "0 0 auto", height: 56, borderBottom: `1px solid ${color.border}`, background: "#fff", display: "flex", alignItems: "center", padding: "0 24px", gap: 12 }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>{TITLES[page] ?? "管理台"}</div>
        <div style={{ flex: 1 }} />
        {isTable && table?.primary ? <Button icon={table.primary.icon}>{table.primary.label}</Button> : null}
      </header>

      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: 24 }}>
        <div style={{ maxWidth: 1080, margin: "0 auto" }}>
          {isTable && table ? (
            <>
              {table.tabs ? (
                <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
                  {table.tabs.map((tb, i) => (
                    <span key={tb.key} onClick={() => setTab(i)} style={{ fontSize: 12.5, fontWeight: 600, padding: "6px 13px", borderRadius: radius.md, cursor: "pointer", border: `1px solid ${i === tab ? color.brand : color.border}`, color: i === tab ? color.brand : color.textNav, background: i === tab ? color.brandTintBg : "#fff" }}>{tb.label}</span>
                  ))}
                </div>
              ) : null}
              <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, overflow: "hidden" }}>
                <div style={{ display: "grid", gridTemplateColumns: gridCols, gap: 12, padding: "11px 16px", background: color.surfaceAlt, borderBottom: `1px solid ${color.border}`, fontSize: 11.5, fontWeight: 700, color: color.textSubtle }}>
                  {table.cols.map((c, i) => <div key={i} style={{ textAlign: i === table.cols.length - 1 && c.width ? "right" : "left" }}>{c.label}</div>)}
                </div>
                {table.rows.map((r, ri) => (
                  <div key={r.id} style={{ display: "grid", gridTemplateColumns: gridCols, gap: 12, padding: "12px 16px", alignItems: "center", borderTop: ri ? `1px solid ${color.borderFaint}` : "none" }}>
                    {r.cells.map((cell, ci) => (
                      <div key={ci} style={{ textAlign: ci === r.cells.length - 1 && table.cols[ci]?.width ? "right" : "left", minWidth: 0 }}>
                        {cell.kind === "badge" ? (
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11.5, fontWeight: 600, color: toneColor[cell.tone ?? "neutral"].text, background: toneColor[cell.tone ?? "neutral"].bg, border: `1px solid ${toneColor[cell.tone ?? "neutral"].border}`, padding: "3px 9px", borderRadius: radius.pill }}>
                            <Dot tone={cell.tone ?? "neutral"} />{cell.text}
                          </span>
                        ) : cell.kind === "action" ? (
                          <span onClick={() => onCellAction(cell.onClickKey, r.id)} style={{ fontSize: 12, color: color.brand, fontWeight: 600, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}>{cell.text}<Icon name="chevron-right" size={13} color={color.brand} /></span>
                        ) : (
                          <span style={{ fontSize: 12.5, color: color.textStrong, fontFamily: cell.mono ? "ui-monospace, monospace" : undefined, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", display: "block" }}>{cell.text}</span>
                        )}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </>
          ) : null}

          {page === "sandbox" ? (
            <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "18px 20px" }}>
              <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>可调配置</div>
              <div style={{ fontSize: 12, color: color.textSubtle, marginBottom: 14 }}>修改需填写 reason 并写审计；影响新建容器与后续容量准入，已存在容器重建后生效。</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px 20px" }}>
                {sandbox.map((c) => (
                  <div key={c.key} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", borderBottom: `1px solid ${color.borderFaint}`, padding: "8px 0" }}>
                    <div>
                      <div style={{ fontSize: 12.5, fontWeight: 600, color: color.textStrong, fontFamily: "ui-monospace, monospace" }}>{c.key}</div>
                      <div style={{ fontSize: 11, color: color.textSubtle }}>{c.desc}</div>
                    </div>
                    <span style={{ fontSize: 13, fontWeight: 700, color: color.brandStrong }}>{c.val}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {page === "audit" ? (
            <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "18px 20px" }}>
              <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 14 }}>回放时间线 · 支付延迟突增定界</div>
              {audit.map((n, i) => (
                <div key={i} style={{ display: "grid", gridTemplateColumns: "18px 1fr", gap: 10, paddingBottom: 12 }}>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
                    <div style={{ width: 10, height: 10, borderRadius: "50%", background: color.brand, marginTop: 3 }} />
                    {i < audit.length - 1 ? <div style={{ width: 2, flex: 1, marginTop: 3, background: color.border }} /> : null}
                  </div>
                  <div style={{ paddingBottom: 2 }}>
                    <div style={{ fontSize: 12.5, fontWeight: 600, color: color.textStrong, fontFamily: "ui-monospace, monospace" }}>{n.event}</div>
                    <div style={{ fontSize: 11.5, color: color.textSubtle, marginTop: 2 }}>{n.detail}</div>
                  </div>
                </div>
              ))}
              <div style={{ marginTop: 6, fontSize: 11.5, color: color.textSubtle }}>脱敏铁律：不显 Cookie / Secret / token / prompt 全文，只显 hash / request_id。</div>
            </div>
          ) : null}
        </div>
      </div>

      <TemplateEditorModal open={!!tplOpen} name={tplOpen === "tpl_sre_fast_recovery" ? "感知快恢 Agent" : "模板编辑器"} onClose={() => setTplOpen(null)} />
      <ToolAnnotationSlideIn open={!!annotTool} tool={annotTool ?? ""} onClose={() => setAnnotTool(null)} />
    </>
  );
}
