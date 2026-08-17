// 告警接管入口的按 run 持久化（2026-08-17 拍板：关页重开可恢复）。
//
// - 单键 map：prune / 版本迁移一处收口。每次调用即时读盘不做内存缓存——把多标签竞态
//   窗口压到单次写的毫秒级。**刻意不监听 storage 事件**：A 页确认后 B 页不实时撤按钮，
//   最坏双确认由 rules:ensure 幂等（already_covered）兜底——拍板取舍，别当 bug 排查。
// - 隐私模式 localStorage 抛异常 → 全部吞掉，功能退化为纯内存（与 alertEntry 同哲学）。
// - done 仅收三档真终态；not_granted / error 不落盘（用户之后被开通/重试应能再走）。
// - 条目含规则名与提示词（EnsureRuleResult 全量），同机换账号可见——运维文案非凭据，
//   7 天 TTL 收敛，不按用户 namespace（me 异步到达会引入初始化顺序问题，不值得）。
import type { AlertEntryContext } from "../../lib/alertEntry";
import { SEVERITY_ORDER } from "../../alerts/constants";
import type { EnsureRuleOutcome, EnsureRuleResult } from "../../alerts/types";

const KEY = "openops.alertTakeover.runs";
const VERSION = 1;
const TTL_MS = 7 * 24 * 60 * 60 * 1000;
const MAX_ENTRIES = 50;
const OUTCOMES: readonly EnsureRuleOutcome[] = ["created", "merged", "already_covered"];

export interface TakeoverStoreEntry {
  ctx: { category: string; severity: AlertEntryContext["severity"] };
  /** bind 时的归属实例：mock 恢复用它防「切过侧栏 Agent 后建错实例」；real 只作 sanity check。 */
  instanceId?: string;
  done?: EnsureRuleOutcome;
  result?: EnsureRuleResult;
  /** 最后触碰时刻（bind / markDone 均刷新）——prune 淘汰排序键。 */
  at: number;
}

export type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;
type EntryMap = Record<string, TakeoverStoreEntry>;

const defaultStorage = (): StorageLike | null => {
  try {
    return globalThis.localStorage;
  } catch {
    return null;
  }
};

const readMap = (s: StorageLike): EntryMap => {
  const raw = s.getItem(KEY);
  if (!raw) return {};
  const parsed = JSON.parse(raw) as { v?: number; entries?: EntryMap } | null;
  // 未知版本/形状 → 整体弃（顶层 v 一处判定，别逐键打补丁）
  if (parsed?.v !== VERSION || !parsed.entries || typeof parsed.entries !== "object") return {};
  return parsed.entries;
};

const writeMap = (s: StorageLike, entries: EntryMap): void => {
  s.setItem(KEY, JSON.stringify({ v: VERSION, entries }));
};

/** 条目级校验：severity/done 越界、done 有而 result 无、形状烂 → 按损坏弃（宁丢勿错）。 */
const validEntry = (e: TakeoverStoreEntry | undefined | null): e is TakeoverStoreEntry => {
  if (!e || typeof e !== "object" || typeof e.at !== "number") return false;
  if (!e.ctx || typeof e.ctx.category !== "string") return false;
  if (!(SEVERITY_ORDER as readonly string[]).includes(e.ctx.severity)) return false;
  if (e.done !== undefined && (!(OUTCOMES as readonly string[]).includes(e.done) || !e.result)) return false;
  return true;
};

/** 纯核（单测面）：损坏条目 + TTL 过期清除，再按 at 淘汰至上限。 */
export function pruneEntries(entries: EntryMap, now: number): EntryMap {
  const alive = Object.entries(entries)
    .filter(([, e]) => validEntry(e) && now - e.at < TTL_MS)
    .sort((a, b) => b[1].at - a[1].at)
    .slice(0, MAX_ENTRIES);
  return Object.fromEntries(alive);
}

export function loadEntry(runKey: string, s: StorageLike | null = defaultStorage(),
                          now: number = Date.now()): TakeoverStoreEntry | null {
  try {
    if (!s) return null;
    const e = readMap(s)[runKey];
    // 过期只返 null 不回写：避免每次开会话都写盘，物理清理留给下次写路径的 prune
    return validEntry(e) && now - e.at < TTL_MS ? e : null;
  } catch {
    return null;
  }
}

/** 整体替换该 runKey 条目（含清掉旧 done/result）——pending 优先语义：深链新意图压过历史终态。 */
export function bindEntry(runKey: string, ctx: AlertEntryContext, instanceId: string,
                          s: StorageLike | null = defaultStorage(), now: number = Date.now()): void {
  try {
    if (!s) return;
    const entries = readMap(s);
    entries[runKey] = {
      ctx: { category: ctx.category, severity: ctx.severity },
      ...(instanceId ? { instanceId } : {}),
      at: now,
    };
    // 含新条目一起 prune：落盘后恒 ≤ 上限（新条目 at 最新必存活）
    writeMap(s, pruneEntries(entries, now));
  } catch {
    // 落盘失败退化为纯内存，不阻塞主链路
  }
}

/** 条目缺失也允许落（防御 bind 落盘曾失败）：done 是防重放 L2 的事实源，宁多写勿丢。 */
export function markDone(runKey: string, outcome: EnsureRuleOutcome, result: EnsureRuleResult,
                         s: StorageLike | null = defaultStorage(), now: number = Date.now()): void {
  try {
    if (!s) return;
    const entries = readMap(s);
    const prev = entries[runKey];
    entries[runKey] = {
      // 无前置 bind 时从 result 补 ctx：done 态的 ctx 只剩占位意义（armed/表单都到不了）
      ctx: prev?.ctx ?? {
        category: result.rule.categories[0] ?? "",
        severity: result.rule.severities[0] ?? "warning",
      },
      ...(prev?.instanceId ? { instanceId: prev.instanceId } : {}),
      done: outcome,
      result,
      at: now,
    };
    writeMap(s, pruneEntries(entries, now));
  } catch {
    // 同上
  }
}
