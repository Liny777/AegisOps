import { useState } from "react";
import { color, radius } from "../theme/tokens";
import { toneColor } from "../theme/tokens";
import { Icon } from "../ui";
import type { HitlCardData } from "../lib/api/types";

/** HITL / ASK 内联审批卡（映射 PermissionEngine ASK）。 */
export function HitlCard({ hitl, onDecide }: { hitl: HitlCardData; onDecide?: (d: "approved" | "rejected") => void }) {
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState<HitlCardData["status"]>(hitl.status);

  const decide = (d: "approved" | "rejected") => {
    setStatus(d);
    onDecide?.(d);
  };

  const tc = toneColor.warning;
  return (
    <div style={{ border: `1px solid ${tc.border}`, borderRadius: radius.xl, background: "#fff9ed", boxShadow: "0 1px 3px rgba(20,24,31,.05)", animation: "omPop .25s ease" }}>
      <div style={{ display: "grid", gridTemplateColumns: "28px 1fr", gap: 12, padding: "15px 16px" }}>
        <div style={{ width: 28, height: 28, borderRadius: radius.md, background: "rgba(193,138,32,.14)", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Icon name="shield-check" size={17} color={tc.text} />
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 13.5, fontWeight: 700 }}>{hitl.title}</span>
            <span style={{ fontSize: 11, fontWeight: 600, color: color.brandStrong, background: "#fff", border: `1px solid ${color.brandTintBorder}`, padding: "2px 8px", borderRadius: radius.sm, fontFamily: "ui-monospace, monospace" }}>{hitl.tool}</span>
            {status === "pending" ? <span style={{ fontSize: 11, color: tc.text, marginLeft: "auto" }}>剩余 {hitl.countdown}</span> : null}
          </div>
          <div style={{ fontSize: 13, color: color.textBody, lineHeight: 1.55, marginTop: 6 }}>{hitl.summary}</div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 16px", marginTop: 11 }}>
            {hitl.facts.map((f, i) => (
              <div key={i}>
                <div style={{ fontSize: 10.5, color: color.textSubtle }}>{f.label}</div>
                <div style={{ fontSize: 12, color: color.textStrong, fontWeight: 500, marginTop: 1 }}>{f.value}</div>
              </div>
            ))}
          </div>

          {status === "pending" ? (
            <>
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="确认理由（可选）"
                style={{ width: "100%", marginTop: 12, height: 34, border: `1px solid #dfe2e8`, borderRadius: radius.md, padding: "0 11px", fontSize: 12.5, outline: "none", background: "#fff" }}
              />
              <div style={{ display: "flex", gap: 8, marginTop: 11 }}>
                <button onClick={() => decide("rejected")} style={{ border: `1px solid ${color.borderInput}`, cursor: "pointer", background: "#fff", color: color.textNav, fontSize: 12.5, fontWeight: 600, padding: "8px 16px", borderRadius: radius.pill }}>拒绝</button>
                <button onClick={() => decide("approved")} style={{ border: "none", cursor: "pointer", background: color.brand, color: "#fff", fontSize: 12.5, fontWeight: 700, padding: "8px 18px", borderRadius: radius.pill }}>批准</button>
              </div>
            </>
          ) : (
            <ResultLine status={status} tool={hitl.tool} />
          )}
        </div>
      </div>
    </div>
  );
}

function ResultLine({ status, tool }: { status: "approved" | "rejected"; tool: string }) {
  const ok = status === "approved";
  const c = ok ? toneColor.good : toneColor.danger;
  // 按工具类型给结果文案：Bash 命令≠恢复动作（此前写死「恢复动作将经 Tool Gateway 执行」，内网走查指出）
  const approvedText = tool === "run_container_command"
    ? "已批准 · 命令将在你的容器内执行"
    : `已批准 · 「${tool}」将继续执行`;
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 7, marginTop: 12, fontSize: 12.5, fontWeight: 600, color: c.text, background: c.bg, border: `1px solid ${c.border}`, padding: "7px 12px", borderRadius: radius.md }}>
      <Icon name={ok ? "circle-check" : "circle-x"} size={15} color={c.text} />
      {ok ? approvedText : "已拒绝 · 当前工具调用终止，任务可继续"}
    </div>
  );
}
