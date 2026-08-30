import { useCallback, useEffect, useRef, useState } from "react";
import { color, radius } from "../theme/tokens";
import { toneColor } from "../theme/tokens";
import { Icon } from "../ui";
import { api, API_MODE } from "../lib/api";
import { initRiskSdk, riskSdkAvailable, type RiskSdkInstance } from "../lib/riskSdk";
import type { FlowCheckCardData } from "../lib/api/types";
import "./HitlCard.css";

/** 四号校验卡（29.14）：恢复类工具执行前的风控二次认证。
 *
 * 弹窗 UI 由风控 SDK 内部管理，本卡是**状态容器**：挂载即拉起 SDK 弹窗（initialize→show），
 * 用户输入四号 → SDK verify 返回 {token, flowCode} → decideFlowCheck(approved) 回写后端唤醒
 * 运行时（Gateway 在调用边界注 header）。SDK 未部署/初始化失败 → 显式报错并自动走 rejected
 * （设计稿风险项 2/8：避免运行时干等超时）；verify 失败（输错/取消弹窗）保持 pending 可重试。
 * 布局复用 oa-hitl-* 类（HitlCard.css）：与审批卡同一视觉语言。 */
export function FlowCheckCard({ data, onDecided }: {
  data: FlowCheckCardData;
  onDecided?: (d: "approved" | "rejected" | "timeout") => void;
}) {
  const [status, setStatus] = useState<FlowCheckCardData["status"]>(data.status);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const sdkRef = useRef<RiskSdkInstance | null>(null);
  const launchedRef = useRef(false);      // StrictMode 双挂载守卫：只自动拉起一次弹窗
  const disposedRef = useRef(false);      // unmount 后在飞的 launch 不得再 show/setState
  const autoRejectedRef = useRef(false);  // SDK 缺席只自动拒绝一次

  // 决策结果统一按后端实际收口的 decision 显示（超时/异端已决竞态下可能 ≠ 请求值）
  const settle = useCallback((decided: string) => {
    const s: "approved" | "rejected" | "timeout" =
      decided === "approved" ? "approved" : decided === "timeout" ? "timeout" : "rejected";
    if (!disposedRef.current) setStatus(s);
    onDecided?.(s);
    return s;
  }, [onDecided]);

  const reject = useCallback(async () => {
    let decided = "rejected";
    if (API_MODE === "real") {
      try {
        decided = await api.decideFlowCheck(data.flow_check_request_id, "rejected");
      } catch { /* 网络失败按本端拒绝显示；后端超时兜底收口 */ }
    }
    settle(decided);
  }, [data.flow_check_request_id, settle]);

  const launch = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setErr("");
    try {
      const origin = window.location.origin;  // 标注只存 URI 路径，域名按当前环境动态拼（29.14 约束）
      if (!sdkRef.current) {
        const inst = await initRiskSdk({
          initUrl: `${origin}${data.initPath}`,
          serviceId: data.serviceId,
          invokingMethod: data.invokingMethod,
          operator: data.operator,
          enterpriseId: data.enterpriseId,
          ...(data.targetObject
            ? { targetObject: data.targetObject.value, targetObjectPath: data.targetObject.path }
            : {}),
        });
        if (disposedRef.current) { inst.destroy(); return; }  // 卡已卸载：不得拉起僵尸弹窗
        sdkRef.current = inst;
      }
      sdkRef.current.show();
      const { token, flowCode } = await sdkRef.current.verify(`${origin}${data.verifyPath}`);
      // verify 已通过就必须把结果送达后端（即使卡片已被重挂/卸载），后端是唯一权威
      const decided = API_MODE === "real"
        ? await api.decideFlowCheck(data.flow_check_request_id, "approved", token, flowCode)
        : "approved";
      const s = settle(decided);
      if (s !== "approved" && !disposedRef.current) {
        setErr(`后端已按「${s === "timeout" ? "超时" : "拒绝"}」收口，本次校验未生效`);
      }
    } catch (e) {
      if (disposedRef.current) return;
      setErr(`四号校验未完成：${(e as Error)?.message ?? String(e)}`);
      if (!riskSdkAvailable() && API_MODE === "real" && !autoRejectedRef.current) {
        autoRejectedRef.current = true;
        void reject();
      }
    } finally {
      if (!disposedRef.current) setBusy(false);
    }
  }, [busy, data, reject, settle]);

  useEffect(() => {
    disposedRef.current = false;  // StrictMode「装-卸-装」会先跑一次 cleanup，重挂时必须复位
    if (data.status === "pending" && !launchedRef.current) {
      launchedRef.current = true;
      void launch();
    }
    return () => {
      disposedRef.current = true;
      sdkRef.current?.destroy();
      sdkRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅挂载时自动拉起一次
  }, []);

  const tc = toneColor.warning;
  return (
    <div className="oa-hitl-card" style={{ border: `1px solid ${tc.border}`, borderRadius: radius.xl, background: "#fff9ed", boxShadow: "0 1px 3px rgba(20,24,31,.05)", animation: "omPop .25s ease" }}>
      <div className="oa-hitl-card-grid" style={{ display: "grid", gap: 12, padding: "15px 16px" }}>
        <div style={{ width: 28, height: 28, borderRadius: radius.md, background: "rgba(193,138,32,.14)", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Icon name="shield-lock" size={17} color={tc.text} />
        </div>
        <div className="oa-hitl-card-content" style={{ minWidth: 0 }}>
          <div className="oa-hitl-heading" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span className="oa-hitl-title" style={{ fontSize: 13.5, fontWeight: 700 }}>需要四号校验</span>
            <span className="oa-hitl-tool" style={{ fontSize: 11, fontWeight: 600, color: color.brandStrong, background: "#fff", border: `1px solid ${color.brandTintBorder}`, padding: "2px 8px", borderRadius: radius.sm, fontFamily: "ui-monospace, monospace" }}>{data.tool}</span>
            {status === "pending" ? <span className="oa-hitl-countdown" style={{ fontSize: 11, color: tc.text }}>剩余 {data.countdown}</span> : null}
          </div>
          <div className="oa-hitl-summary" style={{ fontSize: 13, color: color.textBody, lineHeight: 1.55, marginTop: 6 }}>{data.summary}</div>

          <div className="oa-hitl-facts" style={{ display: "grid", gap: "8px 16px", marginTop: 11 }}>
            {data.facts.map((f, i) => (
              <div className="oa-hitl-fact" key={i}>
                <div className="oa-hitl-fact-label" style={{ fontSize: 10.5, color: color.textSubtle }}>{f.label}</div>
                <div className="oa-hitl-fact-value" style={{ fontSize: 12, color: color.textStrong, fontWeight: 500, marginTop: 1 }}>{f.value}</div>
              </div>
            ))}
          </div>

          {err ? (
            <div style={{ marginTop: 10, fontSize: 12, color: toneColor.danger.text, background: toneColor.danger.bg, border: `1px solid ${toneColor.danger.border}`, padding: "7px 11px", borderRadius: radius.md }}>{err}</div>
          ) : null}

          {status === "pending" ? (
            <div className="oa-hitl-actions" style={{ display: "flex", gap: 8, marginTop: 11 }}>
              <button onClick={reject} disabled={busy} style={{ border: `1px solid ${color.borderInput}`, cursor: "pointer", background: "#fff", color: color.textNav, fontSize: 12.5, fontWeight: 600, padding: "8px 16px", borderRadius: radius.pill }}>拒绝</button>
              <button onClick={() => void launch()} disabled={busy} style={{ border: "none", cursor: "pointer", background: color.brand, color: "#fff", fontSize: 12.5, fontWeight: 700, padding: "8px 18px", borderRadius: radius.pill, opacity: busy ? 0.6 : 1 }}>
                {busy ? "校验中…" : "输入四号校验"}
              </button>
            </div>
          ) : (
            <FlowCheckResultLine status={status} tool={data.tool} />
          )}
        </div>
      </div>
    </div>
  );
}

function FlowCheckResultLine({ status, tool }: { status: "approved" | "rejected" | "timeout"; tool: string }) {
  const ok = status === "approved";
  const c = ok ? toneColor.good : status === "timeout" ? toneColor.warning : toneColor.danger;
  const text = ok
    ? `四号校验通过 · 「${tool}」将继续执行`
    : status === "timeout"
      ? "四号校验超时 · 当前工具调用终止，任务可继续"
      : "已拒绝 · 当前工具调用终止，任务可继续";
  return (
    <div className="oa-hitl-result" style={{ alignItems: "center", gap: 7, marginTop: 12, fontSize: 12.5, fontWeight: 600, color: c.text, background: c.bg, border: `1px solid ${c.border}`, padding: "7px 12px", borderRadius: radius.md }}>
      <Icon name={ok ? "circle-check" : status === "timeout" ? "clock-x" : "circle-x"} size={15} color={c.text} />
      {text}
    </div>
  );
}
