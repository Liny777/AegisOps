import { useEffect, useState } from "react";
import { color, radius, font } from "../theme/tokens";
import { SlideIn, OverlayHeader, Toggle, SegRadio } from "../ui";
import { api } from "../lib/api";

type ScopeMode = "none" | "optional" | "required";
type Status = "allowed" | "blocked";

/** 四号校验配置的表单态：service_id_by_tenant 用行数组编辑，保存时折回 Record。 */
interface TenantSidRow { tenant: string; sid: string }

const inputStyle: React.CSSProperties = {
  width: "100%", height: 36, border: `1px solid #dfe2e8`, borderRadius: radius.md,
  padding: "0 11px", fontSize: 13, fontFamily: font.mono, outline: "none",
  background: "#fff", boxSizing: "border-box",
};

/** MCP Tool 标注抽屉（管理员）：审批 / 四号校验（互斥）/ 密钥 / scope / appid 路径 / 状态。
 *  B7a：初始化自 catalog 行、保存走 PUT /admin/mcp-tools/{id}/annotation（此前为纯本地态）。
 *  标注全局一份（30.6 拍板②）：从任一模板 drill 进入编辑的是同一份。
 *  29.14：is_flow_check_required 与 is_approval_required 前端联动互斥（开一关一），
 *  开启四号校验时展开配置区（init/verify 只配 URI 路径，域名由工作台按 origin 动态拼接）。 */
export function ToolAnnotationSlideIn({ open, row, onClose, onSaved }: {
  open: boolean;
  row: Record<string, unknown> | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [approval, setApproval] = useState(true);
  const [flowCheck, setFlowCheck] = useState(false);
  const [fcInitPath, setFcInitPath] = useState("");
  const [fcVerifyPath, setFcVerifyPath] = useState("");
  const [fcInvokingMethod, setFcInvokingMethod] = useState("");
  const [fcObjectArgPath, setFcObjectArgPath] = useState("");
  const [fcTenantSids, setFcTenantSids] = useState<TenantSidRow[]>([{ tenant: "", sid: "" }]);
  const [secret, setSecret] = useState(false);
  const [scope, setScope] = useState<ScopeMode>("required");
  const [appidPath, setAppidPath] = useState("$.appid");
  const [status, setStatus] = useState<Status>("allowed");
  const [blockedReason, setBlockedReason] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!row) return;
    const fc = Boolean(row.is_flow_check_required ?? false);
    // 互斥：DB 已保证不同时为 true；四号开启时审批默认展示为关（避免行缺省 ?? true 造成两开）
    setApproval(fc ? false : Boolean(row.is_approval_required ?? true));
    setFlowCheck(fc);
    const cfg = (row.flow_check_config ?? {}) as Record<string, unknown>;
    setFcInitPath(String(cfg.init_path ?? ""));
    setFcVerifyPath(String(cfg.verify_path ?? ""));
    setFcInvokingMethod(String(cfg.invoking_method ?? ""));
    setFcObjectArgPath(String(cfg.object_arg_path ?? ""));
    const sids = (cfg.service_id_by_tenant ?? {}) as Record<string, unknown>;
    const rows = Object.entries(sids).map(([tenant, sid]) => ({ tenant, sid: String(sid ?? "") }));
    setFcTenantSids(rows.length ? rows : [{ tenant: "", sid: "" }]);
    setSecret(Boolean(row.is_secret_required ?? false));
    setScope(((row.scope_mode as ScopeMode) ?? "required"));
    setAppidPath(String(row.appid_arg_path ?? "$.appid"));
    setStatus((row.annotation_status as Status) === "blocked" ? "blocked" : "allowed");
    setBlockedReason(String(row.blocked_reason ?? ""));
    setErr("");
  }, [row]);

  // 互斥联动（29.14）：开审批自动关四号，反之亦然
  const onApprovalChange = (v: boolean) => { setApproval(v); if (v) setFlowCheck(false); };
  const onFlowCheckChange = (v: boolean) => { setFlowCheck(v); if (v) setApproval(false); };

  const save = () => {
    if (!row) return;
    if (scope === "required" && !appidPath.trim()) {
      setErr("scope=required 时 appid_arg_path 必填。");
      return;
    }
    const tenantSids = fcTenantSids.filter((r) => r.tenant.trim() && r.sid.trim());
    if (flowCheck) {
      for (const [label, v] of [["init_path", fcInitPath], ["verify_path", fcVerifyPath]] as const) {
        if (!v.trim().startsWith("/") || v.includes("://")) {
          setErr(`${label} 必须是以 / 开头的 URI 路径（不含域名）。`);
          return;
        }
      }
      if (!fcInvokingMethod.trim()) { setErr("invoking_method 必填。"); return; }
      if (!tenantSids.length) { setErr("至少配置一个租户的 service_id。"); return; }
      if (fcObjectArgPath.trim() && !fcObjectArgPath.trim().startsWith("$")) {
        setErr("object_arg_path 须为 $ 开头的 JSONPath（留空表示不提取）。");
        return;
      }
    }
    setBusy(true);
    api.adminSaveAnnotation(String(row.tool_catalog_id), {
      is_approval_required: approval,
      is_flow_check_required: flowCheck,
      flow_check_config: flowCheck ? {
        init_path: fcInitPath.trim(),
        verify_path: fcVerifyPath.trim(),
        invoking_method: fcInvokingMethod.trim(),
        service_id_by_tenant: Object.fromEntries(tenantSids.map((r) => [r.tenant.trim(), r.sid.trim()])),
        object_arg_path: fcObjectArgPath.trim(),
      } : {},
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
        <Row label="is_approval_required" desc="运行时编译为 AgentScope ASK（人工审批）">
          <Toggle on={approval} onChange={onApprovalChange} />
        </Row>
        <Row label="is_flow_check_required" desc="执行前需四号校验（风控二次认证，与审批互斥）">
          <Toggle on={flowCheck} onChange={onFlowCheckChange} />
        </Row>
        {flowCheck ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 14, padding: "12px 12px", border: `1px solid ${color.border}`, borderRadius: radius.md, background: color.surfaceAlt }}>
            <Field label="init_path（initialization 接口 URI）">
              <input value={fcInitPath} onChange={(e) => setFcInitPath(e.target.value)}
                placeholder="/rca/web/service/risk/control/orc/initialization" style={inputStyle} />
              <PathHint />
            </Field>
            <Field label="verify_path（flow-number-check 接口 URI）">
              <input value={fcVerifyPath} onChange={(e) => setFcVerifyPath(e.target.value)}
                placeholder="/rca/web/service/risk/control/orc/flow-number-check" style={inputStyle} />
            </Field>
            <Field label="invoking_method（调用方法标识）">
              <input value={fcInvokingMethod} onChange={(e) => setFcInvokingMethod(e.target.value)}
                placeholder="redisAgent.hotKeyAnalysis" style={inputStyle} />
            </Field>
            <Field label="service_id（按租户配置）">
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {fcTenantSids.map((r, i) => (
                  <div key={i} style={{ display: "flex", gap: 6 }}>
                    <input value={r.tenant} placeholder="租户 enterprise_id"
                      onChange={(e) => setFcTenantSids(fcTenantSids.map((x, j) => (j === i ? { ...x, tenant: e.target.value } : x)))}
                      style={{ ...inputStyle, flex: 1.4 }} />
                    <input value={r.sid} placeholder="serviceId"
                      onChange={(e) => setFcTenantSids(fcTenantSids.map((x, j) => (j === i ? { ...x, sid: e.target.value } : x)))}
                      style={{ ...inputStyle, flex: 1 }} />
                    <button onClick={() => setFcTenantSids(fcTenantSids.length > 1 ? fcTenantSids.filter((_, j) => j !== i) : [{ tenant: "", sid: "" }])}
                      title="删除该租户行"
                      style={{ flex: "0 0 auto", width: 32, border: `1px solid ${color.border}`, background: "#fff", borderRadius: radius.md, cursor: "pointer", color: color.textSubtle }}>×</button>
                  </div>
                ))}
                <button onClick={() => setFcTenantSids([...fcTenantSids, { tenant: "", sid: "" }])}
                  style={{ alignSelf: "flex-start", height: 28, padding: "0 12px", border: `1px dashed ${color.border}`, background: "#fff", borderRadius: radius.md, fontSize: 12, cursor: "pointer", color: color.textNav }}>+ 添加租户</button>
              </div>
            </Field>
            <Field label="object_arg_path（操作对象提取路径，选填）">
              <input value={fcObjectArgPath} onChange={(e) => setFcObjectArgPath(e.target.value)}
                placeholder="$.target.appid（JSONPath；留空表示不提取）" style={inputStyle} />
              <div style={{ fontSize: 11.5, color: color.textSubtle, marginTop: 5 }}>
                从工具入参提取操作对象，传给风控 SDK 用于展示/对象校验。
              </div>
            </Field>
          </div>
        ) : null}
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
            style={inputStyle}
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
              style={{ ...inputStyle, fontFamily: undefined }}
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

function PathHint() {
  return (
    <div style={{ fontSize: 11.5, color: color.textSubtle, marginTop: 5 }}>
      只配 URI 路径（不含域名）——实际请求域名由工作台按 window.location.origin 动态拼接。
    </div>
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
