import { useEffect, useRef, useState, type ReactNode } from "react";
import { color, radius, shadow, toneColor } from "../theme/tokens";
import { Icon, StatusBadge, Button } from "../ui";
import type { RcaCardData, RcaStep } from "../lib/api/types";
import type { RecoveryAction, RecoveryActionPhase, RecoveryState } from "../lib/rca/recovery";
import "./DiagnosisTimeline.css";

/** 时间线按钮点击后要以用户身份发出的消息（可见可审计，B4 用户拍板）。 */
export interface RcaCardAction {
  kind: "continue" | "adopt";
  message: string;
}

/** 假设文本的前缀（如 "H1"）；无空格分隔时整段截前 8 字符兜底。 */
function hypothesisPrefix(text: string): string {
  return text.match(/^\S+/)?.[0]?.slice(0, 8) ?? text.slice(0, 8);
}

/** 步骤展示元数据：参考竞品风格的双语标题（后端 label 仍为 范围/证据/…，仅前端展示映射）。 */
const STEP_META: Record<number, { title: string; en: string; icon: string }> = {
  1: { title: "诊断范围", en: "SCOPE", icon: "target" },
  2: { title: "证据收集", en: "EVIDENCE", icon: "list-search" },
  3: { title: "假设生成", en: "HYPOTHESIS", icon: "bulb" },
  4: { title: "验证", en: "TEST", icon: "flask" },
  5: { title: "根因报告", en: "REPORT", icon: "report" },
  // 第六节点：恢复执行（审批/工具事件驱动，不属于五步法——进度口径仍是 N/5）
  6: { title: "恢复执行", en: "RECOVER", icon: "first-aid-kit" },
};

/** 恢复相位 → 展示文案与配色（approved/executing 走品牌蓝 + live 脉冲）。 */
const RECOVERY_PHASE_META: Record<RecoveryActionPhase, {
  label: string;
  dot: string;
  text: string;
  bg: string;
  /** 进行中相位：圆点脉冲。 */
  live?: boolean;
}> = {
  pending: { label: "待审批", dot: color.warning, text: color.warningText, bg: color.warningChipBg },
  approved: { label: "已批准", dot: color.brand, text: color.brandStrong, bg: color.brandTintBg, live: true },
  executing: { label: "执行中", dot: color.brand, text: color.brandStrong, bg: color.brandTintBg, live: true },
  executed: { label: "已执行", dot: color.good, text: color.goodText, bg: color.goodBg },
  rejected: { label: "已拒绝", dot: color.danger, text: color.dangerText, bg: toneColor.danger.bg },
  timeout: { label: "审批超时", dot: color.warning, text: color.warningText, bg: color.warningChipBg },
  failed: { label: "执行失败", dot: color.danger, text: color.dangerText, bg: toneColor.danger.bg },
};

/** ISO 时间 → HH:MM（审批明细行展示用）；无效输入返回空串不渲染。 */
function formatClock(iso?: string): string {
  if (!iso) return "";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "";
  return `${String(parsed.getHours()).padStart(2, "0")}:${String(parsed.getMinutes()).padStart(2, "0")}`;
}

/** 收起态一行摘要的兜底：step.summary 缺失（旧快照/后端未上报）时从面板既有字段推导。
 *  仅对 done/active 步调用——currentQ/conclusion/假设是跨步全局字段，waiting 步展示它们
 *  会把「尚未开始」的步伪装成已有产出（demo rev1 时步 5 显示结论之类的自相矛盾）。 */
function fallbackSummary(rca: RcaCardData, num: number, state: RcaStep["state"]): string {
  switch (num) {
    case 1:
      return rca.tiles.length
        ? rca.tiles.map((tile) => `${tile.label} ${tile.value}`).join(" · ")
        : rca.title;
    case 2:
      return rca.facts.length || rca.unknowns.length || rca.sources.length
        ? `已确认事实 ${rca.facts.length} · 待验证 ${rca.unknowns.length} · 证据源 ${rca.sources.length}`
        : "";
    case 3: {
      const top = rca.hypotheses.length
        ? [...rca.hypotheses].sort((a, b) => b.conf - a.conf)[0]
        : undefined;
      return top ? `${top.text} · 置信 ${Math.round(top.conf * 100)}%` : "";
    }
    case 4:
      return rca.currentQ || (state === "done" ? "验证已完成" : "验证进行中");
    case 5:
      return rca.conclusion.length > 60 ? `${rca.conclusion.slice(0, 60)}…` : rca.conclusion;
    default:
      return "";
  }
}

/** 诊断五步法垂直时间线（替代旧 RcaCard 大卡混排）：每步一块、可点击展开，
 *  顺序感由左侧 rail（绿勾 / 蓝底当前步 / 灰待办 + 竖连线）承载。
 *  增量面板早期字段稀疏——空列表分节整块隐藏；完成态判定只认 status==="concluded"
 *  （后端权威），不用 steps 全 done 推断。 */
export function DiagnosisTimeline({
  rca,
  live = false,
  actionsEnabled = true,
  onAction,
  recovery,
}: {
  rca: RcaCardData;
  /** 任务运行中且未闭环：active 步圆点与相位 chip 脉冲。 */
  live?: boolean;
  /** false = 按钮保留但禁用（任务运行中 / 程序化发送挂起）。 */
  actionsEnabled?: boolean;
  /** 缺省（如 run closed）时整个 footer 不渲染。 */
  onAction?: (action: RcaCardAction) => void;
  /** 第六节点「恢复执行」状态（审批/工具事件推导）；缺省或 idle 时节点灰显不可点。 */
  recovery?: RecoveryState;
}) {
  const concluded = rca.status === "concluded";
  const activeStep = rca.steps.find((step) => step.state === "active");
  // 展开覆盖：用户点过谁记谁（优先于默认）；默认 = active 步展开、闭环时步 5 展开。
  // ActivityRail 随 key=runId 重挂 → 换会话自动重置。同 run 第二次诊断任务不重挂——
  // 靠 revision 回退（新板从 1 重计，Workbench 守卫保证段内只前进）识别换板并清覆盖，
  // 否则旧板的展开选择会误套到新板。waiting 步永不展开（无内容），忽略 stale 覆盖。
  const [overrides, setOverrides] = useState<Record<number, boolean>>({});
  // 新线索过渡提示：复用同一 revision 回退信号（换板 = 用户补充线索开启新一轮诊断）。
  // ephemeral（刷新/重挂即无）；盲区：旧板只更新过 ≤1 次（revision 未超过新板起点）时
  // 检测不到回退，提示不出现——可接受，不为此加轮次计数。
  const [renewalNotice, setRenewalNotice] = useState(false);
  const lastRevisionRef = useRef<number>(rca.revision ?? 0);
  useEffect(() => {
    const revision = rca.revision ?? 0;
    if (revision < lastRevisionRef.current) {
      setOverrides({});
      setRenewalNotice(true);
    }
    lastRevisionRef.current = revision;
  }, [rca.revision]);
  useEffect(() => {
    if (!renewalNotice) return;
    const timer = window.setTimeout(() => setRenewalNotice(false), 6_000);
    return () => window.clearTimeout(timer);
  }, [renewalNotice]);
  const isExpanded = (step: RcaStep): boolean =>
    step.state !== "waiting"
    && (overrides[step.num] ?? (concluded ? step.num === 5 : step.state === "active"));
  const toggleStep = (num: number, expanded: boolean) =>
    setOverrides((prev) => ({ ...prev, [num]: !expanded }));
  // 第六节点复用 overrides[6]（步 num 只有 1-5 不冲突；revision 回退换板时随 overrides 一并重置）。
  // 默认收起（收起态有一行摘要）；idle 无内容不可展开，忽略 stale 覆盖。
  const recoveryExpanded = Boolean(recovery && recovery.phase !== "idle" && (overrides[6] ?? false));

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
      message: `采纳当前诊断结论，请生成并发起恢复动作「${pendingConfirmAction.text}」（高危动作走审批流程）。`,
    }
    : undefined;
  const showFooter = Boolean(onAction && (continueAction || adoptAction));

  const totalSteps = rca.steps.length || 5;
  const doneCount = rca.steps.filter((step) => step.state === "done").length;
  const progressNum = activeStep?.num ?? doneCount;

  return (
    <div
      className="oa-diag-card"
      data-testid="rca-card"
      data-rca-revision={rca.revision ?? ""}
      data-rca-status={rca.status ?? ""}
      style={{
        border: "1px solid #dbe3f0",
        borderRadius: radius.xxl,
        background: "#fff",
        boxShadow: shadow.card,
        // 不能 overflow:hidden——header 需要相对活动面板滚动容器 sticky。
        animation: "omPop .3s ease", // 仅首挂播放：revision 更新原地重渲染，不重挂不重播
      }}
    >
      {/* sticky header：标题行 + 实时进度行（进度是这块面板的主角，面板滚动时不离屏） */}
      <div className="oa-diag-header" style={{ borderRadius: `${radius.xxl} ${radius.xxl} 0 0` }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 9,
            padding: "12px 16px 9px",
            background: "linear-gradient(180deg,#fbfcfe,#fff)",
            borderRadius: `${radius.xxl} ${radius.xxl} 0 0`,
          }}
        >
          <div style={{ width: 28, height: 28, borderRadius: radius.md, background: color.brandTintBg, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 28px" }}>
            <Icon name="report-search" size={17} color={color.brand} />
          </div>
          {/* 超长标题单行省略 + title 补全（对齐 Workbench 会话标题惯例），不把相位 chip/time 顶出卡外 */}
          <div
            title={`诊断${rca.title ? ` · ${rca.title}` : ""}`}
            style={{ fontSize: 14, fontWeight: 700, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
          >
            诊断{rca.title ? ` · ${rca.title}` : ""}
          </div>
          {rca.phaseLabel ? (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, fontWeight: 600, color: color.brandStrong, background: color.brandTintBg, padding: "3px 9px", borderRadius: radius.pill, whiteSpace: "nowrap" }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: concluded ? color.good : color.brand, animation: live ? "omPulse 1.2s ease-in-out infinite" : undefined }} />
              {rca.phaseLabel}
            </span>
          ) : null}
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 11.5, color: color.textSubtle, whiteSpace: "nowrap", flex: "0 0 auto" }}>{rca.time}</span>
        </div>
        {/* 进度行：读屏用 role=status 通报当前处于五步法哪一步 */}
        <div
          role="status"
          data-testid="diagnosis-progress"
          aria-label={concluded
            ? "诊断进度：已完成"
            : activeStep
              ? `诊断进度：第${activeStep.num}步 · ${activeStep.label}`
              : "诊断进度"}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "0 16px 10px",
            fontSize: 12,
            fontWeight: 700,
            color: concluded ? color.goodText : color.brandStrong,
            borderBottom: `1px solid ${color.borderInner}`,
          }}
        >
          {concluded ? <Icon name="circle-check" size={14} color={color.good} /> : null}
          {concluded ? `诊断完成 ${totalSteps}/${totalSteps}` : `诊断进度 ${progressNum}/${totalSteps}`}
        </div>
      </div>

      {/* 新线索过渡提示：revision 回退（换板）后 6s 内展示，缓和「富板瞬间变稀疏板」的突兀感 */}
      {renewalNotice ? (
        <div
          role="status"
          data-testid="diagnosis-renewal-notice"
          className="oa-diag-renewal"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 7,
            padding: "8px 16px",
            fontSize: 12,
            fontWeight: 600,
            color: color.brandStrong,
            background: color.brandTintBg,
            borderBottom: `1px solid ${color.borderInner}`,
          }}
        >
          <Icon name="refresh" size={14} color={color.brand} />
          已基于新线索开启新一轮诊断
        </div>
      ) : null}

      {/* tiles 概览格已删（内网反馈：只留标题+时间线）——tiles 数据链保留，
          信息经 fallbackSummary 拼进第 1 步「诊断范围」的摘要文本，不丢 */}
      {/* 垂直时间线：五步 + 常驻第六节点「恢复执行」（连线自步 5 接入，last 恒 false） */}
      <ol className="oa-diag-timeline">
        {rca.steps.map((step) => (
          <StepNode
            key={step.num}
            rca={rca}
            step={step}
            last={false}
            live={live}
            expanded={isExpanded(step)}
            onToggle={() => toggleStep(step.num, isExpanded(step))}
          />
        ))}
        <RecoveryStepNode
          rca={rca}
          recovery={recovery}
          expanded={recoveryExpanded}
          onToggle={() => toggleStep(6, recoveryExpanded)}
        />
      </ol>

      {/* footer 操作条：独立于步卡（折叠结论步时按钮不消失）；run closed（onAction 缺省）
          或无可用动作时整块不渲染 */}
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

function StepNode({
  rca,
  step,
  last,
  live,
  expanded,
  onToggle,
}: {
  rca: RcaCardData;
  step: RcaStep;
  last: boolean;
  live: boolean;
  expanded: boolean;
  onToggle: () => void;
}) {
  const meta = STEP_META[step.num] ?? { title: step.label, en: "", icon: "point" };
  const isActive = step.state === "active";
  const isDone = step.state === "done";
  const waiting = step.state === "waiting";
  // waiting 步不显示摘要：兜底摘要取自跨步全局字段，会把未开始的步伪装成已有产出。
  const summaryLine = step.state === "waiting"
    ? undefined
    : step.summary ?? (fallbackSummary(rca, step.num, step.state) || undefined);
  const dotBg = isDone ? color.good : isActive ? color.brand : "#e6e8ec";

  const headInner = (
    <>
      <Icon name={meta.icon} size={15} color={waiting ? color.textFaint : isActive ? color.brand : color.textNav} />
      <span style={{ fontSize: 12.5, fontWeight: isActive ? 700 : 600, color: waiting ? color.textSubtle : isActive ? color.brand : color.textStrong }}>
        {meta.title}
      </span>
      {meta.en ? (
        <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: 0.6, color: color.textFaint }}>{meta.en}</span>
      ) : null}
      {isActive ? (
        <span style={{ fontSize: 10, fontWeight: 700, color: color.brandStrong, background: color.brandTintBg, padding: "1px 7px", borderRadius: radius.pill, whiteSpace: "nowrap" }}>
          进行中
        </span>
      ) : null}
      <span style={{ flex: 1 }} />
      {!waiting ? (
        <Icon name={expanded ? "chevron-up" : "chevron-down"} size={13} color={color.textSubtle} />
      ) : null}
    </>
  );

  return (
    <li className="oa-diag-step" data-step-state={step.state}>
      {/* 左 rail：圆点（done 绿勾 / active 蓝底步号 / waiting 灰）+ 竖连线（done 段绿） */}
      <span className="oa-diag-step-rail" aria-hidden>
        <span
          className="oa-diag-step-dot"
          style={{
            background: dotBg,
            color: isDone || isActive ? "#fff" : color.textSubtle,
            animation: live && isActive ? "omPulse 1.2s ease-in-out infinite" : undefined,
          }}
        >
          {isDone ? <Icon name="check" size={12} color="#fff" /> : step.num}
        </span>
        {!last ? <span className="oa-diag-step-line" style={{ background: isDone ? color.good : "#e6e8ec" }} /> : null}
      </span>

      {/* 右卡片：waiting 步纯 div 不可点，done/active 为可展开按钮 */}
      <div
        className="oa-diag-step-card"
        style={{
          border: `1px solid ${isActive ? color.brandTintBorder : color.border}`,
          borderRadius: radius.lg,
          background: isActive ? "#fdfeff" : "#fff",
        }}
      >
        {waiting ? (
          <div className="oa-diag-step-head is-waiting" data-testid={`diagnosis-step-${step.num}`}>
            {headInner}
          </div>
        ) : (
          <button
            type="button"
            className="oa-diag-step-head"
            aria-expanded={expanded}
            data-testid={`diagnosis-step-${step.num}`}
            onClick={onToggle}
          >
            {headInner}
          </button>
        )}
        {!expanded && summaryLine ? (
          <div style={{ padding: "0 12px 9px", fontSize: 11.5, color: waiting ? color.textSubtle : color.textMuted, lineHeight: 1.5 }}>
            {summaryLine}
          </div>
        ) : null}
        {expanded && !waiting ? <StepBody rca={rca} step={step} /> : null}
      </div>
    </li>
  );
}

/** 展开态每步详情：渲染分节自旧 RcaCard 对应分块迁移；空列表分节整块隐藏纪律沿用。 */
function StepBody({ rca, step }: { rca: RcaCardData; step: RcaStep }) {
  const isActive = step.state === "active";
  // currentQ/why 是跨步字段：谁 active 谁在展开卡里显示（顺带填补验证步内容）。
  const sharedQuestion = isActive && (rca.currentQ || rca.why) ? (
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
  ) : null;

  const summaryParagraph = step.summary ? (
    <div style={{ fontSize: 12, color: color.textBody, lineHeight: 1.6 }}>{step.summary}</div>
  ) : null;

  const sections: ReactNode[] = [sharedQuestion];

  switch (step.num) {
    case 1: {
      // 范围：summary 即可（tiles 概览格已删，其信息由 fallbackSummary 拼进本步文本）
      sections.push(summaryParagraph ?? (
        <div style={{ fontSize: 12, color: color.textBody, lineHeight: 1.6 }}>{fallbackSummary(rca, 1, step.state)}</div>
      ));
      break;
    }
    case 2: {
      // 证据：facts / unknowns 双框 + 证据源 badge
      sections.push(summaryParagraph);
      sections.push(rca.facts.length || rca.unknowns.length ? (
        <div className="oa-rca-facts" style={{ display: "grid", gap: 12 }}>
          {rca.facts.length ? (
            <div style={{ border: `1px solid ${color.goodBorder}`, borderRadius: radius.lg, padding: "11px 13px", background: color.goodBg }}>
              <div style={{ fontSize: 11.5, fontWeight: 700, color: color.goodText, marginBottom: 7, display: "flex", alignItems: "center", gap: 5 }}>
                <Icon name="checks" size={14} color={color.goodText} />已确认事实
              </div>
              {rca.facts.map((f, i) => (
                <div key={i} style={{ fontSize: 12, color: color.textBody, lineHeight: 1.5, padding: "2px 0", display: "flex", gap: 6 }}>
                  {/* 文本必须包 span：匿名 flex 子项无法定 min-width，长 token 会撑破卡框 */}
                  <span style={{ color: color.good }}>·</span><span style={{ minWidth: 0, flex: 1 }}>{f.text}</span>
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
                  <span style={{ color: color.warning }}>·</span><span style={{ minWidth: 0, flex: 1 }}>{u.text}</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null);
      sections.push(rca.sources.length ? (
        <div>
          <div style={{ fontSize: 11.5, fontWeight: 700, color: color.textNav, marginBottom: 7 }}>证据源状态</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {rca.sources.map((s, i) => (
              <StatusBadge key={i} label={s.name} value={s.status} tone={s.tone} running={s.status === "running"} />
            ))}
          </div>
        </div>
      ) : null);
      break;
    }
    case 3: {
      // 假设：排行 + 置信度进度条
      sections.push(summaryParagraph);
      sections.push(rca.hypotheses.length ? (
        <div>
          <div style={{ fontSize: 11.5, fontWeight: 700, color: color.textNav, marginBottom: 7 }}>假设排行</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            {rca.hypotheses.map((h, i) => {
              const tc = toneColor[h.tagTone];
              return (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, border: `1px solid ${color.border}`, borderRadius: radius.lg, padding: "9px 12px", background: "#fff" }}>
                  <span style={{ fontSize: 12.5, fontWeight: 600, color: color.textStrong, flex: 1, minWidth: 0 }}>{h.text}</span>
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
      ) : null);
      break;
    }
    case 4: {
      // 验证：summary +（active 时）currentQ/why；两者都无 → 灰字占位不留白
      sections.push(summaryParagraph);
      if (!step.summary && !sharedQuestion) {
        sections.push(
          <div style={{ fontSize: 12, color: color.textSubtle }}>验证进行中，暂无详情</div>,
        );
      }
      break;
    }
    case 5: {
      // 结论：虚线框 + 「写入知识库」chip + 下一步行动
      sections.push(summaryParagraph);
      sections.push(rca.conclusion ? (
        <div style={{ border: `1px dashed #cdd4de`, borderRadius: radius.lg, padding: "12px 13px", background: color.surfaceTint }}>
          <div style={{ fontSize: 11.5, fontWeight: 700, color: color.textNav, marginBottom: 5 }}>最终结论</div>
          <div style={{ fontSize: 12.5, color: color.textBody, lineHeight: 1.6 }}>{rca.conclusion}</div>
          <div style={{ marginTop: 9 }}>
            <span title="Knowledge / RAG V1 禁用" style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, fontWeight: 600, color: color.textFaint, background: color.neutralBg, border: `1px solid ${color.border}`, padding: "5px 10px", borderRadius: radius.sm, cursor: "not-allowed" }}>
              <Icon name="bookmark" size={13} color={color.textFaint} />写入知识库<span style={{ fontSize: 10 }}>即将上线</span>
            </span>
          </div>
        </div>
      ) : null);
      sections.push(rca.actions.length ? <ActionRows rca={rca} /> : null);
      break;
    }
    default:
      sections.push(summaryParagraph);
  }

  const rendered = sections.filter(Boolean);
  if (!rendered.length) return null;
  return (
    <div style={{ padding: "0 12px 12px", display: "flex", flexDirection: "column", gap: 12 }}>
      {rendered.map((section, index) => (
        <div key={index}>{section}</div>
      ))}
    </div>
  );
}

/** 「下一步行动」清单：步 5 展开态与第六恢复节点展开态共用（同一数据源 rca.actions）。 */
function ActionRows({ rca }: { rca: RcaCardData }) {
  if (!rca.actions.length) return null;
  return (
    <div>
      <div style={{ fontSize: 11.5, fontWeight: 700, color: color.textNav, marginBottom: 7 }}>下一步行动</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {rca.actions.map((a, i) => {
          const tc = toneColor[a.statusTone];
          return (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, border: `1px solid ${color.border}`, borderRadius: radius.lg, padding: "10px 12px", background: "#fff" }}>
              <span style={{ fontSize: 10.5, fontWeight: 700, color: color.brandStrong, background: color.brandTintBg, padding: "2px 8px", borderRadius: radius.sm, whiteSpace: "nowrap" }}>{a.tier}</span>
              <span style={{ flex: 1, minWidth: 0, fontSize: 12.5, color: color.textStrong }}>{a.text}</span>
              {a.confirm ? (
                <span style={{ fontSize: 10.5, fontWeight: 600, color: color.warningText, background: color.warningChipBg, padding: "2px 7px", borderRadius: radius.sm, whiteSpace: "nowrap" }}>需确认</span>
              ) : null}
              {/* impact 是模型自由文本（≤120 字），nowrap 下长串会撑破卡片——单行省略 + title 补全 */}
              {a.impact ? (
                <span
                  title={`影响：${a.impact}`}
                  style={{ fontSize: 11, color: color.textSubtle, whiteSpace: "nowrap", maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis" }}
                >影响：{a.impact}</span>
              ) : null}
              <span style={{ fontSize: 11, fontWeight: 600, color: tc.text }}>{a.status}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** 第六节点「恢复执行 RECOVER」：常驻时间线尾部，由审批/工具事件驱动（deriveRecoveryState）。
 *  DOM 契约与五步隔离：不设 data-step-state（e2e 断言 done 恒 5 步），改用
 *  data-testid="diagnosis-step-recover" + li[data-recovery-phase]；「诊断进度 N/5」口径不变。
 *  与 reopen 并存：被拒时板回 step5 active（后端行为）+ 本节点「已拒绝」——信号源正交，
 *  构成「结论待修订」叙事。 */
function RecoveryStepNode({
  rca,
  recovery,
  expanded,
  onToggle,
}: {
  rca: RcaCardData;
  recovery?: RecoveryState;
  expanded: boolean;
  onToggle: () => void;
}) {
  const meta = STEP_META[6];
  const phase = recovery?.phase ?? "idle";
  const idle = phase === "idle";
  const phaseMeta = idle ? undefined : RECOVERY_PHASE_META[phase];
  const executed = phase === "executed";
  const summaryLine = !idle && recovery
    ? `恢复动作 ${recovery.counts.total} 项 · ${phaseMeta!.label}`
    : undefined;

  const headInner = (
    <>
      <Icon name={meta.icon} size={15} color={idle ? color.textFaint : phaseMeta!.dot} />
      <span style={{ fontSize: 12.5, fontWeight: idle ? 600 : 700, color: idle ? color.textSubtle : color.textStrong }}>
        {meta.title}
      </span>
      <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: 0.6, color: color.textFaint }}>{meta.en}</span>
      {phaseMeta ? (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 10, fontWeight: 700, color: phaseMeta.text, background: phaseMeta.bg, padding: "1px 7px", borderRadius: radius.pill, whiteSpace: "nowrap" }}>
          <span style={{ width: 5, height: 5, borderRadius: "50%", background: phaseMeta.dot, animation: phaseMeta.live ? "omPulse 1.2s ease-in-out infinite" : undefined }} />
          {phaseMeta.label}
        </span>
      ) : null}
      <span style={{ flex: 1 }} />
      {!idle ? (
        <Icon name={expanded ? "chevron-up" : "chevron-down"} size={13} color={color.textSubtle} />
      ) : null}
    </>
  );

  return (
    <li className="oa-diag-step" data-recovery-phase={phase}>
      <span className="oa-diag-step-rail" aria-hidden>
        <span
          className="oa-diag-step-dot"
          style={{
            background: idle ? "#e6e8ec" : phaseMeta!.dot,
            color: idle ? color.textSubtle : "#fff",
            animation: phaseMeta?.live ? "omPulse 1.2s ease-in-out infinite" : undefined,
          }}
        >
          {executed ? <Icon name="check" size={12} color="#fff" /> : 6}
        </span>
        {/* 尾节点无下行连线 */}
      </span>

      <div
        className="oa-diag-step-card"
        style={{
          border: `1px solid ${phaseMeta?.live ? color.brandTintBorder : color.border}`,
          borderRadius: radius.lg,
          background: phaseMeta?.live ? "#fdfeff" : "#fff",
        }}
      >
        {idle ? (
          <div className="oa-diag-step-head is-waiting" data-testid="diagnosis-step-recover">
            {headInner}
          </div>
        ) : (
          <button
            type="button"
            className="oa-diag-step-head"
            aria-expanded={expanded}
            data-testid="diagnosis-step-recover"
            onClick={onToggle}
          >
            {headInner}
          </button>
        )}
        {!expanded && summaryLine ? (
          <div style={{ padding: "0 12px 9px", fontSize: 11.5, color: color.textMuted, lineHeight: 1.5 }}>
            {summaryLine}
          </div>
        ) : null}
        {expanded && recovery && !idle ? (
          <div style={{ padding: "0 12px 12px", display: "flex", flexDirection: "column", gap: 12 }}>
            <ActionRows rca={rca} />
            <div>
              <div style={{ fontSize: 11.5, fontWeight: 700, color: color.textNav, marginBottom: 7 }}>审批与执行明细</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {recovery.actions.map((action: RecoveryAction) => {
                  const st = RECOVERY_PHASE_META[action.phase];
                  const time = formatClock(action.updatedAt ?? action.requiredAt);
                  return (
                    <div key={action.approvalRequestId} style={{ display: "flex", alignItems: "center", gap: 10, border: `1px solid ${color.border}`, borderRadius: radius.lg, padding: "9px 12px", background: "#fff" }}>
                      <span style={{ flex: 1, minWidth: 0 }}>
                        <span style={{ display: "block", fontSize: 12, color: color.textStrong }}>{action.label ?? action.tool}</span>
                        <code style={{ display: "block", maxWidth: "100%", marginTop: 2, fontSize: 10.5, color: color.textMuted, fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace" }}>{action.tool}</code>
                      </span>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 10.5, fontWeight: 600, color: st.text, background: st.bg, padding: "2px 8px", borderRadius: radius.sm, whiteSpace: "nowrap" }}>
                        <span style={{ width: 5, height: 5, borderRadius: "50%", background: st.dot, animation: st.live ? "omPulse 1.2s ease-in-out infinite" : undefined }} />
                        {st.label}
                      </span>
                      {time ? (
                        <span style={{ fontSize: 11, color: color.textSubtle, whiteSpace: "nowrap", flex: "0 0 auto" }}>{time}</span>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </li>
  );
}

/** 空态骨架：五个 waiting 节点 + 第六灰节点（双语标题），供 ActivityRail 的诊断空态复用。
 *  模型漏调上报工具时就停在这里——不做任何服务端/前端伪造。 */
export function DiagnosisTimelineSkeleton() {
  return (
    <ol className="oa-diag-timeline oa-diag-skeleton" aria-hidden>
      {[1, 2, 3, 4, 5, 6].map((num) => {
        const meta = STEP_META[num];
        return (
          <li key={num} className="oa-diag-step" data-step-state={num === 6 ? undefined : "waiting"}>
            <span className="oa-diag-step-rail">
              <span className="oa-diag-step-dot" style={{ background: "#e6e8ec", color: color.textSubtle }}>{num}</span>
              {num < 6 ? <span className="oa-diag-step-line" style={{ background: "#e6e8ec" }} /> : null}
            </span>
            <div className="oa-diag-step-card" style={{ border: `1px solid ${color.border}`, borderRadius: radius.lg, background: "#fff" }}>
              <div className="oa-diag-step-head is-waiting">
                <Icon name={meta.icon} size={15} color={color.textFaint} />
                <span style={{ fontSize: 12.5, fontWeight: 600, color: color.textSubtle }}>{meta.title}</span>
                <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: 0.6, color: color.textFaint }}>{meta.en}</span>
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
