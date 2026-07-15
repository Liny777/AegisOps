import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { color, radius } from "../theme/tokens";
import { toneColor } from "../theme/tokens";
import { Icon, Button, Dot, Pill } from "../ui";
import { api } from "../lib/api";
import type { AdminTableData, SandboxCfg, SandboxContainer, AuditNode } from "../lib/api/types";
import { useConnTest, ConnTestResult, PROTOCOL_LABEL, DEFAULT_CONTEXT_WINDOW } from "../settings/AddCustomModelDialog";
import { ToolAnnotationSlideIn } from "./ToolAnnotationSlideIn";
import { TemplateEditorModal } from "./TemplateEditorModal";

const TITLES: Record<string, string> = {
  templates: "模板管理",
  "model-assets": "模型资产",
  skills: "Skill 基线",
  users: "用户与白名单",
  sandbox: "沙箱与容量",
  audit: "审计与 Trace 回放",
};

/** 管理台（30.6 2026-07-09 IA）：5 一级页；模板管理内 drill（资产治理 → Tool 标注，标注全局一份）。 */
export function AdminConsole() {
  const { page = "templates" } = useParams();
  const [table, setTable] = useState<AdminTableData | null>(null);
  const [audit, setAudit] = useState<AuditNode[]>([]);
  // 模板 drill：模板管理 → 模板名·资产治理 → MCP名·Tool 标注
  const [tplDrill, setTplDrill] = useState<{ id: string; name: string } | null>(null);
  const [mcpDrill, setMcpDrill] = useState<string | null>(null);
  const [toolsRaw, setToolsRaw] = useState<Record<string, unknown>[]>([]);
  const [annotRow, setAnnotRow] = useState<Record<string, unknown> | null>(null);
  // 模型资产：注册 / 白名单授权弹窗；模板编辑器（B7·二）
  const [registerOpen, setRegisterOpen] = useState(false);
  const [grantsFor, setGrantsFor] = useState<{ id: string; name: string } | null>(null);
  const [tplEdit, setTplEdit] = useState<string | null>(null);
  // B7·三：白名单添加弹窗 / 表格动作错误横幅 / 审计 Trace 过滤
  const [wlAddOpen, setWlAddOpen] = useState(false);
  const [actionErr, setActionErr] = useState("");
  const [traceFilter, setTraceFilter] = useState("");

  const isTable = ["templates", "model-assets", "skills", "users"].includes(page);

  const load = useCallback(async () => {
    if (page === "templates") {
      if (mcpDrill) {
        const d = await api.getAdminMcpTools(mcpDrill);
        setToolsRaw(d.raw);
        setTable(d);
      } else if (tplDrill) {
        // 资产治理（drill 1）：平台资产表 + 按模板 default_tools 计算「绑定/解绑」列（B7·二写路径）
        const [assets, detail, toolsData] = await Promise.all([
          api.getAdminTemplateAssets(),
          api.getAdminTemplateDetail(tplDrill.id),
          api.getAdminMcpTools(null),
        ]);
        const base = (detail.draft_version ?? detail.active_version) as Record<string, unknown> | null;
        const bound = new Set((((base?.content_json as Record<string, unknown>)?.main as Record<string, unknown>)?.default_tools ?? []) as string[]);
        const allowedByMcp = new Map<string, string[]>();
        for (const r of toolsData.raw) {
          if (r.annotation_id != null && r.annotation_status === "allowed") {
            const k = String(r.mcp_display_name);
            allowedByMcp.set(k, [...(allowedByMcp.get(k) ?? []), String(r.tool_name)]);
          }
        }
        setTable({
          ...assets,
          cols: [...assets.cols, { label: "模板绑定", width: "72px" }],
          rows: assets.rows.map((r) => {
            const tools = allowedByMcp.get(r.id) ?? [];
            const isBound = tools.length > 0 && tools.every((t) => bound.has(t));
            return { ...r, cells: [...r.cells, { text: isBound ? "解绑" : "绑定", kind: "action" as const, onClickKey: "toggle-bind" }] };
          }),
        });
      } else {
        setTable(await api.getAdminTable("templates"));
      }
    } else if (isTable) {
      setTable(await api.getAdminTable(page));
    } else if (page === "audit") {
      setAudit(traceFilter ? await api.getAuditTrace(traceFilter) : await api.getAuditTimeline());
    }
  }, [page, isTable, tplDrill, mcpDrill, traceFilter]);

  useEffect(() => { setTplDrill(null); setMcpDrill(null); }, [page]);
  useEffect(() => { void load(); }, [load]);

  const tabs = table?.tabs;
  const gridCols = useMemo(() => (table ? table.cols.map((c) => c.width ?? "1fr").join(" ") : ""), [table]);

  const onCellAction = (key: string | undefined, rowId: string, rowName: string) => {
    if (key === "open-template") setTplDrill({ id: rowId, name: rowName });
    else if (key === "edit-template") setTplEdit(rowId);
    else if (key === "open-mcp") setMcpDrill(rowId);
    else if (key === "annotate") setAnnotRow(toolsRaw.find((r) => String(r.tool_catalog_id) === rowId) ?? null);
    else if (key === "model-grants") setGrantsFor({ id: rowId, name: rowName });
    else if (key === "toggle-bind" && tplDrill) void toggleBind(rowId);
    else if (key === "wl-revoke" || key === "wl-add") {
      setActionErr("");
      const op = key === "wl-revoke" ? api.adminRevokeWhitelist(rowId) : api.adminAddWhitelist(rowId, "");
      void op.then(() => load()).catch((e) => setActionErr((e as Error).message));
    }
    else if (key === "role-admin" || key === "role-user") {
      // 角色升/降（set-role 补链）：改自己后端 400（防管理面锁死），错误进动作横幅
      setActionErr("");
      void api.adminSetRole(rowId, key === "role-admin" ? "platform_admin" : "user")
        .then(() => load()).catch((e) => setActionErr((e as Error).message));
    }
  };

  /** 资产治理「绑定/解绑」：该 MCP 的全部 allowed tools 加入/移出模板草稿 default_tools（发布后生效）。 */
  const toggleBind = async (mcpName: string) => {
    if (!tplDrill) return;
    const [detail, toolsData] = await Promise.all([
      api.getAdminTemplateDetail(tplDrill.id),
      api.getAdminMcpTools(null),
    ]);
    const base = (detail.draft_version ?? detail.active_version) as Record<string, unknown> | null;
    const content = { ...((base?.content_json ?? {}) as Record<string, unknown>) };
    const main = { ...((content.main ?? {}) as Record<string, unknown>) };
    const cur = new Set(((main.default_tools ?? []) as string[]));
    const mcpTools = toolsData.raw
      .filter((r) => String(r.mcp_display_name) === mcpName && r.annotation_id != null && r.annotation_status === "allowed")
      .map((r) => String(r.tool_name));
    const isBound = mcpTools.length > 0 && mcpTools.every((t) => cur.has(t));
    mcpTools.forEach((t) => (isBound ? cur.delete(t) : cur.add(t)));
    content.main = { ...main, default_tools: [...cur] };
    try {
      const v = await api.saveTemplateDraft(tplDrill.id, content);
      alert(`已保存到草稿 v${v.version_no}（在模板编辑器发布后生效）`);
      void load();
    } catch (e) {
      alert((e as Error).message);
    }
  };

  // 面包屑（drill 时替换标题）：模板管理 / 模板名 · 资产治理 / MCP名 · Tool 标注
  const crumbs = page === "templates" && tplDrill
    ? [
        { label: "模板管理", onClick: () => { setTplDrill(null); setMcpDrill(null); } },
        { label: `${tplDrill.name} · 资产治理`, onClick: mcpDrill ? () => setMcpDrill(null) : undefined },
        ...(mcpDrill ? [{ label: `${mcpDrill} · Tool 标注`, onClick: undefined }] : []),
      ]
    : null;

  return (
    <>
      <header style={{ flex: "0 0 auto", height: 56, borderBottom: `1px solid ${color.border}`, background: "#fff", display: "flex", alignItems: "center", padding: "0 24px", gap: 12 }}>
        {crumbs ? (
          <div style={{ fontSize: 14, fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
            {crumbs.map((c, i) => (
              <span key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {i > 0 ? <Icon name="chevron-right" size={14} color={color.textFaint} /> : null}
                <span onClick={c.onClick} style={{ cursor: c.onClick ? "pointer" : "default", color: c.onClick ? color.brand : color.textStrong, fontWeight: i === crumbs.length - 1 ? 700 : 600 }}>{c.label}</span>
              </span>
            ))}
          </div>
        ) : (
          <div style={{ fontSize: 15, fontWeight: 700 }}>{TITLES[page] ?? "管理台"}</div>
        )}
        <div style={{ flex: 1 }} />
        {page === "model-assets" && table?.primary ? (
          <Button icon={table.primary.icon} onClick={() => setRegisterOpen(true)}>{table.primary.label}</Button>
        ) : null}
        {page === "users" && table?.primary ? (
          <Button icon={table.primary.icon} onClick={() => setWlAddOpen(true)}>{table.primary.label}</Button>
        ) : null}
      </header>

      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: 24 }}>
        <div style={{ maxWidth: 1080, margin: "0 auto" }}>
          {isTable && table ? (
            <>
              {page === "templates" && tplDrill && !mcpDrill ? (
                <div style={{ display: "flex", alignItems: "flex-start", gap: 10, background: color.brandTintBg, border: "1px solid rgba(22,131,255,.18)", borderRadius: radius.lg, padding: "11px 14px", marginBottom: 14, fontSize: 12, color: color.brandStrong, lineHeight: 1.6 }}>
                  <Icon name="info-circle" size={15} color={color.brand} />
                  <span>模板「{tplDrill.name}」的资产治理——此处治理该模板引用的平台资产；<b>Tool 标注为全局配置</b>，修改将影响引用同一 tool 的所有模板（30.6 拍板②）。</span>
                </div>
              ) : null}
              {actionErr ? (
                <div style={{ display: "flex", alignItems: "center", gap: 8, background: "#fdecec", border: "1px solid #f5c2c0", borderRadius: radius.lg, padding: "9px 13px", marginBottom: 12, fontSize: 12, color: color.dangerText }}>
                  <Icon name="alert-triangle" size={14} color={color.dangerText} />{actionErr}
                </div>
              ) : null}
              {tabs ? (
                <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
                  {tabs.map((tb) => (
                    <span key={tb.key}
                      style={{ fontSize: 12.5, fontWeight: 600, padding: "6px 14px", borderRadius: radius.md, color: color.textNav, background: "#eceef2" }}>
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
                          <span onClick={() => onCellAction(cell.onClickKey, r.id, String(r.cells[0]?.text ?? ""))} style={{ fontSize: 12, color: color.brand, fontWeight: 600, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}>{cell.text}<Icon name="chevron-right" size={13} color={color.brand} /></span>
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
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
                <div style={{ fontSize: 14, fontWeight: 700 }}>{traceFilter ? "Trace 串联回放" : "回放时间线 · 最近事件"}</div>
                <div style={{ flex: 1 }} />
                <input
                  placeholder="按 audit_trace_id 过滤（或点击事件的 trace 徽标）"
                  value={traceFilter}
                  onChange={(e) => setTraceFilter(e.target.value.trim())}
                  style={{ width: 320, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: "6px 10px", fontSize: 12, fontFamily: "ui-monospace, monospace" }}
                />
                {traceFilter ? (
                  <span onClick={() => setTraceFilter("")} style={{ fontSize: 12, color: color.brand, fontWeight: 600, cursor: "pointer" }}>清除过滤</span>
                ) : null}
              </div>
              {audit.map((n, i) => (
                <div key={i} style={{ display: "grid", gridTemplateColumns: "18px 1fr", gap: 10, paddingBottom: 12 }}>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
                    <div style={{ width: 10, height: 10, borderRadius: "50%", background: color.brand, marginTop: 3 }} />
                    {i < audit.length - 1 ? <div style={{ width: 2, flex: 1, marginTop: 3, background: color.border }} /> : null}
                  </div>
                  <div style={{ paddingBottom: 2 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontSize: 12.5, fontWeight: 600, color: color.textStrong, fontFamily: "ui-monospace, monospace" }}>{n.event}</span>
                      {n.trace ? (
                        <span onClick={() => setTraceFilter(n.trace!)} title={`按此 Trace 串联：${n.trace}`}
                          style={{ fontSize: 10.5, fontFamily: "ui-monospace, monospace", color: color.brandStrong, background: color.brandTintBg, padding: "1px 7px", borderRadius: radius.pill, cursor: "pointer" }}>
                          {n.trace.slice(0, 8)}
                        </span>
                      ) : null}
                    </div>
                    <div style={{ fontSize: 11.5, color: color.textSubtle, marginTop: 2 }}>{n.detail}</div>
                  </div>
                </div>
              ))}
              <div style={{ marginTop: 6, fontSize: 11.5, color: color.textSubtle }}>脱敏铁律：不显 Cookie / Secret / token / prompt 全文，只显 hash / request_id。</div>
            </div>
          ) : null}
        </div>
      </div>

      <ToolAnnotationSlideIn
        open={!!annotRow}
        row={annotRow}
        onClose={() => setAnnotRow(null)}
        onSaved={() => { setAnnotRow(null); void load(); }}
      />
      {registerOpen ? <RegisterModelDialog onClose={() => setRegisterOpen(false)} onSaved={() => { setRegisterOpen(false); void load(); }} /> : null}
      {grantsFor ? <ModelGrantsDialog target={grantsFor} onClose={() => setGrantsFor(null)} onSaved={() => { setGrantsFor(null); void load(); }} /> : null}
      <TemplateEditorModal open={!!tplEdit} templateId={tplEdit} onClose={() => setTplEdit(null)} onChanged={() => void load()} />
      {wlAddOpen ? <AddWhitelistDialog onClose={() => setWlAddOpen(false)} onSaved={() => { setWlAddOpen(false); void load(); }} /> : null}
    </>
  );
}

/** 加入白名单弹窗（B7·三）：user_id 必填；不存在的用户会自动建行（角色默认 user）。 */
function AddWhitelistDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [userId, setUserId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = () => {
    if (!userId.trim()) { setErr("user_id 必填（工号，如 0026xxxx）"); return; }
    setBusy(true);
    api.adminAddWhitelist(userId.trim(), displayName.trim())
      .then(onSaved)
      .catch((e) => setErr((e as Error).message))
      .finally(() => setBusy(false));
  };
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(21,25,35,.4)", zIndex: 60, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 380, background: "#fff", borderRadius: radius.xl, padding: 20, display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>加入白名单</div>
        <input placeholder="user_id（工号，必填）" value={userId} onChange={(e) => setUserId(e.target.value)}
          style={{ border: `1px solid ${color.border}`, borderRadius: radius.md, padding: "8px 10px", fontSize: 12.5, fontFamily: "ui-monospace, monospace" }} />
        <input placeholder="展示名（选填，缺省用 user_id）" value={displayName} onChange={(e) => setDisplayName(e.target.value)}
          style={{ border: `1px solid ${color.border}`, borderRadius: radius.md, padding: "8px 10px", fontSize: 12.5 }} />
        {err ? <div style={{ fontSize: 12, color: color.dangerText }}>{err}</div> : null}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
          <button onClick={onClose} style={{ height: 32, padding: "0 13px", border: `1px solid ${color.border}`, background: "#fff", borderRadius: radius.md, fontSize: 12, fontWeight: 600, color: color.textNav, cursor: "pointer" }}>取消</button>
          <button onClick={submit} disabled={busy} style={{ height: 32, padding: "0 14px", border: "none", background: color.brand, color: "#fff", borderRadius: radius.md, fontSize: 12, fontWeight: 700, cursor: "pointer", opacity: busy ? 0.7 : 1 }}>加入</button>
        </div>
      </div>
    </div>
  );
}

/** 注册模型接口（30.6 五）：display_name / model_id / base_url / secret_env_var / 协议 / 上下文长度 / access_scope。
 *  存前须「测试连接」通过（Key 走服务器环境变量探测）；走平台网关的模型（不填 base_url）可跳过测试直接存。 */
function RegisterModelDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ display_name: "", model_id: "", base_url: "", secret_env_var: "", access_scope: "all" });
  const [contextWindow, setContextWindow] = useState(DEFAULT_CONTEXT_WINDOW);
  const [err, setErr] = useState("");
  const test = useConnTest();
  // 探测相关字段（base_url/model_id/secret_env_var）一改即让上次测试失效
  const setField = (key: keyof typeof f, val: string) => {
    setF((d) => ({ ...d, [key]: val }));
    if (key === "base_url" || key === "model_id" || key === "secret_env_var") test.reset();
  };
  const input = (key: keyof typeof f, placeholder: string, mono = false) => (
    <input value={f[key]} onChange={(e) => setField(key, e.target.value)} placeholder={placeholder}
      style={{ width: "100%", height: 36, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, padding: "0 11px", fontSize: 12.5, outline: "none", fontFamily: mono ? "ui-monospace, monospace" : undefined, boxSizing: "border-box" }} />
  );

  const fieldsOk = Boolean(f.display_name.trim() && f.model_id.trim() && contextWindow > 0);
  const hasBaseUrl = Boolean(f.base_url.trim());
  // 有 base_url 必须测通才能存；走网关（不填 base_url）无端点可测，允许直接存
  const canSave = fieldsOk && (test.state === "ok" || !hasBaseUrl);

  const runTest = () => {
    if (!hasBaseUrl || !f.model_id.trim() || test.state === "testing") return;
    void test.run(() => api.testModelAssetConnection({ base_url: f.base_url.trim(), model_id: f.model_id.trim(), secret_env_var: f.secret_env_var.trim() }));
  };
  const save = () => {
    const env = f.secret_env_var.trim();
    if (env && !/^[A-Z][A-Z0-9_]{2,63}$/.test(env)) {
      setErr("「API Key 环境变量名」应填变量名（大写字母/数字/下划线，如 OPENOPS_PLATFORM_GLM_API_KEY）——看起来填入了 Key 本身：Key 不落库，请配到后端环境变量后在此填变量名");
      return;
    }
    api.adminRegisterModel({
      display_name: f.display_name.trim(), model_id: f.model_id.trim(),
      base_url: f.base_url.trim() || undefined, secret_env_var: env || undefined,
      context_window_tokens: contextWindow, access_scope: f.access_scope,
    }).then(onSaved).catch((e) => setErr((e as Error).message));
  };
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 60, background: "rgba(20,24,31,.42)", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 460, background: "#fff", borderRadius: radius.modal, padding: "22px 22px 18px" }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 14 }}>注册模型接口</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {input("display_name", "展示名，如：交易大模型-TX")}
          {input("model_id", "model_id，如：tx-llm-v2", true)}
          <div style={{ display: "flex", gap: 10 }}>
            <div style={{ flex: 1 }}>
              <div style={{ height: 36, display: "flex", alignItems: "center", padding: "0 11px", fontSize: 12.5, color: color.textNav, background: color.neutralBg, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, boxSizing: "border-box" }}>接口协议：{PROTOCOL_LABEL}</div>
            </div>
            <div style={{ flex: 1 }}>
              <input type="number" min={1} step={1000} value={contextWindow} onChange={(e) => setContextWindow(Number(e.target.value) || 0)} placeholder="上下文长度，默认 128000"
                style={{ width: "100%", height: 36, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, padding: "0 11px", fontSize: 12.5, outline: "none", boxSizing: "border-box" }} />
            </div>
          </div>
          {input("base_url", "OpenAI 兼容 endpoint（可空，用平台网关时）", true)}
          {input("secret_env_var", "API Key 环境变量名，如 OPENOPS_PLATFORM_GLM_API_KEY", true)}
          <div style={{ fontSize: 11.5, color: color.textSubtle, lineHeight: 1.5, marginTop: -4 }}>
            ⚠ 此处填<b>环境变量名</b>，不是 Key 本身——真实 Key 配在后端进程环境变量（run-backend 里
            <span style={{ fontFamily: "ui-monospace, monospace" }}> export 变量名=Key</span>），绝不落库。填了 base_url 须「测试连接」通过（Key 从环境变量取）才能注册。
          </div>
        </div>
        <div style={{ display: "flex", gap: 14, marginTop: 12, fontSize: 12.5 }}>
          {(["all", "restricted"] as const).map((s) => (
            <label key={s} style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
              <input type="radio" checked={f.access_scope === s} onChange={() => setF((d) => ({ ...d, access_scope: s }))} />
              {s === "all" ? "全员开放" : "限定人员（注册后配置授权）"}
            </label>
          ))}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
          <Button variant="secondary" icon={test.state === "testing" ? "loader-2" : "plug-connected"}
            disabled={!hasBaseUrl || !f.model_id.trim() || test.state === "testing"} onClick={runTest}>测试连接</Button>
          <ConnTestResult state={test.state} reason={test.reason} />
        </div>
        {err ? <div style={{ fontSize: 12, color: color.dangerText, marginTop: 10 }}>{err}</div> : null}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
          <Button variant="secondary" onClick={onClose}>取消</Button>
          <Button disabled={!canSave} onClick={save}
            title={!canSave && fieldsOk && hasBaseUrl ? "请先测试连接通过" : undefined}>注册</Button>
        </div>
      </div>
    </div>
  );
}

/** 白名单授权弹窗（30.6 五·人员勾选版，2026-07-09 拍板按人不按部门）。 */
function ModelGrantsDialog({ target, onClose, onSaved }: {
  target: { id: string; name: string }; onClose: () => void; onSaved: () => void;
}) {
  const [scope, setScope] = useState<"all" | "restricted">("all");
  const [users, setUsers] = useState<{ user_id: string; display_name: string }[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [err, setErr] = useState("");
  useEffect(() => {
    api.adminGetModelGrants(target.id).then((g) => { setScope(g.access_scope as "all" | "restricted"); setPicked(new Set(g.user_ids)); });
    api.adminListUsers().then(setUsers);
  }, [target.id]);
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 60, background: "rgba(20,24,31,.42)", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 480, background: "#fff", borderRadius: radius.modal, padding: "22px 22px 18px" }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>白名单授权 · {target.name}</div>
        <div style={{ fontSize: 12, color: color.textSubtle, marginBottom: 14, lineHeight: 1.6 }}>私有模型接口通常只对指定人员开放。设置后，仅授权范围内的用户可在实例配置与会话中选用该模型。</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 13 }}>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
            <input type="radio" checked={scope === "all"} onChange={() => setScope("all")} />
            全员开放 <span style={{ fontSize: 11.5, color: color.textSubtle }}>所有白名单用户可选用</span>
          </label>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
            <input type="radio" checked={scope === "restricted"} onChange={() => setScope("restricted")} />
            限定人员 <span style={{ fontSize: 11.5, color: color.textSubtle }}>仅勾选用户可选用</span>
          </label>
        </div>
        {scope === "restricted" ? (
          <div style={{ marginTop: 12, border: `1px solid ${color.border}`, borderRadius: radius.lg, maxHeight: 200, overflowY: "auto", padding: "6px 4px" }}>
            {users.map((u) => (
              <label key={u.user_id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 10px", fontSize: 12.5, cursor: "pointer" }}>
                <input type="checkbox" checked={picked.has(u.user_id)}
                  onChange={(e) => setPicked((p) => { const n = new Set(p); e.target.checked ? n.add(u.user_id) : n.delete(u.user_id); return n; })} />
                <span style={{ fontFamily: "ui-monospace, monospace" }}>{u.user_id}</span>
                <span style={{ color: color.textSubtle }}>{u.display_name}</span>
              </label>
            ))}
          </div>
        ) : null}
        {err ? <div style={{ fontSize: 12, color: color.dangerText, marginTop: 10 }}>{err}</div> : null}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
          <Button variant="secondary" onClick={onClose}>取消</Button>
          <Button onClick={() => {
            api.adminSaveModelGrants(target.id, scope, scope === "all" ? [] : [...picked])
              .then(onSaved).catch((e) => setErr((e as Error).message));
          }}>保存授权</Button>
        </div>
      </div>
    </div>
  );
}

/** 新原型：沙箱可调配置——只读 → 编辑中 → reason 必填 → 确认生效（写审计 runtime_config.updated）。 */
function SandboxPanel() {
  const [cfg, setCfg] = useState<SandboxCfg[]>([]);
  const [containers, setContainers] = useState<SandboxContainer[]>([]);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  const [saved, setSaved] = useState(false);

  const load = useCallback(() => {
    api.getSandboxCfg().then(setCfg);
    api.getSandboxContainers().then(setContainers).catch(() => setContainers([]));
  }, []);
  useEffect(load, [load]);

  const destroy = async (userId: string) => {
    const why = window.prompt(`强制销毁用户 ${userId} 的沙箱容器会中断其当前任务。请填写原因：`);
    if (!why || !why.trim()) return;
    try {
      await api.destroySandboxContainer(userId, why.trim());
      load();
    } catch (e) {
      setErr((e as Error).message);
    }
  };

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
      <div style={{ marginBottom: 18 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
          <div style={{ fontSize: 12.5, fontWeight: 700, color: color.textNav }}>
            用户容器（{containers.length} 个占用中 · 会话期常驻）
          </div>
          <button onClick={load} style={{ height: 28, padding: "0 11px", border: `1px solid ${color.border}`, background: "#fff", borderRadius: radius.sm, fontSize: 11.5, cursor: "pointer", color: color.textNav }}>刷新</button>
        </div>
        {containers.length === 0 ? (
          <div style={{ fontSize: 12, color: color.textSubtle, padding: "10px 0" }}>当前无活跃用户容器（用户开启会话时按需创建）。</div>
        ) : (
          <div style={{ border: `1px solid ${color.borderFaint}`, borderRadius: radius.md, overflow: "hidden" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1.4fr 0.8fr 0.7fr 1fr 0.7fr", gap: 8, padding: "7px 12px", background: color.neutralBg, fontSize: 11, fontWeight: 700, color: color.textSubtle }}>
              <span>user_id</span><span>状态</span><span>活跃 run</span><span>镜像</span><span>操作</span>
            </div>
            {containers.map((c) => (
              <div key={c.user_id} style={{ display: "grid", gridTemplateColumns: "1.4fr 0.8fr 0.7fr 1fr 0.7fr", gap: 8, padding: "8px 12px", borderTop: `1px solid ${color.borderFaint}`, fontSize: 12, alignItems: "center" }}>
                <span style={{ fontFamily: "ui-monospace, monospace" }}>{c.user_id}</span>
                <Pill tone={c.runtime_status === "active" ? "good" : "neutral"}>{c.runtime_status}</Pill>
                <span>{c.active_run_count}</span>
                <span style={{ fontFamily: "ui-monospace, monospace", fontSize: 11, color: color.textSubtle }}>{c.image_version}</span>
                <button onClick={() => destroy(c.user_id)} style={{ height: 26, padding: "0 9px", border: "1px solid #f3d9d7", background: "#fff", borderRadius: radius.sm, fontSize: 11, cursor: "pointer", color: color.dangerText }}>销毁</button>
              </div>
            ))}
          </div>
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
