import assert from "node:assert/strict";
import test from "node:test";

import { initialRcaGuard, nextRcaGuard, shouldApplyRca } from "./model";
import { parseRcaCard } from "./schema";

const steps = [
  { num: 1, label: "范围", state: "done" },
  { num: 2, label: "证据", state: "active" },
  { num: 3, label: "假设", state: "waiting" },
  { num: 4, label: "验证", state: "waiting" },
  { num: 5, label: "结论", state: "waiting" },
];

test("同 task revision 严格单调：3 之后拒收 2/3，接受 4", () => {
  let guard = initialRcaGuard();
  assert.equal(shouldApplyRca(guard, "tsk-1", 3), true);
  guard = nextRcaGuard(guard, "tsk-1", 3);
  assert.equal(shouldApplyRca(guard, "tsk-1", 2), false); // resync 带回旧快照：不闪回
  assert.equal(shouldApplyRca(guard, "tsk-1", 3), false); // 双通道重复投递：幂等
  assert.equal(shouldApplyRca(guard, "tsk-1", 4), true);
});

test("缺 revision 的旧 payload last-write-wins，且不倒退已知基线", () => {
  let guard = initialRcaGuard();
  guard = nextRcaGuard(guard, "tsk-1", 3);
  assert.equal(shouldApplyRca(guard, "tsk-1", null), true); // 旧后端无 revision：后到覆盖
  guard = nextRcaGuard(guard, "tsk-1", null);
  assert.equal(guard.revision, 3); // 基线保留
  assert.equal(shouldApplyRca(guard, "tsk-1", 2), false); // 之后带 revision 的旧事件仍被拒
});

test("跨 task 重置：同 run 第二次定界 revision 从 1 重计也接受", () => {
  let guard = nextRcaGuard(initialRcaGuard(), "tsk-1", 5);
  assert.equal(shouldApplyRca(guard, "tsk-2", 1), true);
  guard = nextRcaGuard(guard, "tsk-2", 1);
  assert.deepEqual(guard, { taskId: "tsk-2", revision: 1 });
  assert.equal(shouldApplyRca(guard, "tsk-2", 1), false);
  assert.equal(shouldApplyRca(guard, "tsk-2", 2), true);
});

test("跨 task 且缺 revision：接受并把基线清空（新任务从头计）", () => {
  let guard = nextRcaGuard(initialRcaGuard(), "tsk-1", 9);
  guard = nextRcaGuard(guard, "tsk-2", null);
  assert.deepEqual(guard, { taskId: "tsk-2", revision: null });
  assert.equal(shouldApplyRca(guard, "tsk-2", 1), true); // 不被旧任务的 9 压制
});

test("guard 推进：taskId 缺失归一化为 null", () => {
  const guard = nextRcaGuard(initialRcaGuard(), undefined, 2);
  assert.deepEqual(guard, { taskId: null, revision: 2 });
  assert.equal(shouldApplyRca(guard, null, 3), true);
  assert.equal(shouldApplyRca(guard, null, 2), false);
});

test("schema：全量快照通过并剥离未知键（emit 注入的 agent_key 不进渲染模型）", () => {
  const parsed = parseRcaCard({
    title: "支付延迟突增",
    phaseLabel: "定界中",
    revision: 2,
    status: "in_progress",
    steps,
    agent_key: "main", // emit 注入的旁路字段
    facts: [{ text: "P99 升至 1.4s" }],
    hypotheses: [{ text: "H1 连接泄漏", tag: "支持", tagTone: "good", conf: 0.72 }],
  });
  assert.ok(parsed);
  assert.equal(parsed.revision, 2);
  assert.equal(parsed.status, "in_progress");
  assert.equal("agent_key" in parsed, false);
  assert.deepEqual(parsed.tiles, []); // 缺字段给默认，不崩渲染
  assert.equal(parsed.conclusion, "");
});

test("schema：缺 steps / 非法枚举静默拒收（返回 undefined）", () => {
  assert.equal(parseRcaCard({ title: "无步骤" }), undefined);
  assert.equal(parseRcaCard({ steps: [] }), undefined);
  assert.equal(parseRcaCard({ steps: [{ num: 1, label: "范围", state: "flying" }] }), undefined);
  assert.equal(parseRcaCard({ steps, status: "done" }), undefined); // status 枚举外
  assert.equal(parseRcaCard({ steps, hypotheses: [{ text: "H1", conf: 1.5 }] }), undefined); // conf 越界
  assert.equal(parseRcaCard(null), undefined);
});

test("分段键=board_task_id：子/主交替发事件同段单调，旧 revision 拒收", () => {
  // 后端 envelope 的 task_id 是调用方（子 Agent 更新带子 task_id、主任务兜底带主 task_id），
  // 但 payload.board_task_id 恒为 owner 主任务——Workbench 用它作分段键，
  // 归属交替不会被误判「新任务」重置守卫。
  let guard = initialRcaGuard();
  guard = nextRcaGuard(guard, "tsk_leader", 3); // 子 Agent 更新（board_task_id=tsk_leader）
  assert.equal(shouldApplyRca(guard, "tsk_leader", 4), true); // 主任务兜底，同段推进
  guard = nextRcaGuard(guard, "tsk_leader", 4);
  assert.equal(shouldApplyRca(guard, "tsk_leader", 3), false); // 迟到旧 revision 拒收
  assert.equal(shouldApplyRca(guard, "tsk_next", 1), true); // 真正的新任务：换段接受
});
