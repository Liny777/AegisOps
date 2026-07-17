import { useState } from "react";
import { color, radius } from "../theme/tokens";
import { Modal, OverlayHeader, Button, TextInput, Icon } from "../ui";
import { api } from "../lib/api";
import type { TestConnResult } from "../lib/api";

/**
 * 添加自定义模型（OpenAI 兼容）：录 Secret（明文仅此刻提交）→ 建 llm-config。
 * 保存前须先「测试连接」通过（egress + tool-calling 探测）；接口协议固定 OpenAI Compatible；
 * 上下文长度默认 128000。onCreated 回传 llm_config_id + 展示名。
 */
export const PROTOCOL_LABEL = "OpenAI Compatible";
export const DEFAULT_CONTEXT_WINDOW = 128000;

/** 「测试连接」状态机（自带/平台模型的保存门共用）：测通(ok)才允许保存；相关字段一改即失效。
 *  mock=后端未真实打网（OPENOPS_LLM_PROBE=mock）：仍放行保存（否则离线开发全断），但 UI 须如实说明。 */
export function useConnTest() {
  const [state, setState] = useState<"idle" | "testing" | "ok" | "fail">("idle");
  const [reason, setReason] = useState<string | null>(null);
  const [mock, setMock] = useState(false);
  const reset = () => { setState("idle"); setReason(null); setMock(false); };
  const run = async (fn: () => Promise<TestConnResult>) => {
    setState("testing"); setReason(null); setMock(false);
    try {
      const r = await fn();
      setState(r.ok ? "ok" : "fail");
      setReason(r.ok ? null : (r.reason ?? "连接失败"));
      setMock(r.probe_mode === "mock");
    } catch (e) {
      setState("fail"); setReason((e as Error).message || "测试失败");
    }
  };
  return { state, reason, mock, reset, run };
}

/** 测试结果行（通过=绿、mock 通过=黄「未真实探测」、失败=红原因、测试中=转圈）。 */
export function ConnTestResult({ state, reason, mock }: { state: "idle" | "testing" | "ok" | "fail"; reason: string | null; mock?: boolean }) {
  if (state === "idle") return null;
  if (state === "testing") return <div style={{ fontSize: 12, color: color.textSubtle, display: "inline-flex", alignItems: "center", gap: 6 }}><Icon name="loader-2" size={13} spin />测试中…</div>;
  // mock 通过≠验过：地址与网络策略真校验了，但 Key 与模型能力没验——别给绿勾，否则又是一次「以为测过了」
  if (state === "ok" && mock) {
    return <div style={{ fontSize: 12, color: color.warningText, display: "inline-flex", alignItems: "flex-start", gap: 6, lineHeight: 1.5 }}><Icon name="alert-triangle" size={13} color={color.warningText} style={{ marginTop: 1, flex: "0 0 13px" }} /><span>未真实探测（服务端 mock 模式）：地址与网络策略已校验，但未验证 API Key 与模型能力</span></div>;
  }
  if (state === "ok") return <div style={{ fontSize: 12, color: color.goodText ?? "#0a7d3f", display: "inline-flex", alignItems: "center", gap: 6 }}><Icon name="circle-check" size={13} color={color.goodText ?? "#0a7d3f"} />连接正常，可保存</div>;
  return <div style={{ fontSize: 12, color: color.dangerText, display: "inline-flex", alignItems: "flex-start", gap: 6, lineHeight: 1.5 }}><Icon name="circle-x" size={13} color={color.dangerText} style={{ marginTop: 1, flex: "0 0 13px" }} /><span>{reason ?? "连接失败"}</span></div>;
}

/** 提交链共享（本弹窗与初始化向导内联表单共用）：录 Secret → 建 llm-config → 回传 id+label。 */
export async function submitCustomLlm(f: {
  displayName: string; baseUrl: string; modelName: string; apiKey: string; contextWindow?: number;
}): Promise<{ id: string; label: string }> {
  const sec = await api.createSecret(`${f.displayName.trim()} Key`, f.apiKey.trim());
  const cfg = await api.createLlmConfig({
    display_name: f.displayName.trim(),
    base_url: f.baseUrl.trim(),
    model_name: f.modelName.trim(),
    secret_ref_id: sec.secret_ref_id,
    context_window_tokens: f.contextWindow ?? DEFAULT_CONTEXT_WINDOW,
  });
  return { id: cfg.llm_config_id, label: f.displayName.trim() };
}
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
  const [contextWindow, setContextWindow] = useState(DEFAULT_CONTEXT_WINDOW);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const test = useConnTest();

  const reset = () => {
    setDisplayName(""); setBaseUrl("https://"); setModelName(""); setApiKey("");
    setContextWindow(DEFAULT_CONTEXT_WINDOW); setBusy(false); setError(null); test.reset();
  };
  const close = () => { if (!busy) { reset(); onClose(); } };

  // 探测相关字段（base_url/模型名/Key）一改即让上次「测试连接」失效，必须重测才能保存。
  const onBaseUrl = (v: string) => { setBaseUrl(v); test.reset(); };
  const onModelName = (v: string) => { setModelName(v); test.reset(); };
  const onApiKey = (v: string) => { setApiKey(v); test.reset(); };

  const fieldsOk =
    displayName.trim().length > 0 &&
    /^https?:\/\/.+/.test(baseUrl.trim()) &&
    modelName.trim().length > 0 &&
    apiKey.trim().length > 0 &&
    contextWindow > 0;
  const canSave = fieldsOk && test.state === "ok";  // 只有测试连接通过才能保存

  const runTest = () => {
    if (!fieldsOk || test.state === "testing") return;
    void test.run(() => api.testLlmConnection({ base_url: baseUrl.trim(), model_name: modelName.trim(), api_key: apiKey.trim() }));
  };

  const submit = async () => {
    if (!canSave || busy) return;
    setBusy(true);
    setError(null);
    try {
      const created = await submitCustomLlm({ displayName, baseUrl, modelName, apiKey, contextWindow });
      onCreated(created.id, created.label);
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
        <Field label="接口协议">
          <div style={{ height: 36, display: "flex", alignItems: "center", padding: "0 11px", fontSize: 13, color: color.textNav, background: color.neutralBg, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, boxSizing: "border-box" }}>
            {PROTOCOL_LABEL}<span style={{ marginLeft: 8, fontSize: 11.5, color: color.textSubtle }}>（当前仅支持）</span>
          </div>
        </Field>
        <Field label="base_url">
          <TextInput value={baseUrl} onChange={onBaseUrl} placeholder="https://api.openai.com/v1" mono />
        </Field>
        <Field label="模型名（model）">
          <TextInput value={modelName} onChange={onModelName} placeholder="gpt-4o" mono />
        </Field>
        <Field label="上下文长度（token）">
          <input
            type="number" min={1} step={1000}
            value={contextWindow}
            onChange={(e) => setContextWindow(Number(e.target.value) || 0)}
            placeholder="128000"
            style={{
              width: "100%", height: 36, border: `1px solid ${color.borderInput}`, borderRadius: radius.md,
              padding: "0 11px", fontSize: 13, outline: "none", boxSizing: "border-box",
            }}
          />
        </Field>
        <Field label="API Key">
          <input
            type="password"
            value={apiKey}
            onChange={(e) => onApiKey(e.target.value)}
            placeholder="sk-…"
            autoComplete="off"
            style={{
              width: "100%", height: 36, border: `1px solid ${color.borderInput}`, borderRadius: radius.md,
              padding: "0 11px", fontSize: 13, outline: "none", boxSizing: "border-box",
              fontFamily: "ui-monospace, monospace",
            }}
          />
        </Field>

        {/* 测试连接：必须测通才能保存 */}
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Button variant="secondary" icon={test.state === "testing" ? "loader-2" : "plug-connected"}
            disabled={!fieldsOk || test.state === "testing"} onClick={runTest}>测试连接</Button>
          <ConnTestResult state={test.state} reason={test.reason} mock={test.mock} />
        </div>

        {error ? (
          <div style={{ display: "flex", alignItems: "flex-start", gap: 8, padding: "10px 12px", borderRadius: radius.lg, background: "#fdf3f3", border: "1px solid #f3c9c9", fontSize: 12, color: color.dangerText, lineHeight: 1.6 }}>
            <Icon name="alert-triangle" size={15} color={color.dangerText} style={{ marginTop: 1, flex: "0 0 15px" }} />
            <span>{error}</span>
          </div>
        ) : null}

        <div style={{ padding: "10px 12px", borderRadius: radius.lg, background: color.brandTintBg, border: `1px solid rgba(22,131,255,.18)`, fontSize: 11.5, color: color.brandStrong, lineHeight: 1.6 }}>
          API Key 明文仅在此刻提交、加密存 user_secret，接口永不回显；须先「测试连接」通过（校验地址+探测 tool calling 能力）才能保存。
        </div>
      </div>
      <div style={{ flex: "0 0 auto", display: "flex", justifyContent: "flex-end", gap: 10, padding: "14px 20px", borderTop: `1px solid ${color.border}` }}>
        <Button variant="secondary" onClick={close} disabled={busy}>取消</Button>
        <Button icon={busy ? "loader-2" : "plus"} disabled={!canSave || busy} onClick={submit}
          title={!canSave && fieldsOk ? "请先测试连接通过" : undefined}>
          {busy ? "创建中…" : "创建"}
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
