import { EventType, type BaseEvent, type Message } from "@ag-ui/client";
import {
  AgentRunner,
  type AgentRunnerConnectRequest,
  type AgentRunnerIsRunningRequest,
  type AgentRunnerRunRequest,
  type AgentRunnerStopRequest,
  type LocalThreadEndpointRecord,
} from "@copilotkit/runtime/v2";
import assert from "node:assert/strict";
import test from "node:test";
import { Observable, Subject, of } from "rxjs";
import { runWithIdentity } from "./identity";
import {
  createBackendTranscriptLoader,
  PersistentConnectAgentRunner,
  TranscriptLoadError,
  type TranscriptLoader,
  type TranscriptMessage,
} from "./persistent-runner";
import { SharedConnectAgentRunner, type LifecycleEvent } from "./shared-runner";

const USER_A = { "x-openops-mock-user": "persistent-user-a", cookie: "session=owner-a" };
const USER_B = { "x-openops-mock-user": "persistent-user-b", cookie: "session=owner-b" };

const HISTORY: TranscriptMessage[] = [
  { id: "msg-user", role: "user", content: "仅属于 A 的问题" },
  { id: "msg-assistant", role: "assistant", content: "仅属于 A 的回答" },
] as TranscriptMessage[];

function started(threadId: string): BaseEvent {
  return {
    type: EventType.RUN_STARTED,
    threadId,
    runId: `run-${threadId}`,
  } as BaseEvent;
}

class ControlledLocalRunner extends AgentRunner {
  readonly ɵsupportsLocalThreadEndpoints = true as const;
  memoryAvailable = false;
  running = false;
  connectCalls = 0;
  runStream = new Subject<BaseEvent>();

  run(_request: AgentRunnerRunRequest): Observable<BaseEvent> {
    this.running = true;
    return this.runStream.asObservable();
  }

  connect(request: AgentRunnerConnectRequest): Observable<BaseEvent> {
    this.connectCalls += 1;
    return of(started(request.threadId));
  }

  isRunning(_request: AgentRunnerIsRunningRequest): Promise<boolean> {
    return Promise.resolve(this.running);
  }

  stop(_request: AgentRunnerStopRequest): Promise<boolean> {
    this.running = false;
    this.runStream.complete();
    return Promise.resolve(true);
  }

  listThreads(): LocalThreadEndpointRecord[] {
    return [];
  }

  getThreadMessages(_threadId: string): Message[] {
    return this.memoryAvailable || this.running ? HISTORY : [];
  }

  getThreadEvents(threadId: string): BaseEvent[] {
    return this.memoryAvailable || this.running ? [started(threadId)] : [];
  }

  getThreadState(): Record<string, unknown> | null {
    return null;
  }

  clearThreads(): void {
    this.memoryAvailable = false;
    this.running = false;
  }
}

function buildRunner(
  loader: TranscriptLoader,
  events: LifecycleEvent[] = [],
  local = new ControlledLocalRunner(),
) {
  const shared = new SharedConnectAgentRunner(local, (event) => events.push(event));
  return {
    local,
    runner: new PersistentConnectAgentRunner(shared, loader, (event) => events.push(event)),
  };
}

function collect(source: Observable<BaseEvent>): Promise<BaseEvent[]> {
  return new Promise((resolve, reject) => {
    const events: BaseEvent[] = [];
    source.subscribe({
      next: (event) => events.push(event),
      error: reject,
      complete: () => resolve(events),
    });
  });
}

function snapshotMessages(events: BaseEvent[]): TranscriptMessage[] {
  return (events.find((event) => event.type === EventType.MESSAGES_SNAPSHOT) as
    | { messages: TranscriptMessage[] }
    | undefined)?.messages ?? [];
}

test("memory hit 使用原生 runner 回放，不访问后端", async () => {
  let loads = 0;
  const { local, runner } = buildRunner(async () => {
    loads += 1;
    return HISTORY;
  });
  local.memoryAvailable = true;

  const events = await runWithIdentity(USER_A, () => collect(runner.connect({ threadId: "memory" })));
  assert.equal(loads, 0);
  assert.equal(local.connectCalls, 1);
  assert.deepEqual(events.map((event) => event.type), [EventType.RUN_STARTED]);
});

test("memory miss 合成完整快照，空新会话也有完整 run 边界", async () => {
  const restored = buildRunner(async () => HISTORY).runner;
  const restoredEvents = await runWithIdentity(USER_A, () =>
    collect(restored.connect({ threadId: "restored" })),
  );
  assert.deepEqual(restoredEvents.map((event) => event.type), [
    EventType.RUN_STARTED,
    EventType.MESSAGES_SNAPSHOT,
    EventType.RUN_FINISHED,
  ]);
  assert.deepEqual(snapshotMessages(restoredEvents), HISTORY);

  const empty = buildRunner(async () => []).runner;
  const emptyEvents = await runWithIdentity(USER_A, () =>
    collect(empty.connect({ threadId: "brand-new" })),
  );
  assert.deepEqual(snapshotMessages(emptyEvents), []);
  assert.equal(emptyEvents.at(-1)?.type, EventType.RUN_FINISHED);
});

test("同名 thread 的持久恢复按身份隔离", async () => {
  const seenIdentities: string[] = [];
  const { runner } = buildRunner(async ({ identity }) => {
    seenIdentities.push(identity.cookie ?? "");
    const owner = identity.cookie?.endsWith("owner-a") ? "A" : "B";
    return [{ id: `msg-${owner}`, role: "user", content: `history-${owner}` }] as TranscriptMessage[];
  });

  const [eventsA, eventsB] = await Promise.all([
    runWithIdentity(USER_A, () => collect(runner.connect({ threadId: "same-thread" }))),
    runWithIdentity(USER_B, () => collect(runner.connect({ threadId: "same-thread" }))),
  ]);
  assert.deepEqual(new Set(seenIdentities), new Set(["session=owner-a", "session=owner-b"]));
  assert.equal(snapshotMessages(eventsA)[0]?.content, "history-A");
  assert.equal(snapshotMessages(eventsB)[0]?.content, "history-B");
});

test("浏览器取消 connect 会同步中止 transcript 请求", async () => {
  let signal: AbortSignal | undefined;
  let rejectLoad: ((error: Error) => void) | undefined;
  const loader: TranscriptLoader = ({ signal: nextSignal }) => {
    signal = nextSignal;
    return new Promise((_resolve, reject) => {
      rejectLoad = reject;
      nextSignal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
    });
  };
  const { runner } = buildRunner(loader);
  const subscription = runWithIdentity(USER_A, () =>
    runner.connect({ threadId: "cancelled-load" }).subscribe(),
  );
  await new Promise((resolve) => setImmediate(resolve));
  subscription.unsubscribe();
  assert.equal(signal?.aborted, true);
  rejectLoad?.(new Error("cleanup"));
});

test("加载期间新 run 启动时丢弃旧快照并切回 memory", async () => {
  let resolveLoad: ((messages: TranscriptMessage[]) => void) | undefined;
  const loader: TranscriptLoader = () =>
    new Promise((resolve) => {
      resolveLoad = resolve;
    });
  const { local, runner } = buildRunner(loader);
  const eventsPromise = runWithIdentity(USER_A, () =>
    collect(runner.connect({ threadId: "racing-thread" })),
  );
  await new Promise((resolve) => setImmediate(resolve));
  local.running = true;
  resolveLoad?.(HISTORY);

  const events = await eventsPromise;
  assert.equal(local.connectCalls, 1);
  assert.equal(events.some((event) => event.type === EventType.MESSAGES_SNAPSHOT), false);
  assert.deepEqual(events.map((event) => event.type), [EventType.RUN_STARTED]);
});

test("后端失败与非法响应让 connect 失败，日志保持脱敏", async () => {
  const lifecycle: LifecycleEvent[] = [];
  const { runner } = buildRunner(
    async () => {
      throw new TranscriptLoadError("HTTP_403");
    },
    lifecycle,
  );
  await assert.rejects(
    runWithIdentity(USER_A, () => collect(runner.connect({ threadId: "private-run-id" }))),
    /could not be restored/i,
  );
  const serialized = JSON.stringify(lifecycle);
  assert.equal(serialized.includes("persistent-user-a"), false);
  assert.equal(serialized.includes("session=owner-a"), false);
  assert.equal(serialized.includes("private-run-id"), false);
  assert.equal(serialized.includes("HTTP_403"), true);

  const invalidLoader = createBackendTranscriptLoader("http://backend", async () =>
    new Response(JSON.stringify({ data: [{ role: "user", content: "missing id" }] }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  await assert.rejects(
    invalidLoader({ threadId: "run", identity: USER_A, signal: new AbortController().signal }),
    (error: unknown) => error instanceof TranscriptLoadError && error.code === "INVALID_RESPONSE",
  );
});

test("backend transcript adapter 透传当前身份并拒绝 HTTP 权限错误", async () => {
  let requestedUrl = "";
  let requestedHeaders = new Headers();
  const loader = createBackendTranscriptLoader("http://backend/", async (input, init) => {
    requestedUrl = String(input);
    requestedHeaders = new Headers(init?.headers);
    return new Response(JSON.stringify({ data: HISTORY }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  assert.deepEqual(
    await loader({
      threadId: "run/with space",
      identity: USER_A,
      signal: new AbortController().signal,
    }),
    HISTORY,
  );
  assert.equal(requestedUrl, "http://backend/api/openops/v1/agent-runs/run%2Fwith%20space/messages");
  assert.equal(requestedHeaders.get("cookie"), USER_A.cookie);
  assert.equal(requestedHeaders.get("x-openops-mock-user"), USER_A["x-openops-mock-user"]);

  const forbidden = createBackendTranscriptLoader("http://backend", async () =>
    new Response(JSON.stringify({ error: { code: "FORBIDDEN" } }), { status: 403 }),
  );
  await assert.rejects(
    forbidden({ threadId: "run", identity: USER_A, signal: new AbortController().signal }),
    (error: unknown) => error instanceof TranscriptLoadError && error.code === "HTTP_403",
  );
});

test("sidecar 重建后可再次从持久 AgentState transcript 恢复稳定消息", async () => {
  const loader: TranscriptLoader = async () => HISTORY;
  const first = await runWithIdentity(USER_A, () =>
    collect(buildRunner(loader).runner.connect({ threadId: "restart" })),
  );
  const rebuilt = await runWithIdentity(USER_A, () =>
    collect(buildRunner(loader).runner.connect({ threadId: "restart" })),
  );
  assert.deepEqual(snapshotMessages(first), HISTORY);
  assert.deepEqual(snapshotMessages(rebuilt), HISTORY);
});
