import assert from "node:assert/strict";
import test from "node:test";

import {
  activityReducer,
  createActivityState,
  mergeActivityEvents,
  normalizeActivityPage,
  normalizeDelegation,
  prependActivityPage,
  projectRailModel,
} from "./model";
import { isOpsDiagnosticEvent, showInBusinessTimeline, technicalMeta } from "../../workbench/activity/eventPresentation";

const auditEvent = (
  id: string,
  eventType: string,
  occurredAt: string,
  payload: Record<string, unknown> = {},
) => ({
  audit_event_id: id,
  event_id: id,
  event_type: eventType,
  agent_run_id: "run-1",
  occurred_at: occurredAt,
  payload_redacted_json: payload,
});

test("state/audit/live 使用 event_id 去重、按时间与 sequence 稳定排序，并过滤思考文本", () => {
  let events = mergeActivityEvents([], [
    { ...auditEvent("e2", "openops.tool.call.started", "2026-07-14T02:00:00Z"), sequence: 2 },
    { ...auditEvent("e1", "openops.subagent.started", "2026-07-14T02:00:00Z"), sequence: 1 },
    auditEvent("hidden", "openops.model.thinking.delta", "2026-07-14T02:00:01Z"),
    // 新增：模型思考增量走 assistant 流展示为 reasoning 折叠卡，同样不进活动栏（regex 命中 .thinking.）。
    auditEvent("hidden2", "openops.assistant.thinking.delta", "2026-07-14T02:00:01Z"),
  ], "state");
  events = mergeActivityEvents(events, [{
    event_id: "e1",
    event_type: "openops.subagent.started",
    agent_run_id: "run-1",
    sequence: 1,
    occurred_at: "2026-07-14T02:00:00.010Z",
    message: "开始检查",
    payload_redacted_json: { agent_key: "inspect", delegation_id: "d1" },
  }], "live");

  assert.deepEqual(events.map((event) => event.eventId), ["e1", "e2"]);
  assert.equal(events[0].message, "开始检查");
  assert.deepEqual(events[0].sources.sort(), ["live", "state"]);
});

test("同 ID 的直接审计类型不会覆盖 openops 实时类型或降低 severity", () => {
  const direct = auditEvent("same", "task.started", "2026-07-14T02:00:00Z");
  const live = {
    ...auditEvent("same", "openops.task.started", "2026-07-14T02:00:00.010Z"),
    severity: "warning",
    sequence: 8,
  };

  const liveFirst = mergeActivityEvents(
    mergeActivityEvents([], [live], "live"), [direct], "state",
  )[0];
  const auditFirst = mergeActivityEvents(
    mergeActivityEvents([], [direct], "state"), [live], "live",
  )[0];
  for (const event of [liveFirst, auditFirst]) {
    assert.equal(event.eventType, "openops.task.started");
    assert.equal(event.severity, "warning");
    assert.equal(event.sequence, 8);
  }
});

test("hydrate 只补齐 snapshot，不覆盖已经先到的实时事件", () => {
  const live = activityReducer(createActivityState(), {
    type: "merge_events",
    source: "live",
    events: [auditEvent("live-1", "openops.subagent.started", "2026-07-14T02:01:00Z", {
      agent_key: "inspect",
      delegation_id: "d1",
    })],
  });
  const hydrated = activityReducer(live, {
    type: "hydrate",
    snapshot: {
      recent_events: [auditEvent("old-1", "task.started", "2026-07-14T02:00:00Z")],
      events_cursor: "cursor-1",
      events_has_more: true,
    },
  });

  assert.deepEqual(hydrated.events.map((event) => event.eventId), ["old-1", "live-1"]);
  assert.equal(hydrated.nextCursor, "cursor-1");
  assert.equal(hydrated.hasMore, true);
});

test("同一角色重复派发按 delegation 独立成轨，账本终态优先于事件推断", () => {
  const state = activityReducer(createActivityState(), {
    type: "hydrate",
    snapshot: {
      recent_events: [
        auditEvent("e1", "openops.subagent.started", "2026-07-14T02:00:00Z", {
          agent_key: "inspect", agent_label: "巡检", delegation_id: "d1",
          dispatch_batch_id: "b1", dispatch_batch_no: 3,
        }),
        auditEvent("e2", "openops.subagent.started", "2026-07-14T02:00:01Z", {
          agent_key: "inspect", agent_label: "巡检", delegation_id: "d2",
          dispatch_batch_id: "b1", dispatch_batch_no: 3,
        }),
      ],
      delegations: [
        { delegation_id: "d1", agent_key: "inspect", delegation_status: "completed",
          dispatch_batch_id: "b1", dispatch_batch_no: 3, task_summary: "检查日志" },
        { delegation_id: "d2", agent_key: "inspect", delegation_status: "running",
          dispatch_batch_id: "b1", dispatch_batch_no: 3, task_summary: "检查指标" },
      ],
    },
  });
  const rail = projectRailModel(state);

  assert.equal(rail.rounds.length, 1);
  assert.equal(rail.rounds[0].label, "第 3 轮");
  assert.equal(rail.rounds[0].tracks.length, 2);
  assert.deepEqual(new Set(rail.rounds[0].tracks.map((track) => track.delegation.delegationId)),
    new Set(["d1", "d2"]));
  assert.equal(rail.rounds[0].counts.completed, 1);
  assert.equal(rail.rounds[0].counts.running, 1);
  assert.equal(rail.rounds[0].status, "running");
  assert.equal(rail.rounds[0].tracks.find((track) => track.delegation.delegationId === "d1")?.delegation.status,
    "completed");
});

test("running 账本快照会被随后到达的实时终态收口，已落账终态不被反向覆盖", () => {
  const running = activityReducer(createActivityState(), {
    type: "hydrate",
    snapshot: {
      delegations: [{
        delegation_id: "d-live",
        agent_key: "inspect",
        delegation_status: "running",
        dispatch_batch_id: "b-live",
        dispatch_batch_no: 4,
      }],
    },
  });
  const reported = activityReducer(running, {
    type: "merge_events",
    source: "live",
    events: [auditEvent("reported", "openops.subagent.reported", "2026-07-14T02:02:00Z", {
      agent_key: "inspect",
      delegation_id: "d-live",
      dispatch_batch_id: "b-live",
      dispatch_batch_no: 4,
    })],
  });
  assert.equal(projectRailModel(reported).rounds[0].tracks[0].delegation.status, "completed");

  const settled = activityReducer(createActivityState(), {
    type: "hydrate",
    snapshot: {
      delegations: [{
        delegation_id: "d-settled",
        agent_key: "recover",
        delegation_status: "completed",
        dispatch_batch_id: "b-settled",
      }],
      recent_events: [auditEvent("late-failed", "openops.subagent.failed", "2026-07-14T02:03:00Z", {
        agent_key: "recover",
        delegation_id: "d-settled",
        dispatch_batch_id: "b-settled",
      })],
    },
  });
  assert.equal(projectRailModel(settled).rounds[0].tracks[0].delegation.status, "completed");
});

test("审计行与实时 envelope 均保留 external_request_id 供技术视图展示", () => {
  const [event] = mergeActivityEvents([], [{
    ...auditEvent("request", "openops.tool.call.succeeded", "2026-07-14T02:00:00Z"),
    external_request_id: "req-123",
  }], "audit");
  assert.equal(event.externalRequestId, "req-123");
  assert.deepEqual(technicalMeta(event).find((item) => item.label === "请求"), {
    label: "请求",
    value: "req-123",
  });
});

test("running 快照可被四类实时终态推进，并保留事件里的角色展示名", () => {
  const cases = [
    ["openops.subagent.reported", "completed"],
    ["openops.subagent.failed", "failed"],
    ["openops.subagent.timeout", "timeout"],
    ["openops.subagent.cancelled", "cancelled"],
  ] as const;
  for (const [eventType, status] of cases) {
    const base = activityReducer(createActivityState(), {
      type: "hydrate",
      snapshot: {
        delegations: [{
          delegation_id: `d-${status}`,
          agent_key: "inspect",
          agent_label: "inspect",
          delegation_status: "running",
          dispatch_batch_id: `b-${status}`,
        }],
      },
    });
    const next = activityReducer(base, {
      type: "merge_events",
      source: "live",
      events: [auditEvent(`e-${status}`, eventType, "2026-07-14T04:00:00Z", {
        agent_key: "inspect",
        agent_label: "巡检 Agent",
        delegation_id: `d-${status}`,
        dispatch_batch_id: `b-${status}`,
      })],
    });
    const track = projectRailModel(next).rounds[0].tracks[0];
    assert.equal(track.delegation.status, status);
    assert.equal(track.delegation.agentLabel, "巡检 Agent");
  }
});

test("重连 hydrate 不会把已经前翻的历史游标重置到最新页", () => {
  const hydrated = activityReducer(createActivityState(), {
    type: "hydrate",
    snapshot: {
      recent_events: [auditEvent("recent-cursor", "task.started", "2026-07-14T04:00:00Z")],
      events_next_cursor: "cursor-latest",
      events_has_more: true,
    },
  });
  const paged = activityReducer(hydrated, {
    type: "prepend_page",
    page: {
      items: mergeActivityEvents([], [
        auditEvent("older-cursor", "agent_run.created", "2026-07-14T03:00:00Z"),
      ], "audit"),
      next_cursor: "cursor-older",
      has_more: true,
    },
  });
  const reconnected = activityReducer(paged, {
    type: "hydrate",
    snapshot: {
      recent_events: [auditEvent("recent-cursor", "task.started", "2026-07-14T04:00:00Z")],
      events_next_cursor: "cursor-latest",
      events_has_more: true,
    },
  });
  assert.equal(reconnected.nextCursor, "cursor-older");
});

test("切换 Run 的 replace_snapshot 不保留上一 Run 的事件、派发或分页游标", () => {
  const firstRun = activityReducer(createActivityState(), {
    type: "hydrate",
    snapshot: {
      recent_events: [auditEvent("run-a-event", "openops.subagent.dispatched", "2026-07-14T04:00:00Z", {
        delegation_id: "run-a-delegation",
        dispatch_batch_id: "run-a-batch",
      })],
      delegations: [{
        delegation_id: "run-a-delegation",
        agent_key: "inspect",
        delegation_status: "running",
      }],
      events_next_cursor: "run-a-cursor",
      events_has_more: true,
    },
  });

  const secondRun = activityReducer(firstRun, {
    type: "replace_snapshot",
    snapshot: {
      recent_events: [auditEvent("run-b-event", "openops.task.completed", "2026-07-14T05:00:00Z")],
      delegations: [],
      events_next_cursor: null,
      events_has_more: false,
    },
  });

  assert.deepEqual(secondRun.events.map((event) => event.eventId), ["run-b-event"]);
  assert.deepEqual(secondRun.delegations, []);
  assert.equal(secondRun.nextCursor, null);
  assert.equal(secondRun.hasMore, false);
});

test("业务时间线只展示派发、工具或 Skill、审批与汇报里程碑", () => {
  const events = mergeActivityEvents([], [
    auditEvent("model", "openops.model.call.started", "2026-07-14T04:00:00Z"),
    auditEvent("sandbox", "openops.sandbox.command.executed", "2026-07-14T04:00:01Z"),
    auditEvent("tool", "openops.tool.call.started", "2026-07-14T04:00:02Z"),
    auditEvent("approval", "openops.approval.required", "2026-07-14T04:00:03Z"),
    auditEvent("report", "openops.subagent.reported", "2026-07-14T04:00:04Z"),
  ], "state");
  assert.deepEqual(events.filter(showInBusinessTimeline).map((event) => event.eventId), [
    "tool", "approval", "report",
  ]);
});

test("装配缺口诊断事件不进业务时间线（只进技术轨）", () => {
  // BUSINESS_MILESTONE 里 `tool(?:\.call)?\.` 的 `.call` 可选、`skill\.` 更是直接匹配 ⇒ 两条
  // skipped 天然命中，曾把「74 个 MCP 工具未装配」这类运维信号糊进用户对话栏。
  // TECHNICAL_ONLY 显式排除，同时不得误伤真正的里程碑（tool.call.started / skill.completed 仍须展示）。
  const events = mergeActivityEvents([], [
    auditEvent("toolSkipped", "openops.tool.skipped", "2026-07-14T04:00:00Z"),
    auditEvent("skillSkipped", "openops.skill.skipped", "2026-07-14T04:00:01Z"),
    auditEvent("tool", "openops.tool.call.started", "2026-07-14T04:00:02Z"),
    auditEvent("skill", "openops.skill.completed", "2026-07-14T04:00:03Z"),
  ], "state");
  assert.deepEqual(events.filter(showInBusinessTimeline).map((event) => event.eventId), ["tool", "skill"]);
  // 「全部动态」（ActivityRail.GeneralActivityEvents）走 isOpsDiagnosticEvent 反向过滤——
  // 主 Agent 发的 tool.skipped 不进子 Agent 轮次、只出现在 generalEvents，业务时间线的
  // 过滤器管不到它；此判定必须与上面保持同一事件集，否则又从「全部动态」漏出去。
  assert.deepEqual(events.filter((event) => !isOpsDiagnosticEvent(event)).map((event) => event.eventId), [
    "tool", "skill",
  ]);
});

test("等待审批只是 running 子态，cancelled 会清除；安全投影不读取 raw 文本", () => {
  const delegation = normalizeDelegation({
    delegation_id: "d1",
    agent_key: "recover",
    delegation_status: "running",
    task_text: "raw secret task",
    report_text: "raw secret report",
    task_summary: "恢复实例",
    report_summary: "已完成安全检查",
  });
  assert.equal(delegation?.taskSummary, "恢复实例");
  assert.equal(delegation?.reportSummary, "已完成安全检查");
  assert.equal("taskText" in (delegation ?? {}), false);
  assert.equal("reportText" in (delegation ?? {}), false);

  const base = activityReducer(createActivityState(), {
    type: "merge_delegations",
    delegations: [delegation!],
  });
  const waiting = activityReducer(base, {
    type: "merge_events",
    source: "live",
    events: [auditEvent("approval-1", "openops.approval.required", "2026-07-14T02:00:00Z", {
      agent_key: "recover", delegation_id: "d1", approval_request_id: "a1",
    })],
  });
  assert.equal(projectRailModel(waiting).rounds[0].tracks[0].waitingApproval, true);

  const cancelled = activityReducer(waiting, {
    type: "merge_events",
    source: "live",
    events: [auditEvent("approval-2", "openops.approval.cancelled", "2026-07-14T02:00:01Z", {
      agent_key: "recover", delegation_id: "d1", approval_request_id: "a1",
    })],
  });
  assert.equal(projectRailModel(cancelled).rounds[0].tracks[0].waitingApproval, false);
  assert.equal(projectRailModel(cancelled).rounds[0].tracks[0].delegation.status, "running");
});

test("空批次进入历史派发，历史页 prepend 后保留游标与去重", () => {
  const initial = activityReducer(createActivityState(), {
    type: "hydrate",
    snapshot: {
      recent_events: [auditEvent("recent", "openops.subagent.reported", "2026-07-14T03:00:00Z", {
        agent_key: "diagnose", delegation_id: "legacy-d1", leader_task_id: "task-1",
      })],
      delegations: [{ delegation_id: "legacy-d1", leader_task_id: "task-1", agent_key: "diagnose",
        delegation_status: "completed" }],
      events_cursor: "c1",
      events_has_more: true,
    },
  });
  assert.equal(projectRailModel(initial).rounds[0].label, "历史派发");

  const page = normalizeActivityPage({
    items: [
      auditEvent("older", "task.started", "2026-07-14T01:00:00Z"),
      auditEvent("recent", "openops.subagent.reported", "2026-07-14T03:00:00Z"),
    ],
    next_cursor: "c0",
    has_more: false,
  });
  const merged = prependActivityPage(initial, page);
  assert.deepEqual(merged.events.map((event) => event.eventId), ["older", "recent"]);
  assert.equal(merged.nextCursor, "c0");
  assert.equal(merged.hasMore, false);
});
