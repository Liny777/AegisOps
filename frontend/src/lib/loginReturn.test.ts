import assert from "node:assert/strict";
import test from "node:test";

import { sanitizeStoredReturnPath, toRouterPath } from "./loginReturn";

// 纯函数测试不碰 window/sessionStorage（capture/consume 归浏览器链路手验）——照 alertEntry.test.ts 约定。

const NOW = 1_700_000_000_000;
const stored = (p: unknown, ts: unknown): string => JSON.stringify({ p, ts });

test("toRouterPath：生产文根 /openops 剥前缀，search 保留", () => {
  assert.equal(toRouterPath("/openops/agent-runs/run-1", "", "/openops"), "/agent-runs/run-1");
  assert.equal(
    toRouterPath("/openops/agent-teams/i-1/chat", "?run_id=run-2", "/openops"),
    "/agent-teams/i-1/chat?run_id=run-2",
  );
  // base 恰为整个路径 → 归一为根 → 无需回位
  assert.equal(toRouterPath("/openops", "", "/openops"), null);
  assert.equal(toRouterPath("/openops/", "", "/openops"), null);
});

test("toRouterPath：dev（base 空串）原样通过，根路径不回位", () => {
  assert.equal(toRouterPath("/agent-runs/run-1", "", ""), "/agent-runs/run-1");
  assert.equal(toRouterPath("/", "", ""), null);
  assert.equal(toRouterPath("/", "?q=hello", ""), null); // ?q= 已由 autosend 单独往返，不靠本机制
});

test("toRouterPath：前缀相似但非文根的路径不误剥", () => {
  assert.equal(toRouterPath("/openops2/agent-runs/r", "", "/openops"), "/openops2/agent-runs/r");
});

test("toRouterPath：拦截页 /not-whitelisted 不当深链存（防开通后重登被送回死端页）", () => {
  assert.equal(toRouterPath("/not-whitelisted", "", ""), null);
  assert.equal(toRouterPath("/not-whitelisted/", "", ""), null);
  assert.equal(toRouterPath("/openops/not-whitelisted", "", "/openops"), null);
  // 前缀相似的正常路径不受牵连
  assert.equal(toRouterPath("/not-whitelisted-x", "", ""), "/not-whitelisted-x");
});

test("toRouterPath：非法形态与超长 → null", () => {
  assert.equal(toRouterPath(undefined, "", ""), null); // sse.test.ts 的残缺 window stub 形态
  assert.equal(toRouterPath("relative/path", "", ""), null);
  assert.equal(toRouterPath("/a", "?" + "x".repeat(3000), ""), null);
});

test("sanitizeStoredReturnPath：TTL 内合法路径通过，一分钟级时钟容差", () => {
  assert.equal(sanitizeStoredReturnPath(stored("/agent-runs/run-1", NOW - 60_000), NOW), "/agent-runs/run-1");
  assert.equal(sanitizeStoredReturnPath(stored("/agent-runs/run-1", NOW + 30_000), NOW), "/agent-runs/run-1");
});

test("sanitizeStoredReturnPath：过期 / 时钟回拨超窗 → null", () => {
  assert.equal(sanitizeStoredReturnPath(stored("/agent-runs/run-1", NOW - 16 * 60 * 1000), NOW), null);
  assert.equal(sanitizeStoredReturnPath(stored("/agent-runs/run-1", NOW + 2 * 60_000), NOW), null);
});

test("sanitizeStoredReturnPath：open redirect 形态全拒", () => {
  for (const p of ["https://evil.com/x", "//evil.com", "/\\evil.com", "javascript:alert(1)", ""]) {
    assert.equal(sanitizeStoredReturnPath(stored(p, NOW), NOW), null, p);
  }
});

test("sanitizeStoredReturnPath：坏 JSON / 字段缺失或类型错 → null", () => {
  assert.equal(sanitizeStoredReturnPath(null, NOW), null);
  assert.equal(sanitizeStoredReturnPath("not-json", NOW), null);
  assert.equal(sanitizeStoredReturnPath(JSON.stringify({ p: "/a" }), NOW), null);
  assert.equal(sanitizeStoredReturnPath(stored(123, NOW), NOW), null);
  assert.equal(sanitizeStoredReturnPath(stored("/a", "not-a-number"), NOW), null);
});
