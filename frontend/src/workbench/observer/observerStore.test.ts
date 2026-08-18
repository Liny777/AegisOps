import assert from "node:assert/strict";
import test from "node:test";

import { createObserverStreamStore } from "./observerStore";

/** 注入假 schedule 模拟 rAF：flush() 手动放行一帧。 */
const makeStore = () => {
  const frames: (() => void)[] = [];
  const store = createObserverStreamStore((cb) => frames.push(cb));
  const flush = () => {
    while (frames.length) frames.shift()!();
  };
  return { store, flush };
};

test("delta 通知 rAF 合帧：一帧内多条只 notify 一次", () => {
  const { store, flush } = makeStore();
  let notified = 0;
  store.subscribe(() => notified++);
  store.dispatch({ type: "task_started", taskId: "t" }); // 状态类：立即 notify
  assert.equal(notified, 1);
  store.dispatch({ type: "delta", messageId: "m", delta: "a" });
  store.dispatch({ type: "delta", messageId: "m", delta: "b" });
  store.dispatch({ type: "delta", messageId: "m2", delta: "c" });
  assert.equal(notified, 1); // 帧未放行不通知
  flush();
  assert.equal(notified, 2); // 三条 delta 合并为一次
  assert.equal(store.getState().bubbles.m.text, "ab"); // reduce 是同步的，不等帧
});

test("终态类立即 notify，不等 pending 帧", () => {
  const { store, flush } = makeStore();
  let notified = 0;
  store.subscribe(() => notified++);
  store.dispatch({ type: "task_started", taskId: "t" });
  store.dispatch({ type: "delta", messageId: "m", delta: "a" });
  store.dispatch({ type: "task_terminal" });
  assert.equal(notified, 2); // started + terminal 各一次（delta 帧未放行）
  assert.equal(store.getState().phase, "finalizing");
  flush(); // 延迟帧放行：多一次通知无害（幂等渲染）
  assert.ok(notified >= 2);
});

test("reducer 同引用（无效动作）不 notify 不排帧", () => {
  const { store, flush } = makeStore();
  let notified = 0;
  store.subscribe(() => notified++);
  store.dispatch({ type: "delta", messageId: "m", delta: "x" }); // idle 下丢弃
  store.dispatch({ type: "clear" });                             // 空 idle 同引用
  flush();
  assert.equal(notified, 0);
});

test("退订后不再收到通知", () => {
  const { store } = makeStore();
  let notified = 0;
  const off = store.subscribe(() => notified++);
  store.dispatch({ type: "task_started", taskId: "t" });
  off();
  store.dispatch({ type: "task_terminal" });
  assert.equal(notified, 1);
});
