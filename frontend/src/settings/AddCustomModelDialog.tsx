import { useState } from "react";
import { color, radius } from "../theme/tokens";
import { Modal, OverlayHeader, Button, TextInput, Icon } from "../ui";
import { api } from "../lib/api";

/**
 * 添加自定义模型（OpenAI 兼容）：录 Secret（明文仅此刻提交）→ 建 llm-config。
 * 服务端在建配置时做 egress SSRF 校验 + tool-calling 探测；失败以后端 message 直显，不激活。
 * onCreated 回传 llm_config_id + 展示名，供调用方重载列表/自动选中/绑为实例默认。
 */
export function AddCustomModelDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (llmConfigId: string, label: string) => void;
}) {
  const [displayName, setDisplayName] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://");
  const [modelName, setModelName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setDisplayName(""); setBaseUrl("https://"); setModelName(""); setApiKey("");
    setBusy(false); setError(null);
  };
  const close = () => { if (!busy) { reset(); onClose(); } };

  const ok =
    displayName.trim().length > 0 &&
    /^https?:\/\/.+/.test(baseUrl.trim()) &&
    modelName.trim().length > 0 &&
    apiKey.trim().length > 0;

  const submit = async () => {
    if (!ok || busy) return;
    setBusy(true);
    setError(null);
    try {
      const sec = await api.createSecret(`${displayName.trim()} Key`, apiKey.trim());
      const cfg = await api.createLlmConfig({
        display_name: displayName.trim(),
        base_url: baseUrl.trim(),
        model_name: modelName.trim(),
        secret_ref_id: sec.secret_ref_id,
      });
      onCreated(cfg.llm_config_id, displayName.trim());
      reset();
      onClose();
    } catch (e) {
      setError((e as Error).message || "创建失败");
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={close} maxWidth={480}>
      <OverlayHeader title="添加自定义模型" sub="OpenAI 兼容 · 需支持 tool calling" onClose={close} />
      <div style={{ padding: "18px 20px", display: "flex", flexDirection: "column", gap: 14, overflowY: "auto" }}>
        <Field label="展示名">
          <TextInput value={displayName} onChange={setDisplayName} placeholder="例：我的 GPT-4o" />
        </Field>
        <Field label="base_url">
          <TextInput value={baseUrl} onChange={setBaseUrl} placeholder="https://api.openai.com/v1" mono />
        </Field>
        <Field label="模型名（model）">
          <TextInput value={modelName} onChange={setModelName} placeholder="gpt-4o" mono />
        </Field>
        <Field label="API Key">
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="sk-…"
            autoComplete="off"
            style={{
              width: "100%", height: 36, border: `1px solid ${color.borderInput}`, borderRadius: radius.md,
              padding: "0 11px", fontSize: 13, outline: "none", boxSizing: "border-box",
              fontFamily: "ui-monospace, monospace",
            }}
          />
        </Field>

        {error ? (
          <div style={{ display: "flex", alignItems: "flex-start", gap: 8, padding: "10px 12px", borderRadius: radius.lg, background: "#fdf3f3", border: "1px solid #f3c9c9", fontSize: 12, color: color.dangerText, lineHeight: 1.6 }}>
            <Icon name="alert-triangle" size={15} color={color.dangerText} style={{ marginTop: 1, flex: "0 0 15px" }} />
            <span>{error}</span>
          </div>
        ) : null}

        <div style={{ padding: "10px 12px", borderRadius: radius.lg, background: color.brandTintBg, border: `1px solid rgba(22,131,255,.18)`, fontSize: 11.5, color: color.brandStrong, lineHeight: 1.6 }}>
          API Key 明文仅在此刻提交、加密存 user_secret，接口永不回显；创建时服务端会校验地址并探测能力，探测失败的模型不能激活。
        </div>
      </div>
      <div style={{ flex: "0 0 auto", display: "flex", justifyContent: "flex-end", gap: 10, padding: "14px 20px", borderTop: `1px solid ${color.border}` }}>
        <Button variant="secondary" onClick={close} disabled={busy}>取消</Button>
        <Button icon={busy ? "loader-2" : "plus"} disabled={!ok || busy} onClick={submit}>
          {busy ? "创建并探测中…" : "创建"}
        </Button>
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
