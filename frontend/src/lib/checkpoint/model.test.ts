import assert from "node:assert/strict";
import test from "node:test";
import {
  applyCheckpointClosed,
  applyCheckpointExtended,
  checkpointFromOpened,
  remainingSeconds,
} from "./model";

const PENDING = { checkpointId: "ckpt-1", deadlineAt: "2026-08-16T10:00:10+00:00", status: "pending" } as const;

test("opened → pending；缺 checkpoint_id 丢弃", () => {
  assert.deepEqual(
    checkpointFromOpened({ checkpoint_id: "ckpt-1", deadline_at: "2026-08-16T10:00:10+00:00" }),
    PENDING,
  );
  assert.equal(checkpointFromOpened({ deadline_at: "x" }), undefined);
});

test("extended 更新 deadline：仅同卡且 pending", () => {
  const extended = applyCheckpointExtended({ ...PENDING }, {
    checkpoint_id: "ckpt-1", deadline_at: "2026-08-16T10:03:00+00:00",
  });
  assert.equal(extended?.deadlineAt, "2026-08-16T10:03:00+00:00");
  // 旧卡迟到的 extended 不动当前卡
  const other = applyCheckpointExtended({ ...PENDING }, { checkpoint_id: "ckpt-0", deadline_at: "x" });
  assert.equal(other?.deadlineAt, PENDING.deadlineAt);
  // 已定格结果态不再延长
  const done = { ...PENDING, status: "continued" as const };
  assert.equal(applyCheckpointExtended(done, { checkpoint_id: "ckpt-1", deadline_at: "y" }), done);
});

test("closed → 结果态三分支；旧卡 closed 不误伤", () => {
  assert.equal(applyCheckpointClosed({ ...PENDING }, { checkpoint_id: "ckpt-1", action: "continue", timed_out: true })?.status, "timed_out");
  assert.equal(applyCheckpointClosed({ ...PENDING }, { checkpoint_id: "ckpt-1", action: "add_hypothesis", timed_out: false })?.status, "added");
  assert.equal(applyCheckpointClosed({ ...PENDING }, { checkpoint_id: "ckpt-1", action: "continue", timed_out: false })?.status, "continued");
  assert.equal(applyCheckpointClosed({ ...PENDING }, { checkpoint_id: "ckpt-9", action: "continue" })?.status, "pending");
  assert.equal(applyCheckpointClosed(undefined, { checkpoint_id: "ckpt-1" }), undefined);
});

test("remainingSeconds：向上取整、不为负、无效输入回 0", () => {
  const deadline = "2026-08-16T10:00:10+00:00";
  const base = new Date("2026-08-16T10:00:00+00:00").getTime();
  assert.equal(remainingSeconds(deadline, base), 10);
  assert.equal(remainingSeconds(deadline, base + 9_500), 1); // 0.5s 剩余 → 显示 1
  assert.equal(remainingSeconds(deadline, base + 60_000), 0);
  assert.equal(remainingSeconds("not-a-date", base), 0);
});
