// 告警接管交互状态机（纯函数，零 React）：useAlertTakeover 以 useReducer 驱动，
// AlertTakeoverSlot 按 phase 渲染。done_* 是本 runKey 终态（持久化于 takeoverStore，
// 重开会话经 HYDRATE 恢复，按钮不复现）；error 保留卡片可重试。
import type { AlertEntryContext } from "../../lib/alertEntry";
import type { AlertSeverity, EnsureRuleOutcome } from "../../alerts/types";

export type TakeoverPhase =
  | "idle"
  | "armed"
  | "card_open"
  | "submitting"
  | "done_created"
  | "done_merged"
  | "done_already"
  | "done_not_granted"
  | "error";

export interface TakeoverState {
  phase: TakeoverPhase;
  errorMessage: string | null;
}

export const initialTakeoverState: TakeoverState = { phase: "idle", errorMessage: null };

export type TakeoverAction =
  | { type: "ARM" }
  | { type: "DISARM" }
  /** accessDenied=已知未开通（探询已回 false）：不出表单直达 done_not_granted。 */
  | { type: "OPEN"; accessDenied: boolean }
  | { type: "CANCEL" }
  | { type: "SUBMIT" }
  | { type: "RESOLVE"; outcome: EnsureRuleOutcome }
  | { type: "REJECT_NOT_GRANTED" }
  | { type: "FAIL"; message: string }
  | { type: "RETRY" }
  /** 换 runKey 整体重载。**唯一合法派发点是 useAlertTakeover 的 re-key effect**——
   *  这是 done_* 终态唯一的出口，在别处派发会把防重放砸穿。 */
  | { type: "RESET" }
  /** 从 takeoverStore 恢复终态（重开会话）。仅 idle 生效：RESET→HYDRATE 重复派发天然幂等。 */
  | { type: "HYDRATE"; outcome: EnsureRuleOutcome };

const OUTCOME_PHASE: Record<EnsureRuleOutcome, TakeoverPhase> = {
  created: "done_created",
  merged: "done_merged",
  already_covered: "done_already",
};

/** 非法转移一律原样返回同一引用（严格等值可测）：ARM 打不动终态、DISARM 关不掉已开卡片。 */
export function takeoverReducer(state: TakeoverState, action: TakeoverAction): TakeoverState {
  switch (action.type) {
    case "ARM":
      return state.phase === "idle" ? { phase: "armed", errorMessage: null } : state;
    case "DISARM":
      return state.phase === "armed" ? { phase: "idle", errorMessage: null } : state;
    case "OPEN":
      if (state.phase !== "armed") return state;
      return { phase: action.accessDenied ? "done_not_granted" : "card_open", errorMessage: null };
    case "CANCEL":
      // 取消回 armed；草稿由卡片组件自持，卡片卸载即弃、重开重置
      return state.phase === "card_open" || state.phase === "error"
        ? { phase: "armed", errorMessage: null }
        : state;
    case "SUBMIT":
      return state.phase === "card_open" || state.phase === "error"
        ? { phase: "submitting", errorMessage: null }
        : state;
    case "RESOLVE":
      return state.phase === "submitting"
        ? { phase: OUTCOME_PHASE[action.outcome], errorMessage: null }
        : state;
    case "REJECT_NOT_GRANTED":
      return state.phase === "submitting" ? { phase: "done_not_granted", errorMessage: null } : state;
    case "FAIL":
      return state.phase === "submitting" ? { phase: "error", errorMessage: action.message } : state;
    case "RETRY":
      return state.phase === "error" ? { phase: "submitting", errorMessage: null } : state;
    case "RESET":
      return state.phase === "idle" ? state : initialTakeoverState;
    case "HYDRATE":
      return state.phase === "idle"
        ? { phase: OUTCOME_PHASE[action.outcome], errorMessage: null }
        : state;
    default:
      return state;
  }
}

export interface ComputeArmedInput {
  /** pending 捕获或 takeoverStore 恢复出的上下文；换 runKey 由 re-key effect 重载。 */
  ctx: AlertEntryContext | null;
  /** 当前 runKey 必须仍是 ctx 绑定的那个。real=canonical runId、mock=targetKey（Workbench
   *  计算）；双 null 分支仅剩防御意义——绑定/恢复前 ctx 必为 null，armed 反正 false。 */
  runKey: string | null;
  boundRunKey: string | null;
  /** 模板派生类别集合（getRuleTemplates 失败=空集 → 永不 armed，禁 FALLBACK_CATEGORIES 兜底）。 */
  templateCategories: readonly string[];
  templateSeverities: readonly AlertSeverity[];
  /** 规则挂实例上：`/agent-runs/:id` 深链要等 /state 解出归属实例，未解析前建规会落错实例。 */
  instanceId: string;
  rcaConcluded: boolean;
  running: boolean;
  runStatus: "active" | "closed";
  inputBlocked: boolean;
}

const normCategory = (s: string) => s.trim().toLowerCase();

/** armed 判定纯函数：全维度合取，任一不满足按钮即不出现/撤下。 */
export function computeArmed(input: ComputeArmedInput): boolean {
  const { ctx } = input;
  if (!ctx) return false;
  if (!input.instanceId) return false;
  if (input.runKey !== input.boundRunKey) return false;
  const cat = normCategory(ctx.category);
  if (!input.templateCategories.some((c) => normCategory(c) === cat)) return false;
  if (!input.templateSeverities.includes(ctx.severity)) return false;
  return input.rcaConcluded && !input.running && input.runStatus === "active" && !input.inputBlocked;
}

/** ctx.category → 模板集合内的规范写法（trim+忽略大小写命中）；未命中回退原值（此时必不 armed）。 */
export function resolveTemplateCategory(category: string, templateCategories: readonly string[]): string {
  const target = normCategory(category);
  return templateCategories.find((c) => normCategory(c) === target) ?? category;
}
