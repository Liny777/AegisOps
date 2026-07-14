import assert from "node:assert/strict";
import test from "node:test";

import {
  parseWorkbenchTarget,
  shouldReuseWorkbench,
  type ResolvedWorkbenchSession,
  type WorkbenchTarget,
} from "./workbenchSession";

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
