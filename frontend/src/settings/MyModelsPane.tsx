import { useCallback, useEffect, useState } from "react";
import { color, radius } from "../theme/tokens";
import { Button, Icon, Interactive, Modal, OverlayHeader, TextInput } from "../ui";
import { api } from "../lib/api";
import type { MyLlmConfig } from "../lib/api/types";
import { DEFAULT_CONTEXT_WINDOW, HeadersEditor, headersToRecord, PROTOCOL_LABEL, type HeaderRow } from "./AddCustomModelDialog";

/** 「我的模型」（设置 → 我的模型）：自带模型的看/改/删。
 *
 * 创建入口仍在初始化向导的「高级选项」（那里才有 API Key 输入），本页只管已建配置的后续维护——
 * 最常见的是改自定义 Header（内网网关策略变、租户标识调整）和纠正填错的 base_url。
 * 编辑不重输 API Key：Key 加密存 user_secret 永不回显，保存时由服务端解密后自动重探测。 */
export function MyModelsPane() {
  const [rows, setRows] = useState<MyLlmConfig[] | null>(null);
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState<MyLlmConfig | null>(null);

  const load = useCallback(() => {
    api.getMyLlmConfigs()
      .then((r) => { setRows(r); setErr(""); })
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : "加载失败"));
  }, []);
  useEffect(load, [load]);

  const remove = (row: MyLlmConfig) => {
    // 明说后果：删除不拦占用（运行时 fail-safe 降级），但仍绑着它的 Agent 会改用平台默认模型
    if (!window.confirm(
      `删除「${row.display_name}」？其 API Key 将一并作废，此操作不可撤销。\n\n`
      + "仍绑定该模型的 Agent 下次起任务会改用平台默认模型，并提示重新选择。")) return;
    api.deleteLlmConfig(row.llm_config_id)
      .then(() => { setErr(""); load(); })
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : "删除失败"));
  };

  return (
    <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "16px 20px" }}>
      <div style={{ fontSize: 12.5, color: color.textMuted, lineHeight: 1.7, marginBottom: 14 }}>
        这里管理你在初始化向导「高级选项」里接入的自带模型。API Key 加密保存、永不回显；保存修改时服务端会用它自动重新验证连接。
      </div>
      {err ? (
        <div style={{ display: "flex", alignItems: "flex-start", gap: 8, padding: "10px 12px", borderRadius: radius.lg, background: "#fdf3f3", border: "1px solid #f3c9c9", fontSize: 12, color: color.dangerText, lineHeight: 1.6, marginBottom: 12 }}>
          <Icon name="alert-triangle" size={15} color={color.dangerText} style={{ marginTop: 1, flex: "0 0 15px" }} />
          <span>{err}</span>
        </div>
      ) : null}

      {rows === null ? (
        <div style={{ fontSize: 12.5, color: color.textSubtle }}>加载中…</div>
      ) : rows.length === 0 ? (
        <EmptyState />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {rows.map((r) => <ModelRow key={r.llm_config_id} row={r} onEdit={() => setEditing(r)} onDelete={() => remove(r)} />)}
        </div>
      )}

      {editing ? (
        <EditModelDialog row={editing} onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); setErr(""); load(); }} />
      ) : null}
    </div>
  );
}

function EmptyState() {
  return (
    <div style={{ textAlign: "center", padding: "40px 24px", border: `1px dashed ${color.border}`, borderRadius: radius.xl, background: "rgba(247,248,250,.5)" }}>
      <div style={{ width: 48, height: 48, borderRadius: 14, background: "#fff", border: `1px solid ${color.border}`, display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 12 }}>
        <Icon name="sparkles" size={24} color={color.textFaint} />
      </div>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 6 }}>还没有自带模型</div>
      <div style={{ fontSize: 12.5, color: color.textMuted, lineHeight: 1.7 }}>
        在创建 Agent 的「高级选项：接入自带模型」里添加；添加后可在此修改地址、上下文长度与自定义 Header。
      </div>
    </div>
  );
}

function ModelRow({ row, onEdit, onDelete }: { row: MyLlmConfig; onEdit: () => void; onDelete: () => void }) {
  const headerCount = Object.keys(row.extra_headers || {}).length;
  return (
    <div style={{ border: `1px solid ${color.border}`, borderRadius: radius.xl, background: "#fff", padding: "13px 15px", display: "flex", alignItems: "center", gap: 14 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5 }}>
          <span style={{ fontSize: 13.5, fontWeight: 700 }}>{row.display_name}</span>
          {row.status !== "active" ? <Tag text="已禁用" tone="danger" /> : null}
          {row.supports_tool_calling ? null : <Tag text="无 tool calling" tone="danger" />}
          {headerCount > 0 ? <Tag text={`${headerCount} 个自定义 Header`} tone="brand" /> : null}
        </div>
        <div style={{ fontSize: 11.5, color: color.textSubtle, fontFamily: "ui-monospace, monospace", overflowWrap: "anywhere" }}>
          {row.model_name} · {row.base_url || "（未填地址）"} · {row.context_window_tokens.toLocaleString()} token
        </div>
      </div>
      <Interactive as="button" onClick={onEdit} title="编辑"
        baseStyle={{ border: `1px solid ${color.border}`, background: "#fff", cursor: "pointer", width: 32, height: 32, borderRadius: radius.md, display: "inline-flex", alignItems: "center", justifyContent: "center", color: color.textNav }}
        hoverStyle={{ background: color.pageBg }}>
        <Icon name="pencil" size={15} />
      </Interactive>
      <Interactive as="button" onClick={onDelete} title="删除"
        baseStyle={{ border: `1px solid ${color.border}`, background: "#fff", cursor: "pointer", width: 32, height: 32, borderRadius: radius.md, display: "inline-flex", alignItems: "center", justifyContent: "center", color: color.dangerText }}
        hoverStyle={{ background: "#fdf3f3" }}>
        <Icon name="trash" size={15} />
      </Interactive>
    </div>
  );
}

function Tag({ text, tone }: { text: string; tone: "brand" | "danger" }) {
  const c = tone === "brand" ? { fg: color.brandStrong, bg: color.brandTintBg } : { fg: color.dangerText, bg: "#fdf3f3" };
  return (
    <span style={{ fontSize: 11, fontWeight: 600, color: c.fg, background: c.bg, padding: "2px 7px", borderRadius: radius.pill }}>{text}</span>
  );
}

/** 编辑自带模型：PATCH 只提交真正改动的键。
 *  无「测试连接」按钮——那需要明文 API Key，而 Key 不回显；改连接类字段时服务端会自动重探测，
 *  探测不过则整条不保存并把原因回给这里（同创建期门禁，避免存下一个 active 但跑不通的配置）。 */
function EditModelDialog({ row, onClose, onSaved }: { row: MyLlmConfig; onClose: () => void; onSaved: () => void }) {
  const [displayName, setDisplayName] = useState(row.display_name);
  const [baseUrl, setBaseUrl] = useState(row.base_url);
  const [modelName, setModelName] = useState(row.model_name);
  const [contextWindow, setContextWindow] = useState(row.context_window_tokens || DEFAULT_CONTEXT_WINDOW);
  const [headerRows, setHeaderRows] = useState<HeaderRow[]>(
    Object.entries(row.extra_headers || {}).map(([name, value]) => ({ name, value })),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const headers = headersToRecord(headerRows);
  const headersChanged = JSON.stringify(headers) !== JSON.stringify(row.extra_headers || {});
  const fieldsOk = displayName.trim().length > 0 && /^https?:\/\/.+/.test(baseUrl.trim())
    && modelName.trim().length > 0 && contextWindow > 0;
  // 只提交真正改动的键：PATCH 语义下没变的字段不进 SET，也避免无谓触发服务端重探测
  const patch = {
    ...(displayName.trim() !== row.display_name ? { display_name: displayName.trim() } : {}),
    ...(baseUrl.trim() !== row.base_url ? { base_url: baseUrl.trim() } : {}),
    ...(modelName.trim() !== row.model_name ? { model_name: modelName.trim() } : {}),
    ...(contextWindow !== row.context_window_tokens ? { context_window_tokens: contextWindow } : {}),
    ...(headersChanged ? { extra_headers: headers } : {}),
  };
  const dirty = Object.keys(patch).length > 0;
  // 这几个键一改，服务端就要拿库里的 Key 重探测一次——提前告诉用户保存会慢一点、且可能被拒
  const willReprobe = "base_url" in patch || "model_name" in patch || "extra_headers" in patch;

  const submit = () => {
    if (!fieldsOk || !dirty || busy) return;
    setBusy(true);
    setError("");
    api.updateLlmConfig(row.llm_config_id, patch)
      .then(onSaved)
      .catch((e: unknown) => { setError(e instanceof Error ? e.message : "保存失败"); setBusy(false); });
  };

  return (
    <Modal open onClose={busy ? () => {} : onClose} maxWidth={480}>
      <OverlayHeader title="编辑自定义模型" sub={`${PROTOCOL_LABEL} · API Key 不可见，保持不变`} onClose={busy ? () => {} : onClose} />
      <div style={{ padding: "18px 20px", display: "flex", flexDirection: "column", gap: 14, overflowY: "auto" }}>
        <Field label="展示名"><TextInput value={displayName} onChange={setDisplayName} /></Field>
        <Field label="API 地址"><TextInput value={baseUrl} onChange={setBaseUrl} placeholder="https://api.example.com/v1" mono /></Field>
        <Field label="模型标识"><TextInput value={modelName} onChange={setModelName} placeholder="gpt-4o" mono /></Field>
        <Field label="上下文长度（token）">
          <input type="number" min={1} step={1000} value={contextWindow} onChange={(e) => setContextWindow(Number(e.target.value) || 0)}
            style={{ width: "100%", height: 36, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, padding: "0 11px", fontSize: 13, outline: "none", boxSizing: "border-box" }} />
        </Field>
        <Field label="自定义 Header（可选）">
          <HeadersEditor rows={headerRows} onChange={setHeaderRows} disabled={busy} />
        </Field>

        {error ? (
          <div style={{ display: "flex", alignItems: "flex-start", gap: 8, padding: "10px 12px", borderRadius: radius.lg, background: "#fdf3f3", border: "1px solid #f3c9c9", fontSize: 12, color: color.dangerText, lineHeight: 1.6 }}>
            <Icon name="alert-triangle" size={15} color={color.dangerText} style={{ marginTop: 1, flex: "0 0 15px" }} />
            <span>{error}</span>
          </div>
        ) : null}

        {willReprobe ? (
          <div style={{ padding: "10px 12px", borderRadius: radius.lg, background: color.brandTintBg, border: `1px solid rgba(22,131,255,.18)`, fontSize: 11.5, color: color.brandStrong, lineHeight: 1.6 }}>
            改动了地址 / 模型标识 / 自定义 Header：保存时会用已存的 API Key 重新验证连接，验不过则本次改动不保存。
          </div>
        ) : null}
      </div>
      <div style={{ flex: "0 0 auto", display: "flex", justifyContent: "flex-end", gap: 10, padding: "14px 20px", borderTop: `1px solid ${color.border}` }}>
        <Button variant="secondary" onClick={onClose} disabled={busy}>取消</Button>
        <Button icon={busy ? "loader-2" : "check"} disabled={!fieldsOk || !dirty || busy} onClick={submit}
          title={!dirty ? "没有改动" : undefined}>{busy ? "保存中…" : "保存"}</Button>
      </div>
    </Modal>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 6, color: color.textStrong }}>{label}</div>
      {children}
    </div>
  );
}
