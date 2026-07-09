import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { color, radius } from "../theme/tokens";
import { toneColor } from "../theme/tokens";
import { Icon, Button, Dot, Pill } from "../ui";
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

/** 管理台（isAdminPage·新原型）：通用表（资产含 Skill/MCP/模型 三 Tab）/ 可编辑沙箱 / 审计。 */
export function AdminConsole() {
  const { page = "templates" } = useParams();
  const [table, setTable] = useState<AdminTableData | null>(null);
  const [audit, setAudit] = useState<AuditNode[]>([]);
  const [tab, setTab] = useState(0);
  const [tplOpen, setTplOpen] = useState<string | null>(null);
  const [annotTool, setAnnotTool] = useState<string | null>(null);

  const isTable = ["templates", "mcp-tools", "assets", "users"].includes(page);
  // 新原型：资产页三 Tab（Skill / MCP / 模型）
  const assetTabs = [{ key: "skill", label: "Skill" }, { key: "mcp", label: "MCP" }, { key: "model", label: "模型" }];

  const loadTable = useCallback(async (p: string, tabIdx: number) => {
    if (p === "assets" && assetTabs[tabIdx]?.key === "model") {
      setTable(await api.getAdminTable("models"));
    } else {
      setTable(await api.getAdminTable(p));
    }
  }, []);

  useEffect(() => {
    setTab(0);
    if (isTable) void loadTable(page, 0);
    else if (page === "audit") api.getAuditTimeline().then(setAudit);
  }, [page, isTable, loadTable]);

  const tabs = page === "assets" ? assetTabs : table?.tabs;
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
              {tabs ? (
                <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
                  {tabs.map((tb, i) => (
                    <span key={tb.key}
                      onClick={() => { setTab(i); void loadTable(page, i); }}
                      style={{ fontSize: 12.5, fontWeight: 600, padding: "6px 14px", borderRadius: radius.md, cursor: "pointer", color: i === tab ? "#fff" : color.textNav, background: i === tab ? color.brand : "#eceef2" }}>
                      {tb.label}
                    </span>
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

          {page === "sandbox" ? <SandboxPanel /> : null}

          {page === "audit" ? (
            <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "18px 20px" }}>
              <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 14 }}>回放时间线 · 最近事件</div>
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

      <TemplateEditorModal open={!!tplOpen} name="感知快恢 Agent" onClose={() => setTplOpen(null)} />
      <ToolAnnotationSlideIn open={!!annotTool} tool={annotTool ?? ""} onClose={() => setAnnotTool(null)} />
    </>
  );
}

/** 新原型：沙箱可调配置——只读 → 编辑中 → reason 必填 → 确认生效（写审计 runtime_config.updated）。 */
function SandboxPanel() {
  const [cfg, setCfg] = useState<SandboxCfg[]>([]);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  const [saved, setSaved] = useState(false);

  const load = useCallback(() => { api.getSandboxCfg().then(setCfg); }, []);
  useEffect(load, [load]);

  const startEdit = () => {
    setDraft(Object.fromEntries(cfg.map((c) => [c.key, c.val])));
    setEditing(true);
    setSaved(false);
    setErr("");
  };
  const cancel = () => { setEditing(false); setReason(""); setErr(""); };
  const confirm = async () => {
    if (!reason.trim()) {
      setErr("请填写变更原因——配置修改必须写审计（runtime_config.updated）。");
      return;
    }
    const updates: Record<string, unknown> = {};
    for (const c of cfg) if (draft[c.key] !== c.val) updates[c.key] = draft[c.key];
    try {
      await api.saveSandboxCfg(updates, reason.trim());
      setEditing(false);
      setReason("");
      setErr("");
      setSaved(true);
      load();
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  return (
    <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "18px 20px" }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>可调配置</div>
          <div style={{ fontSize: 12, color: color.textSubtle }}>修改需填写 reason 并写审计；影响新建容器与后续容量准入，已存在容器重建后生效。</div>
        </div>
        {!editing ? (
          <Button variant="secondary" icon="pencil" onClick={startEdit} style={{ fontSize: 12.5, padding: "8px 15px" }}>编辑</Button>
        ) : (
          <Pill tone="warning">编辑中</Pill>
        )}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px 20px" }}>
        {cfg.map((c) => (
          <div key={c.key} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, borderBottom: `1px solid ${color.borderFaint}`, padding: "8px 0" }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 12.5, fontWeight: 600, color: color.textStrong, fontFamily: "ui-monospace, monospace" }}>{c.key}</div>
              <div style={{ fontSize: 11, color: color.textSubtle }}>{c.desc}</div>
            </div>
            {editing ? (
              <input
                value={draft[c.key] ?? ""}
                onChange={(e) => setDraft((d) => ({ ...d, [c.key]: e.target.value }))}
                style={{ width: 110, height: 32, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, padding: "0 10px", fontSize: 13, fontWeight: 700, color: color.brandStrong, textAlign: "right", outline: "none" }}
              />
            ) : (
              <span style={{ fontSize: 13, fontWeight: 700, color: color.brandStrong }}>{c.val}</span>
            )}
          </div>
        ))}
      </div>
      {editing ? (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 16, paddingTop: 14, borderTop: `1px solid ${color.borderInner}` }}>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="变更原因（必填，写入审计 runtime_config.updated）"
              style={{ flex: 1, height: 36, border: `1px solid #dfe2e8`, borderRadius: radius.md, padding: "0 12px", fontSize: 12.5, outline: "none" }}
            />
            <Button variant="secondary" onClick={cancel} style={{ fontSize: 12.5, padding: "9px 16px" }}>取消</Button>
            <Button onClick={confirm} style={{ fontSize: 12.5, padding: "9px 18px" }}>确认生效</Button>
          </div>
          {err ? <div style={{ fontSize: 12, color: color.dangerText, marginTop: 8 }}>{err}</div> : null}
        </>
      ) : null}
      {saved ? (
        <div style={{ fontSize: 12, color: color.goodText, fontWeight: 600, marginTop: 12, display: "inline-flex", alignItems: "center", gap: 5 }}>
          <Icon name="circle-check" size={14} color={color.goodText} />配置已生效：影响新建容器与后续容量准入，已写入审计事件 runtime_config.updated。
        </div>
      ) : null}
    </div>
  );
}
