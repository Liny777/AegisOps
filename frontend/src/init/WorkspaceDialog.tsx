import { useEffect, useState } from "react";
import { color, radius } from "../theme/tokens";
import { Modal, OverlayHeader, Icon, TextInput } from "../ui";
import { api } from "../lib/api";
import { ApiError } from "../lib/api/client";
import type { ScopeApp } from "../lib/api/types";

/** 系统范围创建/编辑：平铺应用列表（真实 APPID），勾选后落库为 workspace 范围。
 * editWorkspaceId 有值=编辑态：预填名称 + 预勾选当前已选应用；保存走 update。 */
export function WorkspaceDialog({ open, onClose, onSaved, editWorkspaceId }: {
  open: boolean; onClose: () => void; onSaved: (id: string) => void; editWorkspaceId?: string | null;
}) {
  const editing = !!editWorkspaceId;
  const [apps, setApps] = useState<ScopeApp[]>([]);
  const [loading, setLoading] = useState(false);
  const [name, setName] = useState("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!open) return;
    // busy 必须一并复位：组件常驻不卸载，上次提交成功后 busy 遗留 true → 重开按钮永远转圈（实测踩坑）
    setName(""); setQ(""); setSelected(new Set()); setErr(""); setBusy(false);
    setLoading(true);
    // 编辑态并取应用列表 + 范围详情（详情预填名称/勾选）；创建态只取应用列表
    Promise.all([api.getScopeApps(), editWorkspaceId ? api.getWorkspace(editWorkspaceId) : Promise.resolve(null)])
      .then(([a, detail]) => {
        setApps(a);
        if (detail) { setName(detail.name); setSelected(new Set(detail.app_ids)); }
      })
      .catch((e) => setErr(e instanceof ApiError ? e.message : editWorkspaceId ? "加载范围详情失败" : "加载应用列表失败"))
      .finally(() => setLoading(false));
  }, [open, editWorkspaceId]);

  const toggleSel = (id: string) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const kw = q.trim().toLowerCase();
  const filtered = kw ? apps.filter((a) => a.app_id.toLowerCase().includes(kw) || a.name.toLowerCase().includes(kw)) : apps;
  // 孤儿：已选但不在当前可见应用列表里的 app_id（编辑者权限变更/应用下线）——仍以已勾选保留，防静默缩小范围。
  const orphanIds = editing ? [...selected].filter((id) => !apps.some((a) => a.app_id === id)) : [];
  // 缺项提示：按钮为什么不可点必须让用户一眼看到（实测有人勾满应用却漏了名称，卡在灰按钮上）
  const missing = !name.trim() ? "先在上方填写范围名称" : selected.size === 0 ? "至少勾选一个应用" : "";
  const canSubmit = !missing && !busy;

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true); setErr("");
    try {
      // 从 selected 出发（含孤儿）；带应用中文名（→ umodel scopes[].projectCn）与 oModel 页面创建的展示一致
      const picked = [...selected].map((id) => {
        const a = apps.find((x) => x.app_id === id);
        return a ? { app_id: a.app_id, name: a.name, tenant_id: a.tenant_id } : { app_id: id };
      });
      const { workspace_id } = editing
        ? await api.updateWorkspace(editWorkspaceId as string, name.trim(), picked)
        : await api.createWorkspace(name.trim(), picked);
      onSaved(workspace_id);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : editing ? "保存失败，请重试" : "创建失败，请重试");
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} maxWidth={560}>
      <OverlayHeader title={editing ? "编辑系统范围" : "创建系统范围"} onClose={onClose} />
      <div style={{ flex: 1, overflowY: "auto", padding: "18px 20px", minHeight: 260 }}>
        <label style={{ fontSize: 13, fontWeight: 600, display: "block", marginBottom: 8 }}>
          范围名称<span style={{ color: color.dangerText, marginLeft: 4 }}>*</span>
        </label>
        <TextInput value={name} onChange={setName} placeholder="例如：支付核心域" />

        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", margin: "18px 0 8px" }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>选择应用（APPID）</span>
          <span style={{ fontSize: 11.5, color: color.textSubtle }}>已选 {selected.size}</span>
        </div>
        <div style={{ fontSize: 11.5, color: color.textSubtle, marginBottom: 8 }}>
          仅列出你有权限访问的应用；勾选的 APPID 组成该系统范围。
        </div>

        <div style={{ position: "relative", marginBottom: 8 }}>
          <Icon name="search" size={14} color={color.textFaint} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)" }} />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索应用名称或 APPID"
            style={{ width: "100%", height: 34, boxSizing: "border-box", padding: "0 10px 0 30px", border: `1px solid ${color.border}`, borderRadius: radius.md, fontSize: 12.5, background: color.surface, color: color.textStrong }}
          />
        </div>

        <div style={{ border: `1px solid ${color.border}`, borderRadius: radius.lg, overflow: "hidden", minHeight: 120 }}>
          {loading ? (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8, padding: "36px 0", color: color.textSubtle, fontSize: 12.5 }}>
              <Icon name="loader-2" size={15} color={color.textSubtle} spin />加载{editing ? "范围详情" : "应用列表"}…
            </div>
          ) : filtered.length === 0 && orphanIds.length === 0 ? (
            <div style={{ textAlign: "center", padding: "36px 0", color: color.textSubtle, fontSize: 12.5 }}>
              {apps.length === 0 ? "未查询到有权限的应用" : "无匹配的应用"}
            </div>
          ) : (
            <>
              {/* 孤儿行（不在可见列表但已选）：仅在未搜索时展示，可取消勾选以移出范围 */}
              {!kw && orphanIds.map((id) => (
                <div key={"orphan-" + id} onClick={() => toggleSel(id)}
                  style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", cursor: "pointer", background: color.brandTintBg, borderBottom: `1px solid ${color.borderFaint}` }}>
                  <div style={{ width: 16, height: 16, flex: "0 0 auto", borderRadius: 4, border: `1.5px solid ${color.brand}`, background: color.brand, display: "flex", alignItems: "center", justifyContent: "center" }}>
                    <Icon name="check" size={11} color="#fff" />
                  </div>
                  <Icon name="apps" size={15} color={color.textSubtle} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12.5, fontWeight: 500, color: color.textStrong, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>不在你可见的应用列表中</div>
                    <div style={{ fontSize: 11, color: color.textSubtle, fontFamily: "ui-monospace, monospace", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{id}</div>
                  </div>
                  <span style={{ flex: "0 0 auto", fontSize: 10.5, color: color.warningText, background: color.surfaceAlt, border: `1px solid ${color.border}`, borderRadius: radius.sm, padding: "2px 7px" }}>原有</span>
                </div>
              ))}
              {filtered.map((a) => {
                const checked = selected.has(a.app_id);
                return (
                  <div key={a.app_id} onClick={() => toggleSel(a.app_id)}
                    style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", cursor: "pointer", background: checked ? color.brandTintBg : "transparent", borderBottom: `1px solid ${color.borderFaint}` }}>
                    <div style={{ width: 16, height: 16, flex: "0 0 auto", borderRadius: 4, border: `1.5px solid ${checked ? color.brand : "#cfd3da"}`, background: checked ? color.brand : "#fff", display: "flex", alignItems: "center", justifyContent: "center" }}>
                      {checked ? <Icon name="check" size={11} color="#fff" /> : null}
                    </div>
                    <Icon name="apps" size={15} color={color.textSubtle} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12.5, fontWeight: 500, color: color.textStrong, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{a.name}</div>
                      <div style={{ fontSize: 11, color: color.textSubtle, fontFamily: "ui-monospace, monospace", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{a.app_id}</div>
                    </div>
                    {a.type ? <span style={{ flex: "0 0 auto", fontSize: 10.5, color: color.textSubtle, background: color.surfaceAlt, border: `1px solid ${color.border}`, borderRadius: radius.sm, padding: "2px 7px" }}>{a.type}</span> : null}
                  </div>
                );
              })}
            </>
          )}
        </div>
        {err ? <div style={{ marginTop: 10, fontSize: 12, color: color.dangerText }}>{err}</div> : null}
      </div>
      <div style={{ flex: "0 0 auto", display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 8, padding: "14px 20px", borderTop: `1px solid ${color.border}`, background: color.surfaceAlt }}>
        {missing && !busy ? (
          <span style={{ marginRight: "auto", display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, color: color.warningText }}>
            <Icon name="info-circle" size={13} color={color.warningText} />{missing}
          </span>
        ) : null}
        <button onClick={onClose} disabled={busy} style={{ height: 36, padding: "0 16px", border: `1px solid ${color.border}`, background: "#fff", borderRadius: radius.md, fontSize: 13, fontWeight: 600, color: "#313844", cursor: busy ? "not-allowed" : "pointer" }}>取消</button>
        <button onClick={submit} disabled={!canSubmit}
          style={{ height: 36, padding: "0 18px", border: "none", background: color.brand, color: "#fff", borderRadius: radius.md, fontSize: 13, fontWeight: 700, cursor: canSubmit ? "pointer" : "not-allowed", opacity: canSubmit ? 1 : 0.5, display: "inline-flex", alignItems: "center", gap: 6 }}>
          {busy ? <Icon name="loader-2" size={14} color="#fff" spin /> : null}{editing ? `保存修改（${selected.size}）` : `创建系统范围（${selected.size}）`}
        </button>
      </div>
    </Modal>
  );
}
