// 接管确认卡（原型图2）：名称/提示词可编辑，类型/级别只读。草稿自持——取消回 armed 即卸载，
// 重开重新从 defaults 预填（不保留上次编辑）。
import { useEffect, useRef, useState } from "react";

import { color, font, radius } from "../../theme/tokens";
import { Button, TextInput } from "../../ui";
import { SEVERITY_LABEL } from "../../alerts/constants";
import type { TakeoverVm } from "./useAlertTakeover";
import "./AlertTakeover.css";

export function TakeoverConfirmCard({ vm }: { vm: TakeoverVm }) {
  const [name, setName] = useState(vm.defaults?.name ?? "");
  const [prompt, setPrompt] = useState(vm.defaults?.prompt ?? "");
  const rootRef = useRef<HTMLDivElement | null>(null);
  // 长对话尾部打开时把卡片滚进视口（nearest：已可见不动，避免抢用户滚动位置）
  useEffect(() => {
    rootRef.current?.scrollIntoView({ block: "nearest" });
  }, []);

  if (!vm.ctx) return null;
  const submitting = vm.phase === "submitting";
  return (
    <div
      ref={rootRef}
      className="oa-takeover-card"
      data-testid="alert-takeover-card"
      style={{ border: `1px solid ${color.border}`, borderRadius: radius.xl, background: "#fff", boxShadow: "0 1px 3px rgba(20,24,31,.05)", animation: "omPop .25s ease" }}
    >
      <div style={{ fontSize: 13.5, fontWeight: 700, color: color.textStrong }}>帮您确认此次告警接管策略：</div>

      <div>
        <div className="oa-takeover-label" style={{ fontSize: 11.5, fontWeight: 700, color: color.textSubtle, marginBottom: 5 }}>规则名称</div>
        <TextInput value={name} onChange={setName} placeholder="规则名称" />
      </div>

      <div className="oa-takeover-readonly-row">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 11.5, fontWeight: 700, color: color.textSubtle, marginBottom: 5 }}>策略类型</div>
          <div className="oa-takeover-readonly" style={{ border: `1px solid ${color.borderFaint}`, borderRadius: radius.md, background: color.surfaceAlt, fontSize: 12.5, color: color.textStrong }}>{vm.ctx.category}</div>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 11.5, fontWeight: 700, color: color.textSubtle, marginBottom: 5 }}>告警级别</div>
          <div className="oa-takeover-readonly" style={{ border: `1px solid ${color.borderFaint}`, borderRadius: radius.md, background: color.surfaceAlt, fontSize: 12.5, color: color.textStrong }}>{SEVERITY_LABEL[vm.ctx.severity]}</div>
        </div>
      </div>

      <div>
        <div style={{ fontSize: 11.5, fontWeight: 700, color: color.textSubtle, marginBottom: 5 }}>提示词</div>
        <textarea
          className="oa-takeover-prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={5}
          placeholder="诊断提示词（预填默认五步法口径，可按场景调整）"
          style={{ width: "100%", boxSizing: "border-box", border: `1px solid ${color.borderInput}`, borderRadius: radius.md, padding: "9px 11px", fontSize: 12.5, lineHeight: 1.7, fontFamily: font.sans, color: color.ink, background: "#fff", resize: "vertical", outline: "none" }}
        />
      </div>

      {vm.phase === "error" && vm.errorMessage ? (
        <div data-testid="alert-takeover-error" style={{ fontSize: 12, color: color.dangerText }}>{vm.errorMessage}</div>
      ) : null}

      <div className="oa-takeover-actions">
        <span style={{ flex: 1, minWidth: 0, fontSize: 12.5, color: color.textBody }}>请确认是否设置告警接管?</span>
        <Button variant="secondary" onClick={vm.cancel} disabled={submitting}>取消</Button>
        <Button icon={submitting ? "loader-2" : "check"} disabled={submitting} onClick={() => vm.confirm({ name, prompt })}>确认</Button>
      </div>
    </div>
  );
}
