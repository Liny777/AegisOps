import assert from "node:assert/strict";
import test from "node:test";

import type { EnsureRuleResult } from "../../alerts/types";
import { bindEntry, loadEntry, markDone, pruneEntries, type StorageLike } from "./takeoverStore";

const KEY = "openops.alertTakeover.runs";
const DAY = 24 * 60 * 60 * 1000;

const memStorage = (): StorageLike & { dump(): string | null } => {
  const m = new Map<string, string>();
  return {
    getItem: (k) => m.get(k) ?? null,
    setItem: (k, v) => void m.set(k, v),
    removeItem: (k) => void m.delete(k),
    dump: () => m.get(KEY) ?? null,
  };
};

const CTX = { source: "alert", category: "MySQL", severity: "warning" } as const;

const RESULT: EnsureRuleResult = {
  outcome: "already_covered",
  rule: {
    rule_id: "r1", name: "Test", description: "", categories: ["MySQL"], enabled: true,
    source: "custom", severities: ["fatal", "critical"], strategies: [], prompt: "p",
    app_ids: [], keywords: [], updated_at: "2026-08-17T10:00:00+08:00",
  },
  renamed: false,
  requested_name: "MySQL普通告警接管规则",
  merge_detail: null,
  prompt_ignored: false,
};

test("bind→load 回环：ctx 与 instanceId 保真，无 done", () => {
  const s = memStorage();
  bindEntry("run_a", CTX, "agt_1", s, 1000);
  const e = loadEntry("run_a", s, 2000);
  assert.deepEqual(e?.ctx, { category: "MySQL", severity: "warning" });
  assert.equal(e?.instanceId, "agt_1");
  assert.equal(e?.done, undefined);
});

test("markDone→load 回环：result 全量保真", () => {
  const s = memStorage();
  bindEntry("run_a", CTX, "agt_1", s, 1000);
  markDone("run_a", "already_covered", RESULT, s, 2000);
  const e = loadEntry("run_a", s, 3000);
  assert.equal(e?.done, "already_covered");
  assert.deepEqual(e?.result, RESULT);
  assert.equal(e?.instanceId, "agt_1"); // markDone 保留 bind 时实例
});

test("bind 整体替换：清掉旧 done/result（pending 优先语义）", () => {
  const s = memStorage();
  bindEntry("run_a", CTX, "agt_1", s, 1000);
  markDone("run_a", "created", RESULT, s, 2000);
  bindEntry("run_a", { ...CTX, severity: "fatal" }, "agt_2", s, 3000);
  const e = loadEntry("run_a", s, 4000);
  assert.equal(e?.done, undefined);
  assert.equal(e?.result, undefined);
  assert.equal(e?.ctx.severity, "fatal");
  assert.equal(e?.instanceId, "agt_2");
});

test("markDone 无前置 bind 也落（done 是防重放事实源）", () => {
  const s = memStorage();
  markDone("run_x", "merged", RESULT, s, 1000);
  const e = loadEntry("run_x", s, 2000);
  assert.equal(e?.done, "merged");
  assert.equal(e?.ctx.category, "MySQL"); // 从 result.rule 补
});

test("TTL：7 天后 load 返 null 且不回写", () => {
  const s = memStorage();
  bindEntry("run_a", CTX, "", s, 0);
  const before = s.dump();
  assert.equal(loadEntry("run_a", s, 7 * DAY + 1), null);
  assert.equal(s.dump(), before); // 只读路径不落盘
  assert.notEqual(loadEntry("run_a", s, 7 * DAY - 1), null);
});

test("prune：超上限按 at 淘汰最旧；过期条目一并清", () => {
  const now = 100 * DAY;
  const entries: Record<string, { ctx: { category: string; severity: "warning" }; at: number }> = {};
  for (let i = 0; i < 55; i++) entries[`run_${i}`] = { ctx: { category: "c", severity: "warning" }, at: now - i * 1000 };
  entries["run_expired"] = { ctx: { category: "c", severity: "warning" }, at: now - 8 * DAY };
  const pruned = pruneEntries(entries as never, now);
  const keys = Object.keys(pruned);
  assert.equal(keys.length, 50);
  assert.ok(keys.includes("run_0") && keys.includes("run_49"));
  assert.ok(!keys.includes("run_54") && !keys.includes("run_expired"));
});

test("写路径触发 prune：bind 第 51 条时最旧的被挤出", () => {
  const s = memStorage();
  for (let i = 0; i < 50; i++) bindEntry(`run_${i}`, CTX, "", s, 1000 + i);
  bindEntry("run_new", CTX, "", s, 5000);
  assert.equal(loadEntry("run_0", s, 6000), null); // at=1000 最旧被淘汰
  assert.notEqual(loadEntry("run_new", s, 6000), null);
});

test("顶层版本不识别 → 整体弃，随后可正常重建", () => {
  const s = memStorage();
  s.setItem(KEY, JSON.stringify({ v: 99, entries: { run_a: { ctx: { category: "c", severity: "warning" }, at: 1 } } }));
  assert.equal(loadEntry("run_a", s, 2), null);
  bindEntry("run_b", CTX, "", s, 3);
  assert.notEqual(loadEntry("run_b", s, 4), null);
});

test("条目级损坏逐条弃：severity 非法 / done 有 result 无 / JSON 烂", () => {
  const s = memStorage();
  s.setItem(KEY, JSON.stringify({
    v: 1,
    entries: {
      bad_sev: { ctx: { category: "c", severity: "urgent" }, at: 1 },
      done_no_result: { ctx: { category: "c", severity: "warning" }, done: "created", at: 1 },
      good: { ctx: { category: "c", severity: "warning" }, at: 1 },
    },
  }));
  assert.equal(loadEntry("bad_sev", s, 2), null);
  assert.equal(loadEntry("done_no_result", s, 2), null);
  assert.notEqual(loadEntry("good", s, 2), null);

  s.setItem(KEY, "{烂");
  assert.equal(loadEntry("good", s, 2), null); // JSON 烂 → catch → null，不 throw
});

test("storage 抛异常：全部 API 不 throw", () => {
  const throwing: StorageLike = {
    getItem: () => { throw new Error("denied"); },
    setItem: () => { throw new Error("denied"); },
    removeItem: () => { throw new Error("denied"); },
  };
  assert.doesNotThrow(() => {
    assert.equal(loadEntry("run_a", throwing, 1), null);
    bindEntry("run_a", CTX, "", throwing, 1);
    markDone("run_a", "created", RESULT, throwing, 1);
  });
});
