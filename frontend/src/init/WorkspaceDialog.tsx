import { useEffect, useState } from "react";
import { color, radius } from "../theme/tokens";
import { Modal, OverlayHeader, Icon, Interactive, TextInput } from "../ui";
import { api } from "../lib/api";
import type { AppTreeNode } from "../lib/api/types";

/** 系统范围创建：4 级应用树（产品>子产品>模块>应用），越权 App fail-closed 不可选。 */
export function WorkspaceDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const [tree, setTree] = useState<AppTreeNode[]>([]);
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (open) api.getAppTree().then((t) => { setTree(t); setExpanded(new Set(collectIds(t))); });
  }, [open]);

  const toggleSel = (id: string) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleExp = (id: string) => setExpanded((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const canCreate = name.trim() && selected.size > 0;

  return (
    <Modal open={open} onClose={onClose} maxWidth={560}>
      <OverlayHeader title="创建系统范围" onClose={onClose} />
      <div style={{ flex: 1, overflowY: "auto", padding: "18px 20px" }}>
        <label style={{ fontSize: 13, fontWeight: 600, display: "block", marginBottom: 8 }}>范围名称</label>
        <TextInput value={name} onChange={setName} placeholder="例如：支付核心域" />
        <div style={{ fontSize: 13, fontWeight: 600, margin: "18px 0 8px" }}>选择应用（APPID）</div>
        <div style={{ fontSize: 11.5, color: color.warningText, background: color.warningBg, border: `1px solid ${color.warningBorder}`, borderRadius: radius.md, padding: "8px 11px", marginBottom: 10 }}>
          推荐使用有权限的 APPID 创建；无权限 App 已锁定（越权 fail-closed）。
        </div>
        <div style={{ border: `1px solid ${color.border}`, borderRadius: radius.lg, overflow: "hidden" }}>
          {tree.map((n) => <TreeRow key={n.id} node={n} depth={0} selected={selected} expanded={expanded} onSel={toggleSel} onExp={toggleExp} />)}
        </div>
      </div>
      <div style={{ flex: "0 0 auto", display: "flex", justifyContent: "flex-end", gap: 8, padding: "14px 20px", borderTop: `1px solid ${color.border}`, background: color.surfaceAlt }}>
        <button onClick={onClose} style={{ height: 36, padding: "0 16px", border: `1px solid ${color.border}`, background: "#fff", borderRadius: radius.md, fontSize: 13, fontWeight: 600, color: "#313844", cursor: "pointer" }}>取消</button>
        <button onClick={() => canCreate && onCreated("ws_new_" + Date.now().toString(36))} disabled={!canCreate}
          style={{ height: 36, padding: "0 18px", border: "none", background: color.brand, color: "#fff", borderRadius: radius.md, fontSize: 13, fontWeight: 700, cursor: canCreate ? "pointer" : "not-allowed", opacity: canCreate ? 1 : 0.5 }}>
          创建系统范围（{selected.size}）
        </button>
      </div>
    </Modal>
  );
}

function TreeRow({ node, depth, selected, expanded, onSel, onExp }: {
  node: AppTreeNode; depth: number; selected: Set<string>; expanded: Set<string>;
  onSel: (id: string) => void; onExp: (id: string) => void;
}) {
  const isApp = node.hasPermission !== undefined;
  const isOpen = expanded.has(node.id);
  const locked = isApp && node.hasPermission === false;
  const checked = selected.has(node.id);
  return (
    <>
      <div
        onClick={() => (isApp ? (!locked && onSel(node.id)) : onExp(node.id))}
        style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", paddingLeft: 12 + depth * 18, cursor: locked ? "not-allowed" : "pointer", background: checked ? color.brandTintBg : "transparent", borderBottom: `1px solid ${color.borderFaint}` }}
      >
        {!isApp ? <Icon name={isOpen ? "chevron-down" : "chevron-right"} size={15} color={color.textSubtle} /> : (
          <div style={{ width: 16, height: 16, borderRadius: 4, border: `1.5px solid ${locked ? "#d7dae0" : checked ? color.brand : "#cfd3da"}`, background: checked ? color.brand : "#fff", display: "flex", alignItems: "center", justifyContent: "center" }}>
            {checked ? <Icon name="check" size={11} color="#fff" /> : null}
          </div>
        )}
        <Icon name={isApp ? "app-window" : depth === 0 ? "box" : depth === 1 ? "stack-2" : "components"} size={15} color={locked ? color.textFaint : color.textSubtle} />
        <span style={{ flex: 1, fontSize: 12.5, fontWeight: isApp ? 500 : 600, color: locked ? color.textFaint : color.textStrong }}>{node.name}{isApp ? <span style={{ color: color.textSubtle, fontFamily: "ui-monospace, monospace", marginLeft: 6, fontSize: 11 }}>{node.id}</span> : null}</span>
        {locked ? <span style={{ display: "inline-flex", alignItems: "center", gap: 3, fontSize: 11, color: color.textFaint }}><Icon name="lock" size={12} color={color.textFaint} />无权限</span> : null}
      </div>
      {!isApp && isOpen ? node.children?.map((c) => <TreeRow key={c.id} node={c} depth={depth + 1} selected={selected} expanded={expanded} onSel={onSel} onExp={onExp} />) : null}
    </>
  );
}

function collectIds(nodes: AppTreeNode[]): string[] {
  return nodes.flatMap((n) => [n.id, ...(n.children ? collectIds(n.children) : [])]);
}
