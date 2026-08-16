// 告警接管编排 hook（Workbench 挂载）：捕获的 AlertEntryContext + 会话状态 → TakeoverVm。
// ctx 绝不进 WorkbenchTarget/workbenchSession——AppShell latest-wins 会重建 SSE/CopilotKit。
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import type { AlertEntryContext } from "../../lib/alertEntry";
import { SEVERITY_LABEL, deriveTemplateCategories } from "../../alerts/constants";
import type { AlertSeverity, EnsureRuleResult } from "../../alerts/types";
import { loadTakeoverApi } from "./takeoverApi";
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
  runId,
  runStatus,
  instanceId,
  rcaConcluded,
  running,
  inputBlocked,
  targetKey,
}: {
  alertCtx: AlertEntryContext | null;
  runId: string | null;
  runStatus: "active" | "closed";
  instanceId: string;
  rcaConcluded: boolean;
  running: boolean;
  inputBlocked: boolean;
  targetKey: string;
}): TakeoverVm {
  const [state, dispatch] = useReducer(takeoverReducer, initialTakeoverState);
  const [ctx, setCtx] = useState<AlertEntryContext | null>(alertCtx);
  const [templates, setTemplates] = useState<TemplateMeta | null>(null);
  // null=探询未回/失败：按已开通放行（appState 同取舍），真未开通由 ensureRule 403 兜底
  const [access, setAccess] = useState<boolean | null>(null);
  const [result, setResult] = useState<EnsureRuleResult | null>(null);
  const [boundRunId, setBoundRunId] = useState<string | null>(null);
  const boundTargetKeyRef = useRef(targetKey);

  // 常驻 Workbench 切会话（targetKey 变）→ ctx 永久失效：接管按钮不得残留到别的会话
  useEffect(() => {
    if (targetKey !== boundTargetKeyRef.current) setCtx(null);
  }, [targetKey]);

  // boundRunId = ctx 消费后首个非空 runId（mock 档恒 null，computeArmed 按双 null 一致放行）
  useEffect(() => {
    if (ctx && runId) setBoundRunId((cur) => cur ?? runId);
  }, [ctx, runId]);

  // ctx 非空才拉模板+开通探询（并动态加载 alerts/api chunk）；普通会话零副作用
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
    runId,
    boundRunId,
    targetKey,
    boundTargetKey: boundTargetKeyRef.current,
    templateCategories: templates?.categories ?? [],
    templateSeverities: templates?.severities ?? [],
    instanceId,
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
      .then((api) => api.ensureRule(instanceId, {
        // 类别用模板规范写法（armed 已保证命中）；名称/提示词清空时回落默认，不发空串
        name: edited.name.trim() || defaultRuleName(ctx, templates.categories),
        categories: [resolveTemplateCategory(ctx.category, templates.categories)],
        severities: [ctx.severity],
        prompt: edited.prompt.trim() || templates.defaultPrompt,
      }))
      .then((res) => {
        setResult(res);
        dispatch({ type: "RESOLVE", outcome: res.outcome });
      })
      .catch((err) => {
        if (isNotGrantedError(err)) dispatch({ type: "REJECT_NOT_GRANTED" });
        else dispatch({ type: "FAIL", message: (err as Error).message || "创建失败，请重试" });
      });
  }, [ctx, templates, instanceId]);

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
