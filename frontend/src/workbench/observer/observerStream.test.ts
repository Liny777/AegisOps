import assert from "node:assert/strict";
import test from "node:test";

import {
  OBSERVER_BUBBLE_CHAR_LIMIT,
  OBSERVER_BUBBLE_MAX_COUNT,
  initialObserverStream,
  isObserverDelta,
  observerStreamReducer,
  type ObserverDeltaSignals,
  type ObserverStreamState,
} from "./observerStream";

const live = (): ObserverStreamState =>
  observerStreamReducer(initialObserverStream(), { type: "task_started", taskId: "t1" });

/* ------------------------------ reducer ------------------------------ */

test("task_started：清空重开进入 live（旧泡/缺段标记全弃）", () => {
  let s = live();
  s = observerStreamReducer(s, { type: "delta", messageId: "m1", delta: "旧" });
  s = observerStreamReducer(s, { type: "resync" });
  const next = observerStreamReducer(s, { type: "task_started", taskId: "t2" });
  assert.deepEqual(next, { phase: "live", taskId: "t2", order: [], bubbles: {}, resyncGap: false });
});

test("task_started 同任务幂等重入：泡保留同引用（快照恢复/SSE 双入口 + refresh 重放）", () => {
  let s = live(); // taskId=t1
  s = observerStreamReducer(s, { type: "delta", messageId: "m", delta: "已累积" });
  const again = observerStreamReducer(s, { type: "task_started", taskId: "t1" });
  assert.equal(again, s); // 同任务：不清泡
  const newTask = observerStreamReducer(s, { type: "task_started", taskId: "t2" });
  assert.equal(newTask.order.length, 0); // 换任务：清空重开
  // taskId 双 null 不视为同任务（无身份不假设连续性）
  const anon = observerStreamReducer(initialObserverStream(), { type: "task_started", taskId: null });
  const anonAgain = observerStreamReducer(
    observerStreamReducer(anon, { type: "delta", messageId: "m", delta: "x" }),
    { type: "task_started", taskId: null },
  );
  assert.equal(anonAgain.order.length, 0);
});

test("delta：同泡追加、多泡按到达序", () => {
  let s = live();
  s = observerStreamReducer(s, { type: "delta", messageId: "m1", delta: "你" });
  s = observerStreamReducer(s, { type: "delta", messageId: "m1", delta: "好" });
  s = observerStreamReducer(s, { type: "delta", messageId: "m2", delta: "！" });
  assert.deepEqual(s.order, ["m1", "m2"]);
  assert.equal(s.bubbles.m1.text, "你好");
  assert.equal(s.bubbles.m2.text, "！");
});

test("delta：仅 live 生效；idle/finalizing/frozen 一律同引用丢弃", () => {
  const idle = initialObserverStream();
  assert.equal(observerStreamReducer(idle, { type: "delta", messageId: "m", delta: "x" }), idle);
  const fin = observerStreamReducer(live(), { type: "task_terminal" });
  assert.equal(observerStreamReducer(fin, { type: "delta", messageId: "m", delta: "x" }), fin);
  const frozen = observerStreamReducer(fin, { type: "freeze" });
  assert.equal(observerStreamReducer(frozen, { type: "delta", messageId: "m", delta: "x" }), frozen);
});

test("delta：空串同引用丢弃", () => {
  const s = live();
  assert.equal(observerStreamReducer(s, { type: "delta", messageId: "m", delta: "" }), s);
});

test("单泡 50k 截断置 truncated，其后同泡增量丢弃", () => {
  let s = live();
  s = observerStreamReducer(s, { type: "delta", messageId: "m", delta: "a".repeat(OBSERVER_BUBBLE_CHAR_LIMIT - 1) });
  s = observerStreamReducer(s, { type: "delta", messageId: "m", delta: "bb" });
  assert.equal(s.bubbles.m.text.length, OBSERVER_BUBBLE_CHAR_LIMIT);
  assert.equal(s.bubbles.m.truncated, true);
  const after = observerStreamReducer(s, { type: "delta", messageId: "m", delta: "c" });
  assert.equal(after, s);
});

test("泡数 40 封顶：超限新泡丢弃，既有泡仍可追加", () => {
  let s = live();
  for (let i = 0; i < OBSERVER_BUBBLE_MAX_COUNT; i++) {
    s = observerStreamReducer(s, { type: "delta", messageId: `m${i}`, delta: "x" });
  }
  const overflow = observerStreamReducer(s, { type: "delta", messageId: "extra", delta: "x" });
  assert.equal(overflow, s);
  const appended = observerStreamReducer(s, { type: "delta", messageId: "m0", delta: "y" });
  assert.equal(appended.bubbles.m0.text, "xy");
});

test("task_terminal：live→finalizing 泡保留；其余相位同引用", () => {
  let s = live();
  s = observerStreamReducer(s, { type: "delta", messageId: "m", delta: "正文" });
  const fin = observerStreamReducer(s, { type: "task_terminal" });
  assert.equal(fin.phase, "finalizing");
  assert.equal(fin.bubbles.m.text, "正文");
  assert.equal(observerStreamReducer(fin, { type: "task_terminal" }), fin);
  const idle = initialObserverStream();
  assert.equal(observerStreamReducer(idle, { type: "task_terminal" }), idle);
});

test("clear：任意相位归 initial；空 idle 同引用（useSyncExternalStore 依赖）", () => {
  const fin = observerStreamReducer(live(), { type: "task_terminal" });
  assert.deepEqual(observerStreamReducer(fin, { type: "clear" }), initialObserverStream());
  const idle = initialObserverStream();
  assert.equal(observerStreamReducer(idle, { type: "clear" }), idle);
});

test("freeze：finalizing/live→frozen 泡保留；idle 同引用", () => {
  let s = live();
  s = observerStreamReducer(s, { type: "delta", messageId: "m", delta: "留" });
  const frozen = observerStreamReducer(observerStreamReducer(s, { type: "task_terminal" }), { type: "freeze" });
  assert.equal(frozen.phase, "frozen");
  assert.equal(frozen.bubbles.m.text, "留");
  const idle = initialObserverStream();
  assert.equal(observerStreamReducer(idle, { type: "freeze" }), idle);
});

test("resync：live 置缺段标记一次；重复/非 live 同引用", () => {
  const s = live();
  const gapped = observerStreamReducer(s, { type: "resync" });
  assert.equal(gapped.resyncGap, true);
  assert.equal(observerStreamReducer(gapped, { type: "resync" }), gapped);
  const idle = initialObserverStream();
  assert.equal(observerStreamReducer(idle, { type: "resync" }), idle);
});

/* ------------------------------ isObserverDelta ------------------------------ */

const signalsBase: ObserverDeltaSignals = {
  apiMode: "real",
  runId: "run_1",
  eventRunId: "run_1",
  taskStatus: "running",
  runStatus: "active",
  aguiStreamRunId: null,
  copilotLocalRun: false,
  pendingSend: false,
  autoQuestion: false,
};

test("isObserverDelta 基准为 true", () => {
  assert.equal(isObserverDelta(signalsBase), true);
});

test("isObserverDelta 逐维翻转均为 false", () => {
  const flips: [string, Partial<ObserverDeltaSignals>][] = [
    ["mock 档无旁观语义", { apiMode: "mock" }],
    ["runId 为空", { runId: null }],
    ["事件不属当前 run", { eventRunId: "run_2" }],
    ["任务未运行", { taskStatus: "completed" }],
    ["任务态未知", { taskStatus: null }],
    ["会话已关闭", { runStatus: "closed" }],
    ["本端 agui 流在播（fallback 本端回合）", { aguiStreamRunId: "run_1" }],
    ["本端 CopilotKit 回合在播（copilot 本端回合）", { copilotLocalRun: true }],
    ["程序化发送在途", { pendingSend: true }],
    ["外链自动发问在途", { autoQuestion: true }],
  ];
  for (const [label, patch] of flips) {
    assert.equal(isObserverDelta({ ...signalsBase, ...patch }), false, label);
  }
});
