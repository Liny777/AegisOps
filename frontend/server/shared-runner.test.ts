import { EventType, HttpAgent, type BaseEvent, type RunAgentInput } from "@ag-ui/client";
import {
  AgentRunner,
  CopilotRuntime,
  InMemoryAgentRunner,
  supportsLocalThreadEndpoints,
  type AgentRunnerConnectRequest,
  type AgentRunnerIsRunningRequest,
  type AgentRunnerRunRequest,
  type AgentRunnerStopRequest,
  type LocalThreadEndpointRecord,
} from "@copilotkit/runtime/v2";
import assert from "node:assert/strict";
import type { AddressInfo } from "node:net";
import test from "node:test";
import { Subject } from "rxjs";
import { createCopilotRuntimeApp } from "./copilot-runtime";
import { identityHeaders, runWithIdentity } from "./identity";
import { SharedConnectAgentRunner } from "./shared-runner";

const USER_A = { "x-openops-mock-user": "runtime-user-a" };
const USER_B = { "x-openops-mock-user": "runtime-user-b" };

function startedEvent(threadId: string, runId = `run-${threadId}`): BaseEvent {
  return { type: EventType.RUN_STARTED, threadId, runId } as BaseEvent;
}

function inputFor(threadId: string, runId = `run-${threadId}`): RunAgentInput {
  return {
    threadId,
    runId,
    state: {},
    messages: [],
    tools: [],
    context: [],
    forwardedProps: {},
  };
}

class FakeAgentRunner extends AgentRunner {
  readonly connectStreams = new Map<string, Subject<BaseEvent>[]>();
  readonly runStreams = new Map<string, Subject<BaseEvent>>();
  readonly connectRequests: string[] = [];
  readonly runRequests: string[] = [];
  readonly stopRequests: string[] = [];
  connectCalls = 0;
  runCalls = 0;
  stopCalls = 0;

  run(request: AgentRunnerRunRequest) {
    this.runCalls += 1;
    this.runRequests.push(request.threadId);
    const stream = new Subject<BaseEvent>();
    this.runStreams.set(request.threadId, stream);
    return stream.asObservable();
  }

  connect(request: AgentRunnerConnectRequest) {
    this.connectCalls += 1;
    this.connectRequests.push(request.threadId);
    const stream = new Subject<BaseEvent>();
    const streams = this.connectStreams.get(request.threadId) ?? [];
    streams.push(stream);
    this.connectStreams.set(request.threadId, streams);
    return stream.asObservable();
  }

  isRunning(request: AgentRunnerIsRunningRequest): Promise<boolean> {
    const stream = this.runStreams.get(request.threadId);
    return Promise.resolve(Boolean(stream && !stream.closed && !stream.isStopped));
  }

  stop(request: AgentRunnerStopRequest): Promise<boolean> {
    this.stopCalls += 1;
    this.stopRequests.push(request.threadId);
    const stream = this.runStreams.get(request.threadId);
    if (!stream || stream.closed || stream.isStopped) return Promise.resolve(false);
    stream.complete();
    return Promise.resolve(true);
  }

  connectStreamAt(index: number): Subject<BaseEvent> {
    const threadId = this.connectRequests[index];
    const streams = threadId ? this.connectStreams.get(threadId) : undefined;
    assert.ok(streams?.length, `missing connect stream at call ${index}`);
    return streams.at(-1)!;
  }
}

class FakeLocalAgentRunner extends FakeAgentRunner {
  readonly ɵsupportsLocalThreadEndpoints = true as const;
  clearCalls = 0;

  listThreads(): LocalThreadEndpointRecord[] {
    return this.runRequests.map((id, index) => ({
      id,
      name: null,
      agentId: "sre-agent",
      organizationId: "",
      createdById: "",
      archived: false,
      createdAt: new Date(index).toISOString(),
      updatedAt: new Date(index).toISOString(),
    }));
  }

  getThreadMessages() {
    return [];
  }

  getThreadEvents() {
    return [];
  }

  getThreadState() {
    return null;
  }

  clearThreads(): void {
    this.clearCalls += 1;
    this.runRequests.length = 0;
  }
}

function runRequest(threadId: string): AgentRunnerRunRequest {
  return {
    threadId,
    agent: new HttpAgent({ url: "http://127.0.0.1/unused" }),
    input: inputFor(threadId),
  };
}

async function eventually(check: () => boolean, message: string, timeoutMs = 3_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.fail(message);
}

test("同一 thread 共享底层 connect，并在上游完成后清理", () => {
  const delegate = new FakeAgentRunner();
  const runner = new SharedConnectAgentRunner(delegate);
  const firstEvents: BaseEvent[] = [];
  const secondEvents: BaseEvent[] = [];

  const first = runner.connect({ threadId: "shared" }).subscribe((event) => firstEvents.push(event));
  const second = runner.connect({ threadId: "shared" }).subscribe((event) => secondEvents.push(event));
  assert.equal(delegate.connectCalls, 1);
  assert.deepEqual(runner.activity(), {
    activeBrowserStreams: 2,
    activeUpstreamRuns: 0,
    sharedConnects: 1,
  });

  delegate.connectStreamAt(0).next(startedEvent("shared"));
  assert.equal(firstEvents.length, 1);
  assert.equal(secondEvents.length, 1);

  first.unsubscribe();
  second.unsubscribe();
  assert.equal(runner.activity().activeBrowserStreams, 0);
  assert.equal(runner.activity().sharedConnects, 1, "浏览器断开不能拆掉后台 connect");

  delegate.connectStreamAt(0).complete();
  assert.equal(runner.activity().sharedConnects, 0);
  runner.connect({ threadId: "shared" });
  assert.equal(delegate.connectCalls, 2, "完成后的新 connect 必须建立新桥");
  delegate.connectStreamAt(1).complete();
});

test("共享 connect 按用户身份隔离，同一用户仍只建立一个底层连接", () => {
  const delegate = new FakeAgentRunner();
  const runner = new SharedConnectAgentRunner(delegate);
  const firstUserEvents: BaseEvent[] = [];
  const secondUserEvents: BaseEvent[] = [];

  const first = runWithIdentity(USER_A, () =>
    runner.connect({ threadId: "same-public-thread" }).subscribe((event) => {
      firstUserEvents.push(event);
    }),
  );
  const duplicate = runWithIdentity(USER_A, () =>
    runner.connect({ threadId: "same-public-thread" }).subscribe((event) => {
      firstUserEvents.push(event);
    }),
  );
  const otherOwner = runWithIdentity(USER_B, () =>
    runner.connect({ threadId: "same-public-thread" }).subscribe((event) => {
      secondUserEvents.push(event);
    }),
  );

  assert.equal(delegate.connectCalls, 2, "同一用户去重，不同用户必须各自建立连接");
  assert.notEqual(delegate.connectRequests[0], delegate.connectRequests[1]);
  assert.ok(
    delegate.connectRequests.every(
      (threadId) => !threadId.includes("runtime-user-a") && !threadId.includes("runtime-user-b"),
    ),
    "底层隔离键不能泄露用户标识",
  );
  assert.equal(runner.activity().sharedConnects, 2);

  delegate.connectStreamAt(0).next(startedEvent("same-public-thread", "owner-a"));
  assert.equal(firstUserEvents.length, 2);
  assert.equal(secondUserEvents.length, 0, "另一用户不能收到首个用户的事件");

  delegate.connectStreamAt(1).next(startedEvent("same-public-thread", "owner-b"));
  assert.equal(firstUserEvents.length, 2);
  assert.equal(secondUserEvents.length, 1);

  first.unsubscribe();
  duplicate.unsubscribe();
  otherOwner.unsubscribe();
  assert.equal(runner.activity().activeBrowserStreams, 0);
  assert.equal(runner.activity().sharedConnects, 2, "普通断连不能关闭任一后台连接");

  delegate.connectStreamAt(0).complete();
  delegate.connectStreamAt(1).complete();
  assert.equal(runner.activity().sharedConnects, 0);
});

test("真实 IAM cookie 优先于固定 mock 头隔离同名 thread", () => {
  const delegate = new FakeAgentRunner();
  const runner = new SharedConnectAgentRunner(delegate);
  const publicThreadId = "same-iam-thread";
  const fixedMockUser = "frontend-demo-user";
  const firstOwnerEvents: BaseEvent[] = [];
  const secondOwnerEvents: BaseEvent[] = [];

  const first = runWithIdentity(
    { "x-openops-mock-user": fixedMockUser, cookie: "iam_session=owner-a" },
    () =>
      runner.connect({ threadId: publicThreadId }).subscribe((event) => {
        firstOwnerEvents.push(event);
      }),
  );
  const second = runWithIdentity(
    { "x-openops-mock-user": fixedMockUser, cookie: "iam_session=owner-b" },
    () =>
      runner.connect({ threadId: publicThreadId }).subscribe((event) => {
        secondOwnerEvents.push(event);
      }),
  );

  assert.equal(delegate.connectCalls, 2, "不同 IAM cookie 不能因固定 mock 头而共享连接");
  assert.notEqual(delegate.connectRequests[0], delegate.connectRequests[1]);
  assert.ok(
    delegate.connectRequests.every(
      (threadId) =>
        !threadId.includes(fixedMockUser) &&
        !threadId.includes("owner-a") &&
        !threadId.includes("owner-b"),
    ),
    "底层隔离键不能泄露 mock 用户或 cookie",
  );

  delegate.connectStreamAt(0).next(startedEvent(publicThreadId, "iam-owner-a"));
  assert.equal(firstOwnerEvents.length, 1);
  assert.equal(secondOwnerEvents.length, 0, "第二个 IAM 用户不能收到第一个用户的事件");

  delegate.connectStreamAt(1).next(startedEvent(publicThreadId, "iam-owner-b"));
  assert.equal(firstOwnerEvents.length, 1);
  assert.equal(secondOwnerEvents.length, 1);

  first.unsubscribe();
  second.unsubscribe();
  delegate.connectStreamAt(0).complete();
  delegate.connectStreamAt(1).complete();
  assert.deepEqual(runner.activity(), {
    activeBrowserStreams: 0,
    activeUpstreamRuns: 0,
    sharedConnects: 0,
  });
});

test("共享包装器保留 InMemoryAgentRunner 的本地 thread endpoints", () => {
  const runner = new SharedConnectAgentRunner(new InMemoryAgentRunner());
  assert.equal(supportsLocalThreadEndpoints(runner), true);
  if (!supportsLocalThreadEndpoints(runner)) assert.fail("runner capability was lost");
  assert.deepEqual(runner.listThreads(), []);
  assert.deepEqual(runner.getThreadMessages("missing-thread"), []);
  assert.deepEqual(runner.getThreadEvents("missing-thread"), []);
  assert.equal(runner.getThreadState("missing-thread"), null);
});

test("全局 clear 被拒绝且不会跨用户清空本地历史", () => {
  const delegate = new FakeLocalAgentRunner();
  const runner = new SharedConnectAgentRunner(delegate);

  const first = runWithIdentity(USER_A, () =>
    runner.run(runRequest("owner-a-history")).subscribe(),
  );
  const second = runWithIdentity(USER_B, () =>
    runner.run(runRequest("owner-b-history")).subscribe(),
  );
  first.unsubscribe();
  second.unsubscribe();

  assert.deepEqual(
    runWithIdentity(USER_A, () => runner.listThreads().map((thread) => thread.id)),
    ["owner-a-history"],
  );
  assert.deepEqual(
    runWithIdentity(USER_B, () => runner.listThreads().map((thread) => thread.id)),
    ["owner-b-history"],
  );

  assert.throws(
    () => runWithIdentity(USER_A, () => runner.clearThreads()),
    /Global thread clearing is disabled/,
  );
  assert.equal(delegate.clearCalls, 0, "不能调用底层的进程级 clearThreads");
  assert.deepEqual(
    runWithIdentity(USER_A, () => runner.listThreads().map((thread) => thread.id)),
    ["owner-a-history"],
  );
  assert.deepEqual(
    runWithIdentity(USER_B, () => runner.listThreads().map((thread) => thread.id)),
    ["owner-b-history"],
  );

  for (const stream of delegate.runStreams.values()) stream.complete();
  assert.equal(runner.activity().activeUpstreamRuns, 0);
});

test("普通断连保持后台 Run，显式 stop 才调用 delegate.stop", async () => {
  const delegate = new FakeAgentRunner();
  const runner = new SharedConnectAgentRunner(delegate);

  const browser = runner.run(runRequest("background")).subscribe();
  assert.equal(runner.activity().activeUpstreamRuns, 1);
  assert.equal(runner.activity().activeBrowserStreams, 1);

  browser.unsubscribe();
  assert.equal(runner.activity().activeBrowserStreams, 0);
  assert.equal(runner.activity().activeUpstreamRuns, 1);
  assert.equal(delegate.stopCalls, 0);

  const stopped = await runner.stop({ threadId: "background" });
  assert.equal(stopped, true);
  assert.equal(delegate.stopCalls, 1);
  assert.equal(runner.activity().activeUpstreamRuns, 0);
});

test("显式 stop 和运行状态按用户身份隔离", async () => {
  const delegate = new FakeAgentRunner();
  const runner = new SharedConnectAgentRunner(delegate);

  const browser = runWithIdentity(USER_A, () =>
    runner.run(runRequest("owned-thread")).subscribe(),
  );
  browser.unsubscribe();

  assert.equal(
    await runWithIdentity(USER_A, () => runner.isRunning({ threadId: "owned-thread" })),
    true,
  );
  assert.equal(
    await runWithIdentity(USER_B, () => runner.isRunning({ threadId: "owned-thread" })),
    false,
  );

  const unauthorized = await runWithIdentity(USER_B, () =>
    runner.stop({ threadId: "owned-thread" }),
  );
  assert.equal(unauthorized, false, "另一用户不能停止该 Run");
  assert.equal(runner.activity().activeUpstreamRuns, 1);

  const authorized = await runWithIdentity(USER_A, () =>
    runner.stop({ threadId: "owned-thread" }),
  );
  assert.equal(authorized, true);
  assert.equal(runner.activity().activeUpstreamRuns, 0);
  assert.notEqual(delegate.stopRequests[0], delegate.stopRequests[1]);
});

test("官方 Express bridge 将 20 个客户端 abort 收敛到零且 connect 去重", async (t) => {
  const delegate = new FakeAgentRunner();
  const runner = new SharedConnectAgentRunner(delegate);
  const agentId = "sre-agent";
  let capturedIdentity: Record<string, string> = {};
  const runtime = new CopilotRuntime({
    agents: () => {
      capturedIdentity = identityHeaders();
      return {
        [agentId]: new HttpAgent({ url: "http://127.0.0.1/unused" }),
      };
    },
    runner,
  });
  const app = createCopilotRuntimeApp({
    runtime,
    runner,
    backendBase: "http://127.0.0.1:1",
    agentId,
    log: () => undefined,
  });
  const server = app.listen(0, "127.0.0.1");
  await new Promise<void>((resolve, reject) => {
    server.once("listening", resolve);
    server.once("error", reject);
  });
  t.after(async () => {
    server.closeAllConnections?.();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  const { port } = server.address() as AddressInfo;
  const base = `http://127.0.0.1:${port}`;
  const threadId = "abort-storm";
  const runtimeUser = { "x-openops-mock-user": "runtime-test-user" };
  const background = runWithIdentity(runtimeUser, () =>
    runner.run(runRequest(threadId)).subscribe(),
  );
  background.unsubscribe();
  assert.equal(runner.activity().activeUpstreamRuns, 1);

  const controllers = Array.from({ length: 20 }, () => new AbortController());
  const requests = controllers.map((controller, index) =>
    fetch(`${base}/api/copilotkit`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-openops-mock-user": "runtime-test-user",
      },
      body: JSON.stringify({
        method: "agent/connect",
        params: { agentId },
        body: inputFor(threadId, `connect-${index}`),
      }),
      signal: controller.signal,
    }),
  );

  await eventually(
    () => runner.activity().activeBrowserStreams === 20,
    "20 个浏览器流没有全部建立",
  );
  assert.equal(delegate.connectCalls, 1);
  assert.equal(capturedIdentity["x-openops-mock-user"], "runtime-test-user");
  delegate.connectStreamAt(0).next(startedEvent(threadId));
  const responses = await Promise.all(requests);
  assert.ok(responses.every((response) => response.status === 200));

  const activeHealth = (await fetch(`${base}/healthz`).then((response) => response.json())) as {
    activity: Record<string, number>;
    resources: {
      heapUsedBytes: number;
      rssBytes: number;
      openFileDescriptors: number | null;
      eventLoopLagMeanMs: number;
      eventLoopLagMaxMs: number;
    };
  };
  assert.equal(activeHealth.activity.activeHttpRequests, 20);
  assert.equal(activeHealth.activity.activeBrowserStreams, 20);
  assert.equal(activeHealth.activity.sharedConnects, 1);
  assert.ok(activeHealth.resources.heapUsedBytes > 0);
  assert.ok(activeHealth.resources.rssBytes >= activeHealth.resources.heapUsedBytes);
  assert.ok(activeHealth.resources.eventLoopLagMeanMs >= 0);
  assert.ok(activeHealth.resources.eventLoopLagMaxMs >= 0);
  if (process.platform === "linux") {
    assert.ok((activeHealth.resources.openFileDescriptors ?? 0) > 0);
  } else {
    assert.equal(activeHealth.resources.openFileDescriptors, null);
  }

  controllers.forEach((controller) => controller.abort());
  await eventually(
    () => runner.activity().activeBrowserStreams === 0,
    "客户端 abort 没有传播到 CopilotKit 订阅",
  );
  const settledHealth = (await fetch(`${base}/healthz`).then((response) => response.json())) as {
    activity: Record<string, number>;
  };
  assert.equal(settledHealth.activity.activeHttpRequests, 0);
  assert.equal(settledHealth.activity.activeBrowserStreams, 0);
  assert.equal(delegate.stopCalls, 0, "普通断连不得隐式 stop");
  assert.equal(runner.activity().sharedConnects, 1);

  const otherOwnerController = new AbortController();
  const otherOwnerRequest = fetch(`${base}/api/copilotkit`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-openops-mock-user": "another-runtime-user",
    },
    body: JSON.stringify({
      method: "agent/connect",
      params: { agentId },
      body: inputFor(threadId, "other-owner-connect"),
    }),
    signal: otherOwnerController.signal,
  });
  await eventually(
    () => runner.activity().activeBrowserStreams === 1,
    "另一用户的连接没有建立",
  );
  assert.equal(delegate.connectCalls, 2, "HTTP 身份上下文必须隔离同名 thread");
  assert.equal(runner.activity().sharedConnects, 2);
  delegate.connectStreamAt(1).next(startedEvent(threadId, "other-owner"));
  assert.equal((await otherOwnerRequest).status, 200);
  otherOwnerController.abort();
  await eventually(
    () => runner.activity().activeBrowserStreams === 0,
    "另一用户的 abort 没有释放浏览器流",
  );

  const unauthorizedStop = await fetch(`${base}/api/copilotkit`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-openops-mock-user": "another-runtime-user",
    },
    body: JSON.stringify({
      method: "agent/stop",
      params: { agentId, threadId },
    }),
  });
  assert.equal(unauthorizedStop.status, 200);
  assert.deepEqual(await unauthorizedStop.json(), {
    stopped: false,
    message: `No active run for thread '${threadId}'.`,
  });
  assert.equal(runner.activity().activeUpstreamRuns, 1, "跨用户 stop 不能终止后台任务");

  const stopResponse = await fetch(`${base}/api/copilotkit`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-openops-mock-user": "runtime-test-user",
    },
    body: JSON.stringify({
      method: "agent/stop",
      params: { agentId, threadId },
    }),
  });
  assert.equal(stopResponse.status, 200);
  assert.deepEqual(await stopResponse.json(), {
    stopped: true,
    interrupt: {
      type: EventType.RUN_ERROR,
      message: "Run stopped by user",
      code: "STOPPED",
    },
  });
  assert.equal(delegate.stopCalls, 2, "跨用户与授权 stop 都按各自身份作用域透传");
  assert.equal(runner.activity().activeUpstreamRuns, 0);

  delegate.connectStreamAt(0).complete();
  delegate.connectStreamAt(1).complete();
  assert.equal(runner.activity().sharedConnects, 0);
});
