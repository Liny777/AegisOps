import { useEffect, useState } from "react";
import { color, radius, font } from "../theme/tokens";
import { SlideIn, OverlayHeader, Toggle, SegRadio } from "../ui";
import { api } from "../lib/api";

type ScopeMode = "none" | "optional" | "required";
type Status = "allowed" | "blocked";

/** MCP Tool 标注抽屉（管理员）：5 字段——审批 / 密钥 / scope / appid 路径 / 状态。
 *  B7a：初始化自 catalog 行、保存走 PUT /admin/mcp-tools/{id}/annotation（此前为纯本地态）。
 *  标注全局一份（30.6 拍板②）：从任一模板 drill 进入编辑的是同一份。 */
export function ToolAnnotationSlideIn({ open, row, onClose, onSaved }: {
  open: boolean;
  row: Record<string, unknown> | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [approval, setApproval] = useState(true);
  const [secret, setSecret] = useState(false);
  const [scope, setScope] = useState<ScopeMode>("required");
  const [appidPath, setAppidPath] = useState("$.appid");
  const [status, setStatus] = useState<Status>("allowed");
  const [blockedReason, setBlockedReason] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!row) return;
    setApproval(Boolean(row.is_approval_required ?? true));
    setSecret(Boolean(row.is_secret_required ?? false));
    setScope(((row.scope_mode as ScopeMode) ?? "required"));
    setAppidPath(String(row.appid_arg_path ?? "$.appid"));
    setStatus((row.annotation_status as Status) === "blocked" ? "blocked" : "allowed");
    setBlockedReason(String(row.blocked_reason ?? ""));
    setErr("");
  }, [row]);

  const save = () => {
    if (!row) return;
    if (scope === "required" && !appidPath.trim()) {
      setErr("scope=required 时 appid_arg_path 必填。");
      return;
    }
    setBusy(true);
    api.adminSaveAnnotation(String(row.tool_catalog_id), {
      is_approval_required: approval,
      is_secret_required: secret,
      scope_mode: scope,
      appid_arg_path: appidPath.trim() || null,
      status,
      blocked_reason: status === "blocked" ? blockedReason.trim() || "管理员拉黑" : null,
    }).then(onSaved).catch((e) => setErr((e as Error).message)).finally(() => setBusy(false));
  };

  return (
    <SlideIn open={open} onClose={onClose} width={460}>
      <OverlayHeader title="Tool 标注" sub={String(row?.tool_name ?? "")} onClose={onClose} />
      <div style={{ flex: 1, overflowY: "auto", padding: "18px 20px", display: "flex", flexDirection: "column", gap: 18 }}>
        <Row label="is_approval_required" desc="运行时编译为 AgentScope ASK">
          <Toggle on={approval} onChange={setApproval} />
        </Row>
        <Row label="is_secret_required" desc="是否需要 SecretRef">
          <Toggle on={secret} onChange={setSecret} />
        </Row>
        <Field label="scope_mode">
          <SegRadio<ScopeMode>
            value={scope}
            onChange={setScope}
            options={[{ label: "none", value: "none" }, { label: "optional", value: "optional" }, { label: "required", value: "required" }]}
          />
        </Field>
        <Field label="appid_arg_path">
          <input
            value={appidPath}
            onChange={(e) => setAppidPath(e.target.value)}
            placeholder="$.appid / $.appids[*]"
            style={{ width: "100%", height: 36, border: `1px solid #dfe2e8`, borderRadius: radius.md, padding: "0 11px", fontSize: 13, fontFamily: font.mono, outline: "none", background: "#fff", boxSizing: "border-box" }}
          />
          {scope === "required" && !appidPath.trim() ? (
            <div style={{ fontSize: 11.5, color: color.dangerText, marginTop: 5 }}>scope=required 时 appid_arg_path 必填，否则运行时 block。</div>
          ) : null}
        </Field>
        <Field label="status">
          <SegRadio<Status>
            value={status}
            onChange={setStatus}
            options={[{ label: "allowed", value: "allowed" }, { label: "blocked", value: "blocked" }]}
          />
        </Field>
        {status === "blocked" ? (
          <Field label="blocked_reason">
            <input
              value={blockedReason}
              onChange={(e) => setBlockedReason(e.target.value)}
              placeholder="拉黑原因（写入标注，运行时 tool.blocked 展示）"
              style={{ width: "100%", height: 36, border: `1px solid #dfe2e8`, borderRadius: radius.md, padding: "0 11px", fontSize: 13, outline: "none", background: "#fff", boxSizing: "border-box" }}
            />
          </Field>
        ) : null}
        {err ? <div style={{ fontSize: 12, color: color.dangerText }}>{err}</div> : null}
      </div>
      <div style={{ flex: "0 0 auto", display: "flex", justifyContent: "flex-end", gap: 8, padding: "14px 20px", borderTop: `1px solid ${color.border}`, background: color.surfaceAlt }}>
        <button onClick={onClose} style={{ height: 36, padding: "0 16px", border: `1px solid ${color.border}`, background: "#fff", borderRadius: radius.md, fontSize: 13, fontWeight: 600, color: "#313844", cursor: "pointer" }}>取消</button>
        <button onClick={save} disabled={busy} style={{ height: 36, padding: "0 18px", border: "none", background: color.brand, color: "#fff", borderRadius: radius.md, fontSize: 13, fontWeight: 700, cursor: busy ? "wait" : "pointer", opacity: busy ? 0.7 : 1 }}>保存标注</button>
      </div>
    </SlideIn>
  );
}

function Row({ label, desc, children }: { label: string; desc: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
      <div>
        <div style={{ fontSize: 13, fontWeight: 600 }}>{label}</div>
        <div style={{ fontSize: 11.5, color: color.textSubtle }}>{desc}</div>
      </div>
      {children}
    </div>
  );
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 7 }}>{label}</div>
      {children}
    </div>
  );
}
