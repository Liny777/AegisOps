import { useEffect, useState } from "react";
import { color, radius } from "../theme/tokens";
import { Modal, Icon, Pill } from "../ui";
import { api } from "../lib/api";

/** 模板编辑器（B7·二真化）：main role + default_tools 勾选（仅 allowed 平台 tool 可绑）、
 *  保存草稿（另存新版本，可反复改）/ 发布（草稿转正不可变；已实例化用户下次任务边界自动派生）。 */
export function TemplateEditorModal({ open, templateId, onClose, onChanged }: {
  open: boolean;
  templateId: string | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [tpl, setTpl] = useState<Record<string, unknown> | null>(null);
  const [activeVer, setActiveVer] = useState<Record<string, unknown> | null>(null);
  const [draftVer, setDraftVer] = useState<Record<string, unknown> | null>(null);
  const [role, setRole] = useState("");
  const [tools, setTools] = useState<Set<string>>(new Set());
  const [allowedTools, setAllowedTools] = useState<string[]>([]);
  const [content, setContent] = useState<Record<string, unknown>>({});
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(true); // 数据未回前禁写，防把空表单存成草稿/发布

  const load = async (tid: string) => {
    setBusy(true);
    const d = await api.getAdminTemplateDetail(tid);
    setTpl(d.template);
    setActiveVer(d.active_version);
    setDraftVer(d.draft_version);
    const base = (d.draft_version ?? d.active_version) as Record<string, unknown> | null;
    const c = ((base?.content_json ?? {}) as Record<string, unknown>);
    setContent(c);
    const main = (c.main ?? {}) as Record<string, unknown>;
    setRole(String(main.role ?? ""));
    setTools(new Set(((main.default_tools ?? []) as string[])));
    const toolsData = await api.getAdminMcpTools(null);
    setAllowedTools(toolsData.raw.filter((r) => r.annotation_id != null && r.annotation_status === "allowed").map((r) => String(r.tool_name)));
    setMsg("");
    setErr("");
    setBusy(false);
  };
  useEffect(() => { if (open && templateId) load(templateId).catch((e) => setErr((e as Error).message)); }, [open, templateId]);

  const buildContent = (): Record<string, unknown> => ({
    ...content,
    main: { ...((content.main ?? {}) as Record<string, unknown>), role: role.trim(), default_tools: [...tools] },
  });

  const saveDraft = () => {
    if (!templateId) return;
    setBusy(true);
    api.saveTemplateDraft(templateId, buildContent())
      .then((v) => { setDraftVer(v); setMsg(`草稿 v${v.version_no} 已保存（发布后生效）`); setErr(""); })
      .catch((e) => setErr((e as Error).message))
      .finally(() => setBusy(false));
  };
  const publish = () => {
    if (!templateId || !draftVer) return;
    setBusy(true);
    api.saveTemplateDraft(templateId, buildContent())  // 先落当前表单再发布，防未保存修改丢失
      .then((v) => api.publishTemplateVersion(String(v.template_version_id)))
      .then(() => load(templateId))  // 先刷新（load 会清 msg），再设成功提示
      .then(() => { setMsg("已发布：新实例按新版本创建；已实例化用户在下一次任务边界自动升级（保留其角色追加与资产绑定）。"); onChanged(); })
      .catch((e) => setErr((e as Error).message))
      .finally(() => setBusy(false));
  };

  const activeNo = activeVer ? `v${activeVer.version_no}` : "—";
  return (
    <Modal open={open} onClose={onClose} maxWidth={720}>
      <div style={{ flex: "0 0 auto", display: "flex", alignItems: "center", gap: 10, padding: "16px 20px", borderBottom: `1px solid ${color.border}` }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>{String(tpl?.display_name ?? "模板")}</div>
        <span style={{ fontSize: 11.5, fontWeight: 600, color: color.brandStrong, background: color.brandTintBg, padding: "2px 8px", borderRadius: radius.sm }}>{activeNo} · active</span>
        {draftVer ? <Pill tone="warning">草稿 v{String(draftVer.version_no)}</Pill> : null}
        <div style={{ flex: 1 }} />
        <button onClick={saveDraft} disabled={busy} style={{ height: 32, padding: "0 13px", border: `1px solid ${color.border}`, background: "#fff", borderRadius: radius.md, fontSize: 12, fontWeight: 600, color: color.textNav, cursor: "pointer" }}>保存草稿</button>
        <button onClick={publish} disabled={busy} title={draftVer ? "" : "发布会先保存当前修改为草稿"} style={{ height: 32, padding: "0 14px", border: "none", background: color.brand, color: "#fff", borderRadius: radius.md, fontSize: 12, fontWeight: 700, cursor: "pointer", opacity: busy ? 0.7 : 1 }}>发布</button>
        <Icon name="x" size={18} color="#697283" onClick={onClose} style={{ marginLeft: 4 }} />
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>
        <Box title="main Agent 默认 role（模板级，普通用户只能追加不可改）">
          <textarea
            value={role}
            onChange={(e) => setRole(e.target.value)}
            style={{ width: "100%", minHeight: 90, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: "10px 12px", fontSize: 12.5, lineHeight: 1.6, fontFamily: "inherit", outline: "none", boxSizing: "border-box" }}
          />
        </Box>
        <Box title="平台 MCP tool 绑定（仅 allowed 标注可绑；模板外工具运行时 fail-closed）">
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
            {allowedTools.map((t) => (
              <label key={t} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontFamily: "ui-monospace, monospace", border: `1px solid ${tools.has(t) ? color.brand : color.border}`, background: tools.has(t) ? color.brandTintBg : "#fff", padding: "5px 10px", borderRadius: radius.sm, cursor: "pointer" }}>
                <input type="checkbox" checked={tools.has(t)}
                  onChange={(e) => setTools((p) => { const n = new Set(p); e.target.checked ? n.add(t) : n.delete(t); return n; })} />
                {t}
              </label>
            ))}
          </div>
          <div style={{ fontSize: 11.5, color: color.textSubtle, marginTop: 8 }}>发布后版本不可原地改；再次修改须另存新草稿。</div>
        </Box>
        <Box title="sub Agent 组（D 块：管理员可编辑；skills/mcp_tools 为该子 Agent 的工具白名单）">
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {(((content.sub_agents ?? []) as Record<string, unknown>[])).map((s, i) => {
              const patch = (field: string, value: unknown) => {
                const subs = [...((content.sub_agents ?? []) as Record<string, unknown>[])];
                subs[i] = { ...subs[i], [field]: value };
                setContent({ ...content, sub_agents: subs });
              };
              const csv = (v: unknown) => ((v ?? []) as string[]).join(", ");
              const parse = (t: string) => t.split(/[,，]/).map((x) => x.trim()).filter(Boolean);
              return (
                <div key={String(s.key)} style={{ border: `1px solid ${color.borderFaint}`, borderRadius: radius.md, padding: "10px 12px", display: "flex", flexDirection: "column", gap: 6 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontSize: 12, fontWeight: 700, color: color.brandStrong, background: color.brandTintBg, padding: "2px 8px", borderRadius: radius.sm }}>{String(s.label ?? s.key)}</span>
                    <span style={{ fontSize: 11, color: color.textSubtle, fontFamily: "ui-monospace, monospace" }}>{String(s.key)}</span>
                    <span style={{ flex: 1 }} />
                    <label style={{ fontSize: 11.5, color: color.textSubtle }}>max_iters
                      <input type="number" min={1} max={200} value={Number(s.max_iters ?? 20)}
                        onChange={(e) => patch("max_iters", Number(e.target.value) || 20)}
                        style={{ width: 58, marginLeft: 6, border: `1px solid ${color.borderInput}`, borderRadius: radius.sm, padding: "2px 6px", fontSize: 12 }} />
                    </label>
                    <label style={{ fontSize: 11.5, color: color.textSubtle }} title="单条工具结果进上下文的保留 token；必须小于模型窗口，经验 ≤1/3（如 128k 窗口 ≤ 40000）">tool_result_limit
                      <input type="number" min={1000} max={200000} step={1000} value={Number(s.tool_result_limit ?? 24000)}
                        onChange={(e) => patch("tool_result_limit", Number(e.target.value) || 24000)}
                        style={{ width: 76, marginLeft: 6, border: `1px solid ${color.borderInput}`, borderRadius: radius.sm, padding: "2px 6px", fontSize: 12 }} />
                    </label>
                  </div>
                  <textarea value={String(s.role ?? "")} onChange={(e) => patch("role", e.target.value)} rows={2}
                    style={{ width: "100%", border: `1px solid ${color.borderInput}`, borderRadius: radius.sm, padding: "6px 8px", fontSize: 12, resize: "vertical", fontFamily: "inherit" }} />
                  <div style={{ display: "flex", gap: 8 }}>
                    <label style={{ flex: 1, fontSize: 11.5, color: color.textSubtle }}>skills（逗号分隔）
                      <input value={csv(s.skills)} onChange={(e) => patch("skills", parse(e.target.value))}
                        style={{ width: "100%", border: `1px solid ${color.borderInput}`, borderRadius: radius.sm, padding: "4px 8px", fontSize: 12, fontFamily: "ui-monospace, monospace" }} />
                    </label>
                    <label style={{ flex: 1, fontSize: 11.5, color: color.textSubtle }}>mcp_tools（逗号分隔）
                      <input value={csv(s.mcp_tools)} onChange={(e) => patch("mcp_tools", parse(e.target.value))}
                        style={{ width: "100%", border: `1px solid ${color.borderInput}`, borderRadius: radius.sm, padding: "4px 8px", fontSize: 12, fontFamily: "ui-monospace, monospace" }} />
                    </label>
                  </div>
                </div>
              );
            })}
          </div>
          <div style={{ fontSize: 11.5, color: color.textSubtle, marginTop: 8 }}>写工具可绑到子 Agent（E1 审批桥）：需人工审批的工具触发时弹审批卡（任务号带子 Agent 后缀），批准后由该子 Agent 继续执行；tool_result_limit 必须小于模型窗口（经验 ≤1/3）。</div>
        </Box>
        <Box title="模板默认 LLM">
          <div style={{ fontSize: 12.5, color: color.textBody, fontFamily: "ui-monospace, monospace" }}>
            {String(((content.default_llm ?? {}) as Record<string, unknown>).provider ?? "platform")} · {String(((content.default_llm ?? {}) as Record<string, unknown>).model ?? "—")}
          </div>
        </Box>
        {msg ? <div style={{ fontSize: 12, color: color.goodText, fontWeight: 600 }}>{msg}</div> : null}
        {err ? <div style={{ fontSize: 12, color: color.dangerText }}>{err}</div> : null}
      </div>
    </Modal>
  );
}

function Box({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ border: `1px solid ${color.border}`, borderRadius: radius.lg, padding: 14 }}>
      <div style={{ fontSize: 12.5, fontWeight: 700, color: color.textNav, marginBottom: 8 }}>{title}</div>
      {children}
    </div>
  );
}
