import assert from "node:assert/strict";
import test from "node:test";

import { recoverSnapshotMessages } from "./workbenchRecovery";

const snapshot = (over: Record<string, unknown> = {}) => ({
  active_task: { task_id: "t1", input_text: "帮我诊断MySQL告警", status: "running" },
  recent_events: [],
  ...over,
});

test("仅有 input_text：恢复出单条用户泡（id 锚定 run）", () => {
  const m = recoverSnapshotMessages(snapshot(), "run_1");
  assert.deepEqual(m, [{ id: "u-run_1", role: "user", text: "帮我诊断MySQL告警" }]);
});

test("无 input_text 无事件：空列表", () => {
  assert.deepEqual(recoverSnapshotMessages({ active_task: {}, recent_events: [] }, "r"), []);
});

test("completed 终态：conclusion 优先，summary 回退，showCopy=true", () => {
  const withConclusion = recoverSnapshotMessages(snapshot({
    recent_events: [
      { task_id: "t1", event_type: "openops.task.completed", event_id: "ev1",
        payload_redacted_json: { conclusion: "根因是连接泄漏", summary: "任务完成" } },
    ],
  }), "run_1");
  assert.equal(withConclusion[1].text, "根因是连接泄漏");
  assert.equal(withConclusion[1].showCopy, true);
  assert.equal(withConclusion[1].id, "ev1");

  const withSummary = recoverSnapshotMessages(snapshot({
    recent_events: [
      { task_id: "t1", event_type: "openops.task.completed", event_id: "ev1",
        payload_redacted_json: { summary: "任务完成" } },
    ],
  }), "run_1");
  assert.equal(withSummary[1].text, "任务完成");
});

test("failed 终态：summary → error_summary 回退链，showCopy 不置", () => {
  const m = recoverSnapshotMessages(snapshot({
    recent_events: [
      { task_id: "t1", event_type: "openops.task.failed", event_id: "ev2",
        payload_redacted_json: { error_summary: "模型调用失败" } },
    ],
  }), "run_1");
  assert.equal(m[1].text, "模型调用失败");
  assert.notEqual(m[1].showCopy, true);
});

test("task_id 不匹配的终态事件不串轮（旧任务结论不得混入当前会话）", () => {
  const m = recoverSnapshotMessages(snapshot({
    recent_events: [
      { task_id: "t0", event_type: "openops.task.completed", event_id: "old",
        payload_redacted_json: { conclusion: "上一轮的结论" } },
    ],
  }), "run_1");
  assert.equal(m.length, 1); // 只有用户泡
});

test("无 task_id 时不猜测 recent_events 归属", () => {
  const m = recoverSnapshotMessages({
    active_task: { input_text: "问题" },
    recent_events: [
      { task_id: "t1", event_type: "openops.task.completed", event_id: "ev",
        payload_redacted_json: { conclusion: "结论" } },
    ],
  }, "run_1");
  assert.equal(m.length, 1);
});

test("同 task 多终态取最后一个（重试后以最终态为准）", () => {
  const m = recoverSnapshotMessages(snapshot({
    recent_events: [
      { task_id: "t1", event_type: "openops.task.failed", event_id: "ev-f",
        payload_redacted_json: { summary: "第一次失败" } },
      { task_id: "t1", event_type: "openops.task.completed", event_id: "ev-c",
        payload_redacted_json: { conclusion: "重试成功" } },
    ],
  }), "run_1");
  assert.equal(m[1].id, "ev-c");
  assert.equal(m[1].text, "重试成功");
});

test("终态缺正文或缺 event_id：不合成气泡（宁缺勿臆造）", () => {
  const noText = recoverSnapshotMessages(snapshot({
    recent_events: [
      { task_id: "t1", event_type: "openops.task.completed", event_id: "ev", payload_redacted_json: {} },
    ],
  }), "run_1");
  assert.equal(noText.length, 1);
  const noId = recoverSnapshotMessages(snapshot({
    recent_events: [
      { task_id: "t1", event_type: "openops.task.completed", payload_redacted_json: { conclusion: "x" } },
    ],
  }), "run_1");
  assert.equal(noId.length, 1);
});
