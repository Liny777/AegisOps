import assert from "node:assert/strict";
import test from "node:test";

import {
  createLatestWorkbenchTargetScheduler,
  parseWorkbenchTarget,
  shouldShowWorkbenchColdStart,
  shouldReuseWorkbench,
  type ResolvedWorkbenchSession,
  type WorkbenchTarget,
} from "./workbenchSession";
import {
  clearRunRecoveryIssue,
  isCurrentRunAction,
  isCurrentRunStream,
  isOwnedOrStaleInitializationCancellation,
  recoverSnapshotMessages,
  resetActivityPageRequest,
  shouldShowWorkbenchPortal,
  workbenchTransitionUi,
  type WorkbenchRecoveryIssue,
} from "../workbench/workbenchRecovery";
import {
  bindCopilotThread,
  isCopilotThreadReady,
  shouldMountCopilotAutoSend,
} from "../workbench/copilot/threadBinding";

const instanceTarget = (instanceId: string, explicitRunId: string | null = null): WorkbenchTarget => ({
  instanceId,
  explicitRunId,
});

const runTarget = (runId: string): WorkbenchTarget => ({ instanceId: "", explicitRunId: runId });

const session = (
  runStatus: ResolvedWorkbenchSession["runStatus"] = "active",
): ResolvedWorkbenchSession => ({
  runId: "run-1",
  instanceId: "instance-1",
  runStatus,
});

test("解析 Agent 对话路由及可选 run_id", () => {
  assert.deepEqual(parseWorkbenchTarget("/agent-teams/instance-1/chat", ""), {
    instanceId: "instance-1",
    explicitRunId: null,
  });
  assert.deepEqual(parseWorkbenchTarget("/agent-teams/instance-1/chat/", "?run_id=run-1"), {
    instanceId: "instance-1",
    explicitRunId: "run-1",
  });
  assert.deepEqual(parseWorkbenchTarget("/agent-teams/team%20one/chat", "?run_id="), {
    instanceId: "team one",
    explicitRunId: null,
  });
});

test("解析 Run 恢复路由并以路径参数为准", () => {
  assert.deepEqual(parseWorkbenchTarget("/agent-runs/run%201", "?run_id=ignored"), {
    instanceId: "",
    explicitRunId: "run 1",
  });
});

test("非对话路由不产生 Workbench target", () => {
  for (const pathname of [
    "/settings",
    "/agent-teams/instance-1/settings",
    "/agent-teams/instance-1/edit",
    "/agents",
    "/agent-runs",
    "/agent-runs/run-1/events",
  ]) {
    assert.equal(parseWorkbenchTarget(pathname, ""), null, pathname);
  }
});

test("非对话页冷进入聊天且 retained 尚未创建时显示稳定占位", () => {
  assert.equal(shouldShowWorkbenchColdStart(runTarget("run-1"), false), true);
  assert.equal(shouldShowWorkbenchColdStart(runTarget("run-1"), true), false);
  assert.equal(shouldShowWorkbenchColdStart(null, false), false);
});

test("拿到 provisional/resolved runId 后可跨两种路由形式复用", () => {
  assert.equal(shouldReuseWorkbench(
    instanceTarget("instance-1"),
    session(),
    runTarget("run-1"),
  ), true);
  assert.equal(shouldReuseWorkbench(
    runTarget("run-1"),
    session(),
    instanceTarget("instance-1", "run-1"),
  ), true);
});

test("无显式 run 仅复用同一实例的 active session", () => {
  assert.equal(shouldReuseWorkbench(instanceTarget("instance-1"), session(), instanceTarget("instance-1")), true);
  assert.equal(shouldReuseWorkbench(instanceTarget("instance-1"), session(), instanceTarget("instance-2")), false);
  assert.equal(shouldReuseWorkbench(instanceTarget("instance-1"), session("closed"), instanceTarget("instance-1")), false);
});

test("显式切换到不同 run 时不复用", () => {
  assert.equal(shouldReuseWorkbench(instanceTarget("instance-1"), session(), runTarget("run-2")), false);
  assert.equal(shouldReuseWorkbench(runTarget("run-1"), session(), instanceTarget("instance-1", "run-2")), false);
});

test("尚未 resolve 时仅相同原始 target 复用", () => {
  assert.equal(shouldReuseWorkbench(
    instanceTarget("instance-1"),
    null,
    instanceTarget("instance-1"),
  ), true);
  assert.equal(shouldReuseWorkbench(
    instanceTarget("instance-1", "run-1"),
    null,
    instanceTarget("instance-1", "run-2"),
  ), false);
  assert.equal(shouldReuseWorkbench(runTarget("run-1"), null, instanceTarget("instance-1", "run-1")), false);
  assert.equal(shouldReuseWorkbench(null, null, instanceTarget("instance-1")), false);
});

function fakeTimers() {
  let nextId = 1;
  const callbacks = new Map<number, () => void>();
  return {
    setTimer(callback: () => void) {
      const id = nextId++;
      callbacks.set(id, callback);
      return id;
    },
    clearTimer(handle: unknown) {
      callbacks.delete(handle as number);
    },
    flush() {
      const pending = [...callbacks.values()];
      callbacks.clear();
      pending.forEach((callback) => callback());
    },
    size: () => callbacks.size,
  };
}

test("快速 burst 只应用最后一个 Workbench target", () => {
  const timers = fakeTimers();
  const scheduler = createLatestWorkbenchTargetScheduler({
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });
  const applied: WorkbenchTarget[] = [];

  assert.equal(scheduler.request(runTarget("run-1"), session(), runTarget("run-2"), (target) => applied.push(target)), true);
  assert.equal(scheduler.request(runTarget("run-1"), session(), runTarget("run-3"), (target) => applied.push(target)), true);
  assert.equal(scheduler.request(runTarget("run-1"), session(), runTarget("run-4"), (target) => applied.push(target)), true);

  assert.equal(timers.size(), 1);
  assert.equal(scheduler.hasPending(), true);
  timers.flush();
  assert.deepEqual(applied, [runTarget("run-4")]);
  assert.equal(scheduler.hasPending(), false);
});

test("同一 canonical Run 不排队，并取消更早的待切换目标", () => {
  const timers = fakeTimers();
  const scheduler = createLatestWorkbenchTargetScheduler({
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });
  const applied: WorkbenchTarget[] = [];
  const currentSession = session();

  assert.equal(scheduler.request(
    instanceTarget("instance-1"),
    currentSession,
    runTarget("run-2"),
    (target) => applied.push(target),
  ), true);
  assert.equal(timers.size(), 1);

  // /agent-runs/run-1 与当前 instance route 指向同一个 canonical session。
  assert.equal(scheduler.request(
    instanceTarget("instance-1"),
    currentSession,
    runTarget("run-1"),
    (target) => applied.push(target),
  ), false);
  assert.equal(timers.size(), 0);
  assert.equal(scheduler.hasPending(), false);

  timers.flush();
  assert.deepEqual(applied, []);
});

test("同一 generic target 的 retained Run 已关闭时仍排队重新初始化", () => {
  const timers = fakeTimers();
  const scheduler = createLatestWorkbenchTargetScheduler({
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });
  const target = instanceTarget("instance-1");
  const applied: WorkbenchTarget[] = [];

  assert.equal(scheduler.request(target, session("closed"), target, (next) => applied.push(next)), true);
  timers.flush();
  assert.deepEqual(applied, [target]);
});

test("仅自身 controller 取消或旧 generation 可静默退出初始化", () => {
  assert.equal(isOwnedOrStaleInitializationCancellation(true, 4, 4), true);
  assert.equal(isOwnedOrStaleInitializationCancellation(false, 3, 4), true);
  // 当前代次收到的外部 AbortError 不在静默条件内，调用方必须进入可重试 error。
  assert.equal(isOwnedOrStaleInitializationCancellation(false, 4, 4), false);
});

test("加载更早事件期间切换 Run 会中止旧请求并立即解除 loading 锁", () => {
  const oldRequest = new AbortController();
  let loadingEarlier = true;
  let currentRequest: AbortController | null = oldRequest;

  currentRequest = resetActivityPageRequest(
    currentRequest,
    (loading) => { loadingEarlier = loading; },
  );

  assert.equal(oldRequest.signal.aborted, true);
  assert.equal(currentRequest, null);
  assert.equal(loadingEarlier, false);
  // 旧请求 finally 此时不会再拥有 request ref，但新会话已经可以重新分页。
  assert.equal(currentRequest === oldRequest, false);
});

test("后台旧 Run 或旧 token 的 AG-UI 回调不能写入当前会话", () => {
  const currentToken = Symbol("current");
  assert.equal(isCurrentRunStream("run-b", "run-a", currentToken, currentToken), false);
  assert.equal(isCurrentRunStream("run-b", "run-b", Symbol("stale"), currentToken), false);
  assert.equal(isCurrentRunStream("run-b", "run-b", currentToken, currentToken), true);
  assert.equal(isCurrentRunAction("run-b", "run-a"), false);
  assert.equal(isCurrentRunAction("run-b", "run-b"), true);
});

test("新 target 恢复失败时保留旧画面但继续禁止向旧 Run 输入", () => {
  const failed = workbenchTransitionUi({
    hasDisplayedRun: true,
    routeTransitionPending: false,
    transitionKey: "run-new",
    targetKey: "run-new",
    transitionStatus: "error",
  });
  assert.deepEqual(failed, {
    failedCurrentTarget: true,
    showSwitchOverlay: false,
    showFailureOverlay: true,
    blockInput: true,
  });
  assert.equal(shouldShowWorkbenchPortal(true, failed.blockInput), false);

  const ready = workbenchTransitionUi({
    hasDisplayedRun: true,
    routeTransitionPending: false,
    transitionKey: "run-new",
    targetKey: "run-new",
    transitionStatus: "ready",
  });
  assert.deepEqual(ready, {
    failedCurrentTarget: false,
    showSwitchOverlay: false,
    showFailureOverlay: false,
    blockInput: false,
  });
  assert.equal(shouldShowWorkbenchPortal(true, ready.blockInput), true);
  assert.equal(shouldShowWorkbenchPortal(false, ready.blockInput), false);
});

test("旧 Run resync 只能清自己的错误，不能清新 target 的切换错误", () => {
  const transitionIssue: WorkbenchRecoveryIssue = {
    owner: "transition",
    generation: 8,
    targetKey: "instance-2\u0000run-2",
    message: "会话切换失败；仍显示上一会话。",
  };
  const oldRunIssue: WorkbenchRecoveryIssue = {
    owner: "run",
    runId: "run-1",
    message: "旧 Run 恢复失败",
  };

  assert.equal(clearRunRecoveryIssue(transitionIssue, "run-1"), transitionIssue);
  assert.equal(clearRunRecoveryIssue(oldRunIssue, "run-1"), null);
  assert.equal(clearRunRecoveryIssue(oldRunIssue, "run-2"), oldRunIssue);
});

test("closed Run 从 recent_events 安全恢复同一 task 的最新终态消息", () => {
  const messages = recoverSnapshotMessages({
    active_task: {
      task_id: "task-latest",
      status: "completed",
      input_text: "检查支付网关",
    },
    recent_events: [
      {
        event_id: "wrong-task",
        task_id: "task-old",
        event_type: "openops.task.completed",
        payload_redacted_json: { conclusion: "不应串入当前会话" },
      },
      {
        event_id: "terminal-summary",
        task_id: "task-latest",
        event_type: "openops.task.completed",
        payload_redacted_json: { summary: "任务完成" },
      },
      {
        event_id: "terminal-conclusion",
        task_id: "task-latest",
        event_type: "openops.task.completed",
        payload_redacted_json: { conclusion: "确认连接池已恢复，延迟回落。", summary: "任务完成" },
      },
    ],
  }, "run-closed");

  assert.deepEqual(messages, [
    { id: "u-run-closed", role: "user", text: "检查支付网关" },
    {
      id: "terminal-conclusion",
      role: "bot",
      text: "确认连接池已恢复，延迟回落。",
      showCopy: true,
    },
  ]);
});

test("recent_events 缺少最新 task 身份时不猜测助手历史", () => {
  assert.deepEqual(recoverSnapshotMessages({
    active_task: { input_text: "只有输入" },
    recent_events: [{
      event_id: "ambiguous",
      event_type: "openops.task.completed",
      payload_redacted_json: { conclusion: "归属不明" },
    }],
  }, "run-1"), [
    { id: "u-run-1", role: "user", text: "只有输入" },
  ]);
});

test("Copilot 输入只在当前 Provider Agent 已绑定本次 commit 的 Run 后开放", () => {
  const agent: { threadId?: string } = { threadId: "run-old" };
  assert.equal(isCopilotThreadReady("run-new", "run-new", agent.threadId), false);

  bindCopilotThread(agent, "run-new");
  assert.equal(agent.threadId, "run-new");
  assert.equal(isCopilotThreadReady("run-new", "run-old", agent.threadId), false);
  assert.equal(isCopilotThreadReady("run-new", "run-new", agent.threadId), true);
  assert.equal(isCopilotThreadReady("run-new", "run-new", agent.threadId, true), false);
  assert.equal(shouldMountCopilotAutoSend(true, "排查告警", true), false);
  assert.equal(shouldMountCopilotAutoSend(false, "排查告警", true), true);
});
