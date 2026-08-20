import assert from "node:assert/strict";
import test from "node:test";

import { RELOAD_KEY, isChunkLoadError, shouldAutoReload, type StorageLike } from "./chunkError";

test("isChunkLoadError：四种引擎/Vite 消息形态均命中（大小写不敏感）", () => {
  for (const msg of [
    "Failed to fetch dynamically imported module: https://x/assets/SettingsHome-abc.js",
    "Importing a module script failed.",
    "error loading dynamically imported module",
    "Unable to preload CSS for /assets/x.css",
  ]) {
    assert.equal(isChunkLoadError(new Error(msg)), true, msg);
  }
});

test("isChunkLoadError：非 chunk 错误与空值返回 false", () => {
  assert.equal(isChunkLoadError(new Error("Cannot read properties of undefined")), false);
  assert.equal(isChunkLoadError(new TypeError("x.at is not a function")), false);
  assert.equal(isChunkLoadError(null), false);
  assert.equal(isChunkLoadError(undefined), false);
});

const fakeStorage = (initial: Record<string, string> = {}): StorageLike & { data: Record<string, string> } => {
  const data = { ...initial };
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => {
      data[k] = v;
    },
  };
};

test("shouldAutoReload：首次放行并落时间戳，60s 内拒绝，超窗再放行", () => {
  const storage = fakeStorage();
  const t0 = 1_700_000_000_000;
  assert.equal(shouldAutoReload(storage, t0), true);
  assert.equal(storage.data[RELOAD_KEY], String(t0));
  assert.equal(shouldAutoReload(storage, t0 + 59_000), false);
  assert.equal(storage.data[RELOAD_KEY], String(t0)); // 拒绝时不覆写时间戳
  assert.equal(shouldAutoReload(storage, t0 + 61_000), true);
  assert.equal(storage.data[RELOAD_KEY], String(t0 + 61_000));
});

test("shouldAutoReload：storage 抛异常（隐私模式）→ false 不 reload", () => {
  const throwing: StorageLike = {
    getItem: () => {
      throw new Error("denied");
    },
    setItem: () => {
      throw new Error("denied");
    },
  };
  assert.equal(shouldAutoReload(throwing, Date.now()), false);
});
