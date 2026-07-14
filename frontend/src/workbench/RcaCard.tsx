import { color, radius, shadow } from "../theme/tokens";
import { toneColor } from "../theme/tokens";
import { Icon, StatusBadge, Button } from "../ui";
import type { RcaCardData, RcaStep } from "../lib/api/types";

/** RCA 决策卡（对齐设计稿 + frontend-v2 rcaCatalog）：原地更新的可审计定界面板。 */
export function RcaCard({ rca, onContinue }: { rca: RcaCardData; onContinue?: () => void }) {
  return (
    <div
      className="oa-rca-card"
      style={{
        border: "1px solid #dbe3f0",
        borderRadius: radius.xxl,
        background: "#fff",
        boxShadow: shadow.card,
        overflow: "hidden",
        animation: "omPop .3s ease",
      }}
    >
      {/* header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 9,
          padding: "13px 16px",
          borderBottom: `1px solid ${color.borderInner}`,
          background: "linear-gradient(180deg,#fbfcfe,#fff)",
        }}
      >
        <div style={{ width: 28, height: 28, borderRadius: radius.md, background: color.brandTintBg, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Icon name="report-search" size={17} color={color.brand} />
        </div>
        <div style={{ fontSize: 14, fontWeight: 700 }}>RCA 决策面板 · {rca.title}</div>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, fontWeight: 600, color: color.brandStrong, background: color.brandTintBg, padding: "3px 9px", borderRadius: radius.pill, whiteSpace: "nowrap" }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: color.brand, animation: "omPulse 1.2s ease-in-out infinite" }} />
          {rca.phaseLabel}
        </span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 11.5, color: color.textSubtle }}>{rca.time}</span>
      </div>

      {/* incident tiles */}
      <div className="oa-rca-tiles" style={{ display: "grid", gap: 1, background: color.borderInner }}>
        {rca.tiles.map((t, i) => (
          <div key={i} style={{ background: "#fff", padding: "11px 13px" }}>
            <div style={{ fontSize: 11, color: color.textSubtle, marginBottom: 3 }}>{t.label}</div>
            <div style={{ fontSize: 12.5, fontWeight: 600, color: color.textStrong, lineHeight: 1.4 }}>{t.value}</div>
          </div>
        ))}
      </div>

      {/* stepper */}
      <div style={{ display: "flex", alignItems: "center", padding: "14px 16px", borderTop: `1px solid ${color.borderInner}`, borderBottom: `1px solid ${color.borderInner}`, overflowX: "auto" }}>
        {rca.steps.map((st, i) => (
          <Step key={st.num} step={st} last={i === rca.steps.length - 1} />
        ))}
      </div>

      <div style={{ padding: "15px 16px", display: "flex", flexDirection: "column", gap: 15 }}>
        {/* current question */}
        <div>
          <div style={{ fontSize: 12.5, color: color.textStrong, marginBottom: 4 }}>
            <span style={{ fontWeight: 700 }}>当前问题：</span>{rca.currentQ}
          </div>
          <div style={{ fontSize: 12, color: color.textMuted, lineHeight: 1.6 }}>
            <span style={{ fontWeight: 600, color: color.textSubtle }}>为什么问这个：</span>{rca.why}
          </div>
        </div>

        {/* facts / unknowns */}
        <div className="oa-rca-facts" style={{ display: "grid", gap: 12 }}>
          <div style={{ border: `1px solid ${color.goodBorder}`, borderRadius: radius.lg, padding: "11px 13px", background: color.goodBg }}>
            <div style={{ fontSize: 11.5, fontWeight: 700, color: color.goodText, marginBottom: 7, display: "flex", alignItems: "center", gap: 5 }}>
              <Icon name="checks" size={14} color={color.goodText} />已确认事实
            </div>
            {rca.facts.map((f, i) => (
              <div key={i} style={{ fontSize: 12, color: color.textBody, lineHeight: 1.5, padding: "2px 0", display: "flex", gap: 6 }}>
                <span style={{ color: color.good }}>·</span>{f.text}
              </div>
            ))}
          </div>
          <div style={{ border: `1px solid ${color.warningBorder}`, borderRadius: radius.lg, padding: "11px 13px", background: color.warningBg }}>
            <div style={{ fontSize: 11.5, fontWeight: 700, color: color.warningText, marginBottom: 7, display: "flex", alignItems: "center", gap: 5 }}>
              <Icon name="help-circle" size={14} color={color.warningText} />未知待验证
            </div>
            {rca.unknowns.map((u, i) => (
              <div key={i} style={{ fontSize: 12, color: color.textBody, lineHeight: 1.5, padding: "2px 0", display: "flex", gap: 6 }}>
                <span style={{ color: color.warning }}>·</span>{u.text}
              </div>
            ))}
          </div>
        </div>

        {/* evidence sources */}
        <div>
          <div style={{ fontSize: 11.5, fontWeight: 700, color: color.textNav, marginBottom: 7 }}>证据源状态</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {rca.sources.map((s, i) => (
              <StatusBadge key={i} label={s.name} value={s.status} tone={s.tone} running={s.status === "running"} />
            ))}
          </div>
        </div>

        {/* hypotheses */}
        <div>
          <div style={{ fontSize: 11.5, fontWeight: 700, color: color.textNav, marginBottom: 7 }}>假设排行</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            {rca.hypotheses.map((h, i) => {
              const tc = toneColor[h.tagTone];
              return (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, border: `1px solid ${color.border}`, borderRadius: radius.lg, padding: "9px 12px", background: "#fff" }}>
                  <span style={{ fontSize: 12.5, fontWeight: 600, color: color.textStrong, flex: 1 }}>{h.text}</span>
                  <span style={{ fontSize: 10.5, fontWeight: 600, color: tc.text, background: tc.bg, border: `1px solid ${tc.border}`, padding: "2px 7px", borderRadius: radius.sm, whiteSpace: "nowrap" }}>{h.tag}</span>
                  <div style={{ width: 96, height: 6, borderRadius: radius.pill, background: "#eaecf0", overflow: "hidden", flex: "0 0 96px" }}>
                    <div style={{ width: `${Math.round(h.conf * 100)}%`, height: "100%", background: tc.dot, borderRadius: radius.pill }} />
                  </div>
                  <span style={{ fontSize: 11.5, fontWeight: 700, color: color.textNav, width: 34, textAlign: "right" }}>{Math.round(h.conf * 100)}%</span>
                </div>
              );
            })}
          </div>
        </div>

        {/* next actions */}
        <div>
          <div style={{ fontSize: 11.5, fontWeight: 700, color: color.textNav, marginBottom: 7 }}>下一步行动</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {rca.actions.map((a, i) => {
              const tc = toneColor[a.statusTone];
              return (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, border: `1px solid ${color.border}`, borderRadius: radius.lg, padding: "10px 12px", background: "#fff" }}>
                  <span style={{ fontSize: 10.5, fontWeight: 700, color: color.brandStrong, background: color.brandTintBg, padding: "2px 8px", borderRadius: radius.sm, whiteSpace: "nowrap" }}>{a.tier}</span>
                  <span style={{ flex: 1, fontSize: 12.5, color: color.textStrong }}>{a.text}</span>
                  {a.confirm ? (
                    <span style={{ fontSize: 10.5, fontWeight: 600, color: color.warningText, background: color.warningChipBg, padding: "2px 7px", borderRadius: radius.sm, whiteSpace: "nowrap" }}>需确认</span>
                  ) : null}
                  <span style={{ fontSize: 11, color: color.textSubtle, whiteSpace: "nowrap" }}>影响：{a.impact}</span>
                  <span style={{ fontSize: 11, fontWeight: 600, color: tc.text }}>{a.status}</span>
                </div>
              );
            })}
          </div>
        </div>

        {/* conclusion */}
        <div style={{ border: `1px dashed #cdd4de`, borderRadius: radius.lg, padding: "12px 13px", background: color.surfaceTint }}>
          <div style={{ fontSize: 11.5, fontWeight: 700, color: color.textNav, marginBottom: 5 }}>最终结论</div>
          <div style={{ fontSize: 12.5, color: color.textBody, lineHeight: 1.6 }}>{rca.conclusion}</div>
          <div style={{ marginTop: 9 }}>
            <span title="Knowledge / RAG V1 禁用" style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, fontWeight: 600, color: color.textFaint, background: color.neutralBg, border: `1px solid ${color.border}`, padding: "5px 10px", borderRadius: radius.sm, cursor: "not-allowed" }}>
              <Icon name="bookmark" size={13} color={color.textFaint} />写入知识库<span style={{ fontSize: 10 }}>即将上线</span>
            </span>
          </div>
        </div>
      </div>

      {/* footer */}
      <div style={{ display: "flex", gap: 8, padding: "12px 16px", borderTop: `1px solid ${color.borderInner}`, background: color.surfaceAlt }}>
        <Button icon="player-play" onClick={onContinue} style={{ fontSize: 12.5, padding: "8px 14px" }}>继续验证 H1</Button>
        <Button variant="secondary" style={{ fontSize: 12.5, padding: "8px 14px" }}>采纳并生成恢复动作</Button>
      </div>
    </div>
  );
}

function Step({ step, last }: { step: RcaStep; last: boolean }) {
  const isActive = step.state === "active";
  const isDone = step.state === "done";
  const dotBg = isDone ? color.good : isActive ? color.brand : "#e6e8ec";
  const dotColor = isDone || isActive ? "#fff" : color.textSubtle;
  return (
    <div style={{ display: "flex", alignItems: "center", flex: 1 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        <div style={{ width: 22, height: 22, borderRadius: "50%", background: dotBg, color: dotColor, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11.5, fontWeight: 700, flex: "0 0 22px" }}>
          {isDone ? <Icon name="check" size={12} color="#fff" /> : step.num}
        </div>
        <span style={{ fontSize: 12.5, fontWeight: isActive ? 700 : 500, color: isActive ? color.brand : isDone ? color.textStrong : color.textSubtle, whiteSpace: "nowrap" }}>{step.label}</span>
      </div>
      {!last ? <div style={{ flex: 1, height: 2, margin: "0 8px", background: isDone ? color.good : "#e6e8ec", minWidth: 16 }} /> : null}
    </div>
  );
}
