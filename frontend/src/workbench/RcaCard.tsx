import { color, radius, shadow } from "../theme/tokens";
import { toneColor } from "../theme/tokens";
import { Icon, StatusBadge, Button } from "../ui";
import type { RcaCardData, RcaStep } from "../lib/api/types";
import "./RcaCard.css";

/** 卡片按钮点击后要以用户身份发出的消息（可见可审计，B4 用户拍板）。 */
export interface RcaCardAction {
  kind: "continue" | "adopt";
  message: string;
}

/** 假设文本的前缀（如 "H1"）；无空格分隔时整段截前 8 字符兜底。 */
function hypothesisPrefix(text: string): string {
  return text.match(/^\S+/)?.[0]?.slice(0, 8) ?? text.slice(0, 8);
}

/** RCA 决策卡（对齐设计稿 + frontend-v2 rcaCatalog）：原地更新的可审计定界面板。
 *  增量面板早期字段稀疏——空列表分节整块隐藏；完成态判定只认 status==="concluded"
 *  （后端权威），不用 steps 全 done 推断。 */
export function RcaCard({
  rca,
  live = false,
  actionsEnabled = true,
  onAction,
}: {
  rca: RcaCardData;
  /** 任务运行中且未闭环：active 步圆点与相位 chip 脉冲。 */
  live?: boolean;
  /** false = 按钮保留但禁用（任务运行中 / 程序化发送挂起）。 */
  actionsEnabled?: boolean;
  /** 缺省（如 run closed）时整个 footer 不渲染。 */
  onAction?: (action: RcaCardAction) => void;
}) {
  const concluded = rca.status === "concluded";
  const activeStep = rca.steps.find((step) => step.state === "active");
  const topHypothesis = rca.hypotheses.length
    ? [...rca.hypotheses].sort((a, b) => b.conf - a.conf)[0]
    : undefined;
  const pendingConfirmAction = rca.actions.find(
    (action) => action.confirm && action.status !== "已执行",
  );
  // footer 数据驱动：主按钮=未闭环时「继续验证 top1 假设」；副按钮=存在需确认且未执行的动作。
  const continueAction: RcaCardAction | undefined = !concluded && topHypothesis
    ? {
      kind: "continue",
      message: `继续验证假设 ${topHypothesis.text}：请给出下一步验证证据与结论。`,
    }
    : undefined;
  const adoptAction: RcaCardAction | undefined = pendingConfirmAction
    ? {
      kind: "adopt",
      message: `采纳当前定界结论，请生成并发起恢复动作「${pendingConfirmAction.text}」（高危动作走审批流程）。`,
    }
    : undefined;
  const showFooter = Boolean(onAction && (continueAction || adoptAction));

  return (
    <div
      className="oa-rca-card"
      data-testid="rca-card"
      data-rca-revision={rca.revision ?? ""}
      data-rca-status={rca.status ?? ""}
      style={{
        border: "1px solid #dbe3f0",
        borderRadius: radius.xxl,
        background: "#fff",
        boxShadow: shadow.card,
        // 不能 overflow:hidden——stepper 需要相对活动面板滚动容器 sticky。
        animation: "omPop .3s ease", // 仅首挂播放：revision 更新原地重渲染，不重挂不重播
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
          borderRadius: `${radius.xxl} ${radius.xxl} 0 0`,
        }}
      >
        <div style={{ width: 28, height: 28, borderRadius: radius.md, background: color.brandTintBg, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Icon name="report-search" size={17} color={color.brand} />
        </div>
        <div style={{ fontSize: 14, fontWeight: 700 }}>RCA 决策面板{rca.title ? ` · ${rca.title}` : ""}</div>
        {rca.phaseLabel ? (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, fontWeight: 600, color: color.brandStrong, background: color.brandTintBg, padding: "3px 9px", borderRadius: radius.pill, whiteSpace: "nowrap" }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: concluded ? color.good : color.brand, animation: live ? "omPulse 1.2s ease-in-out infinite" : undefined }} />
            {rca.phaseLabel}
          </span>
        ) : null}
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 11.5, color: color.textSubtle }}>{rca.time}</span>
      </div>

      {/* incident tiles */}
      {rca.tiles.length ? (
        <div className="oa-rca-tiles" style={{ display: "grid", gap: 1, background: color.borderInner }}>
          {rca.tiles.map((t, i) => (
            <div key={i} style={{ background: "#fff", padding: "11px 13px" }}>
              <div style={{ fontSize: 11, color: color.textSubtle, marginBottom: 3 }}>{t.label}</div>
              <div style={{ fontSize: 12.5, fontWeight: 600, color: color.textStrong, lineHeight: 1.4 }}>{t.value}</div>
            </div>
          ))}
        </div>
      ) : null}

      {/* stepper：实时进度是这张卡的主角——sticky 常驻面板顶部，读屏用 role=status 通报 */}
      <div
        className="oa-rca-stepper"
        role="status"
        aria-label={concluded
          ? "定界进度：已完成"
          : activeStep
            ? `定界进度：第${activeStep.num}步 · ${activeStep.label}`
            : "定界进度"}
        style={{ display: "flex", alignItems: "center", padding: "14px 16px", borderTop: `1px solid ${color.borderInner}`, borderBottom: `1px solid ${color.borderInner}`, overflowX: "auto" }}
      >
        {rca.steps.map((st, i) => (
          <Step key={st.num} step={st} last={i === rca.steps.length - 1} live={live} />
        ))}
      </div>

      <div style={{ padding: "15px 16px", display: "flex", flexDirection: "column", gap: 15 }}>
        {/* current question */}
        {rca.currentQ || rca.why ? (
          <div>
            {rca.currentQ ? (
              <div style={{ fontSize: 12.5, color: color.textStrong, marginBottom: 4 }}>
                <span style={{ fontWeight: 700 }}>当前问题：</span>{rca.currentQ}
              </div>
            ) : null}
            {rca.why ? (
              <div style={{ fontSize: 12, color: color.textMuted, lineHeight: 1.6 }}>
                <span style={{ fontWeight: 600, color: color.textSubtle }}>为什么问这个：</span>{rca.why}
              </div>
            ) : null}
          </div>
        ) : null}

        {/* facts / unknowns */}
        {rca.facts.length || rca.unknowns.length ? (
          <div className="oa-rca-facts" style={{ display: "grid", gap: 12 }}>
            {rca.facts.length ? (
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
            ) : null}
            {rca.unknowns.length ? (
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
            ) : null}
          </div>
        ) : null}

        {/* evidence sources */}
        {rca.sources.length ? (
          <div>
            <div style={{ fontSize: 11.5, fontWeight: 700, color: color.textNav, marginBottom: 7 }}>证据源状态</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {rca.sources.map((s, i) => (
                <StatusBadge key={i} label={s.name} value={s.status} tone={s.tone} running={s.status === "running"} />
              ))}
            </div>
          </div>
        ) : null}

        {/* hypotheses */}
        {rca.hypotheses.length ? (
          <div>
            <div style={{ fontSize: 11.5, fontWeight: 700, color: color.textNav, marginBottom: 7 }}>假设排行</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
              {rca.hypotheses.map((h, i) => {
                const tc = toneColor[h.tagTone];
                return (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, border: `1px solid ${color.border}`, borderRadius: radius.lg, padding: "9px 12px", background: "#fff" }}>
                    <span style={{ fontSize: 12.5, fontWeight: 600, color: color.textStrong, flex: 1 }}>{h.text}</span>
                    {h.tag ? (
                      <span style={{ fontSize: 10.5, fontWeight: 600, color: tc.text, background: tc.bg, border: `1px solid ${tc.border}`, padding: "2px 7px", borderRadius: radius.sm, whiteSpace: "nowrap" }}>{h.tag}</span>
                    ) : null}
                    <div style={{ width: 96, height: 6, borderRadius: radius.pill, background: "#eaecf0", overflow: "hidden", flex: "0 0 96px" }}>
                      <div style={{ width: `${Math.round(h.conf * 100)}%`, height: "100%", background: tc.dot, borderRadius: radius.pill }} />
                    </div>
                    <span style={{ fontSize: 11.5, fontWeight: 700, color: color.textNav, width: 34, textAlign: "right" }}>{Math.round(h.conf * 100)}%</span>
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}

        {/* next actions */}
        {rca.actions.length ? (
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
                    {a.impact ? (
                      <span style={{ fontSize: 11, color: color.textSubtle, whiteSpace: "nowrap" }}>影响：{a.impact}</span>
                    ) : null}
                    <span style={{ fontSize: 11, fontWeight: 600, color: tc.text }}>{a.status}</span>
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}

        {/* conclusion */}
        {rca.conclusion ? (
          <div style={{ border: `1px dashed #cdd4de`, borderRadius: radius.lg, padding: "12px 13px", background: color.surfaceTint }}>
            <div style={{ fontSize: 11.5, fontWeight: 700, color: color.textNav, marginBottom: 5 }}>最终结论</div>
            <div style={{ fontSize: 12.5, color: color.textBody, lineHeight: 1.6 }}>{rca.conclusion}</div>
            <div style={{ marginTop: 9 }}>
              <span title="Knowledge / RAG V1 禁用" style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, fontWeight: 600, color: color.textFaint, background: color.neutralBg, border: `1px solid ${color.border}`, padding: "5px 10px", borderRadius: radius.sm, cursor: "not-allowed" }}>
                <Icon name="bookmark" size={13} color={color.textFaint} />写入知识库<span style={{ fontSize: 10 }}>即将上线</span>
              </span>
            </div>
          </div>
        ) : null}
      </div>

      {/* footer：数据驱动；run closed（onAction 缺省）或无可用动作时整块不渲染 */}
      {showFooter ? (
        <div style={{ display: "flex", gap: 8, padding: "12px 16px", borderTop: `1px solid ${color.borderInner}`, background: color.surfaceAlt, borderRadius: `0 0 ${radius.xxl} ${radius.xxl}` }}>
          {continueAction && topHypothesis ? (
            <Button
              icon="player-play"
              disabled={!actionsEnabled}
              onClick={() => onAction?.(continueAction)}
              style={{ fontSize: 12.5, padding: "8px 14px" }}
            >
              继续验证 {hypothesisPrefix(topHypothesis.text)}
            </Button>
          ) : null}
          {adoptAction ? (
            <Button
              variant="secondary"
              disabled={!actionsEnabled}
              onClick={() => onAction?.(adoptAction)}
              style={{ fontSize: 12.5, padding: "8px 14px" }}
            >
              采纳并生成恢复动作
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Step({ step, last, live }: { step: RcaStep; last: boolean; live: boolean }) {
  const isActive = step.state === "active";
  const isDone = step.state === "done";
  const dotBg = isDone ? color.good : isActive ? color.brand : "#e6e8ec";
  const dotColor = isDone || isActive ? "#fff" : color.textSubtle;
  return (
    <div style={{ display: "flex", alignItems: "center", flex: 1 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        <div style={{ width: 22, height: 22, borderRadius: "50%", background: dotBg, color: dotColor, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11.5, fontWeight: 700, flex: "0 0 22px", animation: live && isActive ? "omPulse 1.2s ease-in-out infinite" : undefined }}>
          {isDone ? <Icon name="check" size={12} color="#fff" /> : step.num}
        </div>
        <span style={{ fontSize: 12.5, fontWeight: isActive ? 700 : 500, color: isActive ? color.brand : isDone ? color.textStrong : color.textSubtle, whiteSpace: "nowrap" }}>{step.label}</span>
      </div>
      {!last ? <div style={{ flex: 1, height: 2, margin: "0 8px", background: isDone ? color.good : "#e6e8ec", minWidth: 16 }} /> : null}
    </div>
  );
}
