import { useEffect, useRef, useState } from "react";
import { color, radius, toneColor } from "../theme/tokens";
import { Icon } from "../ui";
import type { RcaCardData } from "../lib/api/types";
import { remainingSeconds, type CheckpointCardState } from "../lib/checkpoint/model";

/** 假设 checkpoint 决策卡：假设生成（step=3）后弹出，添加假设 / 继续排查 / Ns 后自动继续。
 *
 *  倒计时纯展示（服务端权威判超时）：到 0 只把 chip 翻成「正在继续…」，不发请求——超时的
 *  closed 事件由服务端下发定格结果态。点「添加假设」先发 hold 冻结服务端计时（窗口延长，
 *  extended 事件回来更新 deadline），再展开输入框；不冻结的话 10s 根本打不完一条假设。 */
export function HypothesisCheckpointCard({ checkpoint, rca, onDecide, onExpired }: {
  checkpoint: CheckpointCardState;
  /** 诊断面板现值：卡片标题引用入选假设名（如 C01/C02/C03），面板缺失时省略。 */
  rca?: RcaCardData;
  onDecide: (action: "continue" | "add_hypothesis" | "hold", text?: string) => void;
  /** 倒计时归零回调（每张卡至多一次）。real 路径不传——超时由服务端 closed 事件定格；
   *  mock 路径无服务端，靠它本地翻 timed_out。 */
  onExpired?: () => void;
}) {
  const [composing, setComposing] = useState(false);
  const [draft, setDraft] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const expiredNotified = useRef(false);
  const { status } = checkpoint;
  const pending = status === "pending";

  useEffect(() => {
    if (!pending) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [pending]);

  const seconds = remainingSeconds(checkpoint.deadlineAt, now);
  useEffect(() => {
    if (pending && seconds <= 0 && !expiredNotified.current) {
      expiredNotified.current = true;
      onExpired?.();
    }
  }, [pending, seconds, onExpired]);
  const topNames = (rca?.hypotheses ?? [])
    .map((h) => h.text.match(/^\S+/)?.[0] ?? "")
    .filter(Boolean).slice(0, 3);
  const title = topNames.length
    ? `假设生成已完成（${topNames.join("/")}入选）。是否需要添加新假设？`
    : "假设生成已完成。是否需要添加新假设？";

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    onDecide("add_hypothesis", text);
  };

  return (
    <div
      className="oa-checkpoint-card"
      data-testid="hypothesis-checkpoint-card"
      data-checkpoint-status={checkpoint.status}
      style={{ border: `1px solid ${color.border}`, borderRadius: radius.xl, background: "#fff",
               boxShadow: "0 1px 3px rgba(20,24,31,.05)", padding: "14px 16px",
               animation: "omPop .25s ease" }}
    >
      <div style={{ fontSize: 13.5, fontWeight: 600, color: color.textStrong, lineHeight: 1.55 }}>
        {title}
      </div>

      {status === "pending" ? (
        <>
          <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
            {!composing ? (
              <button
                onClick={() => { setComposing(true); onDecide("hold"); }}
                style={{ display: "inline-flex", alignItems: "center", gap: 6, border: "none", cursor: "pointer",
                         background: color.brand, color: "#fff", fontSize: 12.5, fontWeight: 700,
                         padding: "8px 16px", borderRadius: radius.lg }}
              >
                <Icon name="bulb" size={15} color="#fff" />添加假设
              </button>
            ) : null}
            <button
              onClick={() => onDecide("continue")}
              style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer",
                       border: `1px solid ${color.borderInput}`, background: "#fff", color: color.textNav,
                       fontSize: 12.5, fontWeight: 600, padding: "8px 16px", borderRadius: radius.lg }}
            >
              <Icon name="player-play" size={14} color={color.textNav} />继续排查
            </button>
            <span
              className="oa-checkpoint-countdown"
              style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, fontWeight: 600,
                       color: color.warningText, background: color.warningChipBg,
                       padding: "5px 10px", borderRadius: radius.pill, whiteSpace: "nowrap" }}
            >
              <Icon name="clock" size={13} color={color.warningText} />
              {composing ? `输入中 · ${seconds}s 后自动继续`
                : seconds > 0 ? `${seconds}s 后自动继续` : "正在继续…"}
            </span>
          </div>

          {composing ? (
            <div style={{ marginTop: 10 }}>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="描述新假设（如：H5 网关连接池打满，导致上游超时重试放大）"
                rows={2}
                autoFocus
                style={{ width: "100%", border: `1px solid ${color.borderInput}`, borderRadius: radius.md,
                         padding: "8px 11px", fontSize: 12.5, lineHeight: 1.5, outline: "none",
                         background: "#fff", resize: "vertical" }}
              />
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <button
                  onClick={submit}
                  disabled={!draft.trim()}
                  style={{ border: "none", cursor: draft.trim() ? "pointer" : "not-allowed",
                           background: draft.trim() ? color.brand : "#c4cbd6", color: "#fff",
                           fontSize: 12.5, fontWeight: 700, padding: "7px 16px", borderRadius: radius.pill }}
                >
                  提交假设
                </button>
                <button
                  onClick={() => onDecide("continue")}
                  style={{ border: `1px solid ${color.borderInput}`, cursor: "pointer", background: "#fff",
                           color: color.textNav, fontSize: 12.5, fontWeight: 600, padding: "7px 14px",
                           borderRadius: radius.pill }}
                >
                  不加了，继续排查
                </button>
              </div>
            </div>
          ) : null}
        </>
      ) : (
        <ResultLine status={status} />
      )}
    </div>
  );
}

function ResultLine({ status }: { status: "continued" | "added" | "timed_out" }) {
  const tc = status === "timed_out" ? toneColor.warning : toneColor.good;
  const text = status === "added" ? "已补充假设 · 正在并入候选重排"
    : status === "timed_out" ? "超时未操作 · 已自动继续排查"
    : "已确认 · 继续排查";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 7, marginTop: 11, fontSize: 12.5,
                  fontWeight: 600, color: tc.text, background: tc.bg, border: `1px solid ${tc.border}`,
                  padding: "7px 12px", borderRadius: radius.md }}>
      <Icon name={status === "timed_out" ? "clock-check" : "circle-check"} size={15} color={tc.text} />
      {text}
    </div>
  );
}
