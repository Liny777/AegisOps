import assert from "node:assert/strict";
import test from "node:test";

import {
  activityStateFromSnapshot,
  mergeActivityEvents,
  type ActivityEventInput,
} from "../activity/model";
import { deriveRecoveryState } from "./recovery";

/** 事件构造走真归一化路径（mergeActivityEvents/activityStateFromSnapshot），不手搓 ActivityEvent。 */
const sseEvent = (
  id: string,
  eventType: string,
  occurredAt: string,
  payload: Record<string, unknown> = {},
  extra: Record<string, unknown> = {},
) => ({
  event_id: id,
  event_type: eventType,
  agent_run_id: "run-1",
  task_id: "tsk-1",
  occurred_at: occurredAt,
  payload_redacted_json: payload,
  ...extra,
});

const liveEvents = (rows: ActivityEventInput[]) => mergeActivityEvents([], rows, "live");

test("完整迁移链：required→approved→tool.call.started→succeeded 逐相位推进", () => {
  const required = sseEvent("e1", "openops.approval.required", "2026-07-20T10:00:00Z",
    { approval_request_id: "ap-1", tool: "recover_execute", target: "APP-A" },
    { message: "恢复动作待批准：重启 svc-a 释放连接" });
  // decide_approval 的 payload 无 tool 字段（run_state_service.py），只能按 id 关联
  const approved = sseEvent("e2", "openops.approval.approved", "2026-07-20T10:01:00Z",
    { approval_request_id: "ap-1", reason_chars: 0, summary: "已批准，恢复动作将执行" });
  const started = sseEvent("e3", "openops.tool.call.started", "2026-07-20T10:01:05Z",
    { tool: "recover_execute" });
  const succeeded = sseEvent("e4", "openops.tool.call.succeeded", "2026-07-20T10:01:20Z",
    { tool: "recover_execute", result_summary: "svc-a 已重启" });

  let events = liveEvents([required]);
  let state = deriveRecoveryState(events, { boardTaskId: "tsk-1" });
  assert.equal(state.phase, "pending");
  assert.equal(state.actions.length, 1);
  assert.equal(state.actions[0].tool, "recover_execute");
  assert.equal(state.actions[0].label, "恢复动作待批准：重启 svc-a 释放连接");
  assert.equal(state.actions[0].requiredAt, "2026-07-20T10:00:00Z");

  events = mergeActivityEvents(events, [approved], "live");
  assert.equal(deriveRecoveryState(events, { boardTaskId: "tsk-1" }).phase, "approved");

  events = mergeActivityEvents(events, [started], "live");
  assert.equal(deriveRecoveryState(events, { boardTaskId: "tsk-1" }).phase, "executing");

  events = mergeActivityEvents(events, [succeeded], "live");
  state = deriveRecoveryState(events, { boardTaskId: "tsk-1" });
  assert.equal(state.phase, "executed");
  assert.equal(state.actions[0].updatedAt, "2026-07-20T10:01:20Z");
  assert.deepEqual(state.counts, {
    total: 1, pending: 0, approved: 0, executing: 0, executed: 1, rejected: 0, timeout: 0, failed: 0,
  });
});

test("拒绝与超时：带 id 精确收口；无 id timeout 只收口 pending、不动 approved", () => {
  const events = liveEvents([
    sseEvent("r1", "openops.approval.required", "2026-07-20T10:00:00Z",
      { approval_request_id: "ap-rej", tool: "recover_execute" }),
    sseEvent("r2", "openops.approval.rejected", "2026-07-20T10:00:30Z",
      { approval_request_id: "ap-rej" }),
    sseEvent("t1", "openops.approval.required", "2026-07-20T10:01:00Z",
      { approval_request_id: "ap-tmo", tool: "recover_execute" }),
    // expire 审计路径的 timeout 带 id + tool（emit.py expire_stale_approvals_and_audit）
    sseEvent("t2", "openops.approval.timeout", "2026-07-20T10:06:00Z",
      { approval_request_id: "ap-tmo", tool: "recover_execute" }),
    sseEvent("p1", "openops.approval.required", "2026-07-20T10:07:00Z",
      { approval_request_id: "ap-pend", tool: "run_platform_skill" }),
    sseEvent("a1", "openops.approval.required", "2026-07-20T10:07:10Z",
      { approval_request_id: "ap-appr", tool: "recover_execute" }),
    sseEvent("a2", "openops.approval.approved", "2026-07-20T10:07:30Z",
      { approval_request_id: "ap-appr" }),
    // runtime 循环超时补发（agentscope_runtime.py:1144）：payload 为空、无 id
    sseEvent("t3", "openops.approval.timeout", "2026-07-20T10:12:00Z", {},
      { reason_code: "APPROVAL_TIMEOUT" }),
    // 补发之后才创建的新 pending：历史 timeout 不得误杀（按 requiredAt<=补发时刻收口）
    sseEvent("p2", "openops.approval.required", "2026-07-20T10:15:00Z",
      { approval_request_id: "ap-later", tool: "recover_execute" }),
  ]);
  const state = deriveRecoveryState(events, { boardTaskId: "tsk-1" });
  const phaseOf = (id: string) => state.actions.find((a) => a.approvalRequestId === id)?.phase;

  assert.equal(phaseOf("ap-rej"), "rejected");
  assert.equal(phaseOf("ap-tmo"), "timeout");
  assert.equal(phaseOf("ap-pend"), "timeout"); // 无 id timeout 只收口补发时刻前已在等的 pending
  assert.equal(phaseOf("ap-appr"), "approved"); // 已批准的不被无 id timeout 误伤
  assert.equal(phaseOf("ap-later"), "pending"); // 晚于补发时刻创建的不受影响
  assert.equal(state.phase, "pending");
  assert.equal(state.counts.timeout, 2);
});

test("前缀兼容：审计裸类型与 SSE openops.* 前缀同判，双通道同 event_id 合并为一条", () => {
  // 审计分页行：无 openops. 前缀（run_state_service._event_json 原样透出审计 event_type）
  const auditRows = [
    sseEvent("x1", "approval.required", "2026-07-20T10:00:00Z",
      { approval_request_id: "ap-x", tool: "recover_execute" },
      { audit_event_id: "x1" }),
    sseEvent("x2", "approval.approved", "2026-07-20T10:01:00Z",
      { approval_request_id: "ap-x" },
      { audit_event_id: "x2" }),
  ];
  let events = mergeActivityEvents([], auditRows, "audit");
  let state = deriveRecoveryState(events, { boardTaskId: "tsk-1" });
  assert.equal(state.actions.length, 1);
  assert.equal(state.phase, "approved");

  // 同 event_id 的 SSE 副本（带前缀）到达：merge 去重，不产生第二条动作
  events = mergeActivityEvents(events, [
    sseEvent("x1", "openops.approval.required", "2026-07-20T10:00:00.010Z",
      { approval_request_id: "ap-x", tool: "recover_execute" }),
  ], "live");
  state = deriveRecoveryState(events, { boardTaskId: "tsk-1" });
  assert.equal(state.actions.length, 1);
  assert.equal(state.phase, "approved");
});

test("乱序与重复幂等：迟到 required 不把 approved 拉回 pending，重复注水结果不变", () => {
  // occurred_at 倒挂：approved 先于 required（时钟偏差/审计补录），rank 守卫不回退
  const rows = [
    sseEvent("o1", "openops.approval.approved", "2026-07-20T10:00:00Z",
      { approval_request_id: "ap-o" }),
    sseEvent("o2", "openops.approval.required", "2026-07-20T10:00:05Z",
      { approval_request_id: "ap-o", tool: "recover_execute" },
      { message: "恢复动作待批准" }),
  ];
  let events = liveEvents(rows);
  const first = deriveRecoveryState(events, { boardTaskId: "tsk-1" });
  assert.equal(first.phase, "approved");
  assert.equal(first.actions[0].tool, "recover_execute"); // 迟到 required 仍补齐展示字段
  assert.equal(first.actions[0].label, "恢复动作待批准");

  // 双通道重复投递（同 event_id 再注水一遍）：推导结果逐字段一致
  events = mergeActivityEvents(events, rows, "state");
  assert.deepEqual(deriveRecoveryState(events, { boardTaskId: "tsk-1" }), first);
});

test("多审批聚合：pending 压过终态；全终态取最新一条的相位", () => {
  const base = [
    sseEvent("m1", "openops.approval.required", "2026-07-20T10:00:00Z",
      { approval_request_id: "ap-1", tool: "recover_execute" }),
    sseEvent("m2", "openops.approval.approved", "2026-07-20T10:01:00Z",
      { approval_request_id: "ap-1" }),
    sseEvent("m3", "openops.tool.call.started", "2026-07-20T10:01:05Z",
      { tool: "recover_execute" }),
    sseEvent("m4", "openops.tool.call.succeeded", "2026-07-20T10:01:10Z",
      { tool: "recover_execute" }),
    sseEvent("m5", "openops.approval.required", "2026-07-20T10:02:00Z",
      { approval_request_id: "ap-2", tool: "run_platform_skill" }),
  ];
  let events = liveEvents(base);
  let state = deriveRecoveryState(events, { boardTaskId: "tsk-1" });
  assert.equal(state.phase, "pending"); // 有未决审批时聚合相位必须是 pending
  assert.equal(state.counts.total, 2);
  assert.equal(state.counts.executed, 1);

  events = mergeActivityEvents(events, [
    sseEvent("m6", "openops.approval.rejected", "2026-07-20T10:03:00Z",
      { approval_request_id: "ap-2" }),
  ], "live");
  state = deriveRecoveryState(events, { boardTaskId: "tsk-1" });
  assert.equal(state.phase, "rejected"); // 全终态：rejected(10:03) 晚于 executed(10:01:10)
  assert.deepEqual(state.counts, {
    total: 2, pending: 0, approved: 0, executing: 0, executed: 1, rejected: 1, timeout: 0, failed: 0,
  });
});

test("段过滤（时间锚定）：锚前的上一轮排除、锚后的新任务纳入；无锚/null 不过滤", () => {
  const events = liveEvents([
    // 上一轮的审批（发生在当前板首条 rca.updated 之前）：不得漏进新板的恢复节点
    sseEvent("s1", "openops.approval.required", "2026-07-20T09:00:00Z",
      { approval_request_id: "ap-old", tool: "recover_execute" },
      { task_id: "tsk-old" }),
    // 当前板的时间锚：首条 rca.updated（payload.board_task_id 匹配）
    sseEvent("s-anchor", "openops.rca.updated", "2026-07-20T09:30:00Z",
      { board_task_id: "tsk-new", revision: 1 },
      { task_id: "tsk-new" }),
    // 真实主流程:「采纳并生成恢复动作」= 新消息 = 新任务,审批事件的 taskId 不等于
    // boardTaskId 且无 leader_task_id——必须靠「锚后」纳入(审查确认项)
    sseEvent("s2", "openops.approval.required", "2026-07-20T10:00:00Z",
      { approval_request_id: "ap-adopt", tool: "recover_execute" },
      { task_id: "tsk-recover-round" }),
  ]);

  const filtered = deriveRecoveryState(events, { boardTaskId: "tsk-new" });
  assert.deepEqual(filtered.actions.map((a) => a.approvalRequestId), ["ap-adopt"]);
  assert.equal(filtered.phase, "pending");

  // 找不到锚（rca.updated 被窗口滚出）：宁可多显示不可失踪
  const noAnchor = deriveRecoveryState(
    events.filter((event) => !event.eventType.includes("rca.updated")),
    { boardTaskId: "tsk-new" },
  );
  assert.equal(noAnchor.counts.total, 2);

  const unfiltered = deriveRecoveryState(events, { boardTaskId: null });
  assert.equal(unfiltered.counts.total, 2);
});

test("容器 Bash 审批不被 sandbox 事件收口：allow 只读路径同形事件不得误配（诚实停在已批准）", () => {
  const events = liveEvents([
    sseEvent("sb-r1", "openops.approval.required", "2026-07-20T10:00:00Z",
      { approval_request_id: "ap-cmd1", tool: "run_container_command", args: { command: "systemctl restart svc-a" } }),
    sseEvent("sb-a1", "openops.approval.approved", "2026-07-20T10:00:30Z", { approval_request_id: "ap-cmd1" }),
    // 审批命令执行完成与 allow 只读命令的事件完全同形（无审批关联字段）——都不得收口
    sseEvent("sb-x1", "openops.sandbox.command.executed", "2026-07-20T10:00:40Z",
      { command: "systemctl restart svc-a", exit_code: 0 }),
    sseEvent("sb-x2", "openops.sandbox.command.executed", "2026-07-20T10:00:50Z",
      { command: "cat /proc/meminfo", exit_code: 1 }),
  ]);
  const state = deriveRecoveryState(events, { boardTaskId: "tsk-1" });
  assert.equal(state.actions[0]?.phase, "approved"); // 不误标 executed/failed
  assert.equal(state.phase, "approved");
});

test("pendingApprovals 种子：按 id 与事件去重、被窗口挤出时兜底、跨段行排除", () => {
  const seedRow = (id: string, taskId = "tsk-1") => ({
    approval_request_id: id,
    task_id: taskId,
    tool_call_name: "recover_execute",
    creation_date: "2026-07-20T10:00:00Z",
    decision: "pending",
  });

  // 种子 + 同 id required 事件（走 /state 快照真归一化路径）：只产出一条动作，label 来自事件
  const snapshot = activityStateFromSnapshot({
    recent_events: [sseEvent("sd1", "openops.approval.required", "2026-07-20T10:00:00Z",
      { approval_request_id: "ap-seed", tool: "recover_execute" },
      { message: "恢复动作待批准" })],
  });
  let state = deriveRecoveryState(snapshot.events, {
    boardTaskId: "tsk-1",
    pendingApprovals: [seedRow("ap-seed"), seedRow("ap-seed")], // 重复行也去重
  });
  assert.equal(state.actions.length, 1);
  assert.equal(state.actions[0].label, "恢复动作待批准");
  assert.equal(state.phase, "pending");

  // required 被 100 条窗口挤出：种子兜底建条目，approved 事件仍按 id 收口
  state = deriveRecoveryState(liveEvents([
    sseEvent("sd2", "openops.approval.approved", "2026-07-20T10:05:00Z",
      { approval_request_id: "ap-seed" }),
  ]), { boardTaskId: "tsk-1", pendingApprovals: [seedRow("ap-seed")] });
  assert.equal(state.actions.length, 1);
  assert.equal(state.actions[0].tool, "recover_execute"); // tool 来自种子行 tool_call_name
  assert.equal(state.phase, "approved");

  // 种子行不做段过滤：行是「当前仍待批」的实时真值，上一轮残留的 pending 仍可操作、值得显示
  state = deriveRecoveryState([], {
    boardTaskId: "tsk-1",
    pendingApprovals: [seedRow("ap-old", "tsk-old"), seedRow("ap-sub", "tsk-1.recover-ab12cd34")],
  });
  assert.deepEqual(state.actions.map((a) => a.approvalRequestId).sort(), ["ap-old", "ap-sub"]);

  // 种子后置：历史里无 id 的 timeout 补发不得收口「当前仍待批」的种子行
  state = deriveRecoveryState(liveEvents([
    sseEvent("sd3", "openops.approval.timeout", "2026-07-20T09:00:00Z", {}),
  ]), { boardTaskId: "tsk-1", pendingApprovals: [seedRow("ap-live")] });
  assert.equal(state.actions[0]?.phase, "pending");
});
