// 告警接管编排 hook（Workbench 挂载）：pending ctx / takeoverStore 恢复 → TakeoverVm。
// ctx 绝不进 WorkbenchTarget/workbenchSession——AppShell latest-wins 会重建 SSE/CopilotKit。
// 2026-08-17 持久化改造：换会话不再「永久失效」，而是按 runKey 整体重载（RESET→绑定/恢复）。
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import type { AlertEntryContext } from "../../lib/alertEntry";
import { SEVERITY_LABEL, deriveTemplateCategories } from "../../alerts/constants";
import type { AlertSeverity, EnsureRuleResult } from "../../alerts/types";
import { loadTakeoverApi } from "./takeoverApi";
import { bindEntry, loadEntry, markDone } from "./takeoverStore";
import {
  computeArmed,
  initialTakeoverState,
  resolveTemplateCategory,
  takeoverReducer,
  type TakeoverPhase,
} from "./takeoverMachine";

export interface TakeoverVm {
  phase: TakeoverPhase;
  ctx: AlertEntryContext | null;
  /** 卡片预填；模板 payload 就绪且 ctx 存活才非空（armed 起必非空）。 */
  defaults: { name: string; prompt: string } | null;
  result: EnsureRuleResult | null;
  errorMessage: string | null;
  open: () => void;
  cancel: () => void;
  confirm: (edited: { name: string; prompt: string }) => void;
}

interface TemplateMeta {
  categories: string[];
  severities: AlertSeverity[];
  defaultPrompt: string;
}

const defaultRuleName = (ctx: AlertEntryContext, categories: readonly string[]) =>
  `${resolveTemplateCategory(ctx.category, categories)}${SEVERITY_LABEL[ctx.severity]}告警接管规则`;

/** 未开通识别：mock/real 同以 ApiError.code=FORBIDDEN 下发（mockEnsureRule 注释即此契约）；
 *  旧后端仅文案时按「未开通」兜底匹配。 */
const isNotGrantedError = (err: unknown): boolean =>
  (err as { code?: string }).code === "FORBIDDEN" ||
  ((err as Error).message ?? "").includes("未开通");

export function useAlertTakeover({
  alertCtx,
  runKey,
  runStatus,
  instanceId,
  instanceResolved,
  rcaConcluded,
  running,
  inputBlocked,
}: {
  alertCtx: AlertEntryContext | null;
  /** real=canonical runId（/state 原子换入）、mock=targetKey——由 Workbench 计算，hook 不感知 API_MODE。 */
  runKey: string | null;
  runStatus: "active" | "closed";
  instanceId: string;
  /** true=instanceId 来自 /state 权威解析（real 档）：恢复条目与之冲突即弃；
   *  false=mock 回落值：恢复时优先用条目里 bind 时的实例（防切过侧栏 Agent 后建错实例）。 */
  instanceResolved: boolean;
  rcaConcluded: boolean;
  running: boolean;
  inputBlocked: boolean;
}): TakeoverVm {
  const [state, dispatch] = useReducer(takeoverReducer, initialTakeoverState);
  // pending = 本次进站深链捕获的新意图，只允许在「首个非空 runKey」处消费一次（换会话不得重绑）
  const pendingRef = useRef<AlertEntryContext | null>(alertCtx);
  const [ctx, setCtx] = useState<AlertEntryContext | null>(null);
  const [templates, setTemplates] = useState<TemplateMeta | null>(null);
  // null=探询未回/失败：按已开通放行（appState 同取舍），真未开通由 ensureRule 403 兜底
  const [access, setAccess] = useState<boolean | null>(null);
  const [result, setResult] = useState<EnsureRuleResult | null>(null);
  const [boundRunKey, setBoundRunKey] = useState<string | null>(null);
  // mock 恢复条目的实例修正（real 恒空，/state 权威值不受影响）
  const entryInstanceIdRef = useRef<string>("");

  // re-key：换 runKey 整体重载。RESET 的唯一合法派发点（takeoverMachine 注释的调用纪律）。
  // instanceId/instanceResolved 刻意不进 deps——绑定/恢复只在换 runKey 时发生。
  useEffect(() => {
    if (runKey === boundRunKey) return;
    if (runKey && pendingRef.current) {
      // 首个非空 runKey：pending 优先（深链新意图压过该 run 的历史终态），消费即清
      bindEntry(runKey, pendingRef.current, instanceId);
      setCtx(pendingRef.current);
      pendingRef.current = null;
      entryInstanceIdRef.current = "";
      setResult(null);
      dispatch({ type: "RESET" });
    } else if (runKey) {
      const entry = loadEntry(runKey);
      // real 档 sanity check：条目实例与 /state 权威值冲突 → 弃（防串实例）
      const conflicted = !!(entry?.instanceId && instanceId && entry.instanceId !== instanceId);
      const usable = entry && !(instanceResolved && conflicted) ? entry : null;
      entryInstanceIdRef.current = !instanceResolved ? (usable?.instanceId ?? "") : "";
      setCtx(usable
        ? { source: "alert", category: usable.ctx.category, severity: usable.ctx.severity }
        : null);
      setResult(usable?.result ?? null);
      dispatch({ type: "RESET" });
      if (usable?.done) dispatch({ type: "HYDRATE", outcome: usable.done });
    } else {
      // runKey 回 null（理论不发生，防御）
      setCtx(null);
      setResult(null);
      entryInstanceIdRef.current = "";
      dispatch({ type: "RESET" });
    }
    setBoundRunKey(runKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runKey]);

  // ctx 非空才拉模板+开通探询（并动态加载 alerts/api chunk）；普通会话零副作用。
  // 恢复出的 ctx 同样走这里——按钮复现依赖模板回包（弱网晚现数百 ms，非回归）。
  useEffect(() => {
    if (!ctx) return;
    let alive = true;
    void loadTakeoverApi()
      .then((api) => Promise.all([
        api.getRuleTemplates().then(
          (payload): TemplateMeta | null => ({
            categories: deriveTemplateCategories(payload),
            severities: payload.severities,
            defaultPrompt: payload.default_prompt,
          }),
          (err) => {
            // 模板失败→集合空→永不 armed。禁 FALLBACK_CATEGORIES 兜底 eligibility：
            // 兜底档与后端模板词表可能不符，按错误类别建规比不出按钮更糟。
            console.warn("[alertentry] 规则模板读取失败，接管按钮不出现：", err);
            return null;
          },
        ),
        api.getAccess().then((r) => r.granted, () => true),
      ]))
      .then(([tpl, granted]) => {
        if (!alive) return;
        setTemplates(tpl);
        setAccess(granted);
      })
      .catch((err) => {
        if (alive) console.warn("[alertentry] 接管交互模块加载失败：", err);
      });
    return () => {
      alive = false;
    };
  }, [ctx]);

  const armed = computeArmed({
    ctx,
    runKey,
    boundRunKey,
    templateCategories: templates?.categories ?? [],
    templateSeverities: templates?.severities ?? [],
    instanceId: entryInstanceIdRef.current || instanceId,
    rcaConcluded,
    running,
    runStatus,
    inputBlocked,
  });
  useEffect(() => {
    dispatch({ type: armed ? "ARM" : "DISARM" });
  }, [armed]);

  const defaults = useMemo(
    () => (ctx && templates
      ? { name: defaultRuleName(ctx, templates.categories), prompt: templates.defaultPrompt }
      : null),
    [ctx, templates],
  );

  const open = useCallback(() => {
    dispatch({ type: "OPEN", accessDenied: access === false });
  }, [access]);

  const cancel = useCallback(() => dispatch({ type: "CANCEL" }), []);

  const confirm = useCallback((edited: { name: string; prompt: string }) => {
    if (!ctx || !templates) return;
    dispatch({ type: "SUBMIT" });
    void loadTakeoverApi()
      .then((api) => api.ensureRule(entryInstanceIdRef.current || instanceId, {
        // 类别用模板规范写法（armed 已保证命中）；名称/提示词清空时回落默认，不发空串
        name: edited.name.trim() || defaultRuleName(ctx, templates.categories),
        categories: [resolveTemplateCategory(ctx.category, templates.categories)],
        severities: [ctx.severity],
        prompt: edited.prompt.trim() || templates.defaultPrompt,
      }))
      .then((res) => {
        setResult(res);
        dispatch({ type: "RESOLVE", outcome: res.outcome });
        // 防重放 L2 落盘（not_granted/error 刻意不落——之后被开通/重试应能再走）
        if (runKey) markDone(runKey, res.outcome, res);
      })
      .catch((err) => {
        if (isNotGrantedError(err)) dispatch({ type: "REJECT_NOT_GRANTED" });
        else dispatch({ type: "FAIL", message: (err as Error).message || "创建失败，请重试" });
      });
  }, [ctx, templates, instanceId, runKey]);

  return useMemo<TakeoverVm>(() => ({
    phase: state.phase,
    ctx,
    defaults,
    result,
    errorMessage: state.errorMessage,
    open,
    cancel,
    confirm,
  }), [state, ctx, defaults, result, open, cancel, confirm]);
}
