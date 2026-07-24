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
  compactReplaySegment,
  createBackendTranscriptLoader,
  PersistentConnectAgentRunner,
  REPLAY_CAUGHT_UP_EVENT,
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
  // 回放段之后固定跟一条追平信号(oa.replay.caught_up),前端据此熄灭「正在同步对话」
  assert.deepEqual(events.map((event) => event.type), [EventType.RUN_STARTED, EventType.CUSTOM]);
});

test("memory miss 合成完整快照，空新会话也有完整 run 边界", async () => {
  const restored = buildRunner(async () => HISTORY).runner;
  const restoredEvents = await runWithIdentity(USER_A, () =>
    collect(restored.connect({ threadId: "restored" })),
  );
  assert.deepEqual(restoredEvents.map((event) => event.type), [
    EventType.RUN_STARTED,
    EventType.MESSAGES_SNAPSHOT,
    EventType.CUSTOM, // 追平信号:快照路径同样熄灭「正在同步对话」
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
  assert.deepEqual(events.map((event) => event.type), [EventType.RUN_STARTED, EventType.CUSTOM]);
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

// ---------- 回放段「秒到」:合并 + 追平信号 ----------

function textStart(messageId: string): BaseEvent {
  return { type: EventType.TEXT_MESSAGE_START, messageId, role: "assistant" } as BaseEvent;
}
function textDelta(messageId: string, delta: string): BaseEvent {
  return { type: EventType.TEXT_MESSAGE_CONTENT, messageId, delta } as BaseEvent;
}
function textEnd(messageId: string): BaseEvent {
  return { type: EventType.TEXT_MESSAGE_END, messageId } as BaseEvent;
}
function argsDelta(toolCallId: string, delta: string): BaseEvent {
  return { type: EventType.TOOL_CALL_ARGS, toolCallId, delta } as BaseEvent;
}

/** 可控回放/实时两段流的本地 runner:replay 在订阅同一 tick 同步交付(ReplaySubject 语义),live 经 Subject 后续推。 */
class StreamingLocalRunner extends ControlledLocalRunner {
  replay: BaseEvent[] = [];
  live = new Subject<BaseEvent>();
  /** 模拟已完成 run 的整段回放(InMemoryAgentRunner historic 路径):同步发完即 complete。 */
  completeAfterReplay = false;

  connect(_request: AgentRunnerConnectRequest): Observable<BaseEvent> {
    this.connectCalls += 1;
    return new Observable<BaseEvent>((subscriber) => {
      for (const event of this.replay) subscriber.next(event);
      if (this.completeAfterReplay) {
        subscriber.complete();
        return undefined;
      }
      const liveSubscription = this.live.subscribe(subscriber);
      return () => liveSubscription.unsubscribe();
    });
  }
}

function finished(threadId: string): BaseEvent {
  return {
    type: EventType.RUN_FINISHED,
    threadId,
    runId: `run-${threadId}`,
    outcome: { type: "success" },
  } as BaseEvent;
}

test("compactReplaySegment:连续同源增量合并成整条,结构事件原序保留", () => {
  const merged = compactReplaySegment([
    textStart("m1"),
    textDelta("m1", "流"), textDelta("m1", "式"), textDelta("m1", "回放"),
    textEnd("m1"),
    argsDelta("t1", '{"appid":'), argsDelta("t1", '"APP-A"}'),
    textStart("m2"),
    textDelta("m2", "第二条"),
  ]);
  assert.deepEqual(
    merged.map((event) => [event.type, (event as unknown as { delta?: string }).delta ?? null]),
    [
      [EventType.TEXT_MESSAGE_START, null],
      [EventType.TEXT_MESSAGE_CONTENT, "流式回放"],
      [EventType.TEXT_MESSAGE_END, null],
      [EventType.TOOL_CALL_ARGS, '{"appid":"APP-A"}'],
      [EventType.TEXT_MESSAGE_START, null],
      [EventType.TEXT_MESSAGE_CONTENT, "第二条"],
    ],
  );

  // 不连续(被其他消息隔开)不跨段合并——保序优先
  const interleaved = compactReplaySegment([
    textDelta("m1", "a"), textDelta("m2", "x"), textDelta("m1", "b"),
  ]);
  assert.equal(interleaved.length, 3);
});

test("进行中 run 回放段合并秒到,追平信号在回放/实时交界,live 段原样透传", async () => {
  const local = new StreamingLocalRunner();
  local.running = true; // HIT:任务仍在跑
  local.replay = [
    started("live-run"),
    textStart("m1"),
    textDelta("m1", "正在"), textDelta("m1", "分析"), textDelta("m1", "证据"),
    textEnd("m1"),
    textStart("m2"),
    textDelta("m2", "初步"), textDelta("m2", "假设"),
  ];
  const { runner } = buildRunner(async () => [], [], local);

  const received: BaseEvent[] = [];
  const subscription = runWithIdentity(USER_A, () =>
    runner.connect({ threadId: "live-run" }).subscribe((event) => received.push(event)),
  );
  await new Promise((resolve) => setImmediate(resolve)); // 越过微任务边界,回放段已 flush

  const markerIndex = received.findIndex(
    (event) => event.type === EventType.CUSTOM
      && (event as unknown as { name?: string }).name === REPLAY_CAUGHT_UP_EVENT,
  );
  assert.ok(markerIndex > 0, "追平信号必须存在");
  assert.equal(
    (received[markerIndex] as unknown as { value?: { replayed?: number } }).value?.replayed,
    local.replay.length,
  );
  // 回放段:m1 的 3 条 delta 合并为 1 条整句;m2 同理;marker 是回放段最后一条
  const beforeMarker = received.slice(0, markerIndex);
  const deltas = beforeMarker
    .filter((event) => event.type === EventType.TEXT_MESSAGE_CONTENT)
    .map((event) => (event as unknown as { delta?: string }).delta);
  assert.deepEqual(deltas, ["正在分析证据", "初步假设"]);
  assert.equal(markerIndex, received.length - 1);

  // live 段:后续增量逐条原样透传(in-flight 消息不被封口,可继续追加)
  local.live.next(textDelta("m2", "待验证"));
  local.live.next(textDelta("m2", "。"));
  const afterMarker = received.slice(markerIndex + 1)
    .map((event) => (event as unknown as { delta?: string }).delta);
  assert.deepEqual(afterMarker, ["待验证", "。"]);
  subscription.unsubscribe();
});

test("回放段为空(上游首帧未到)不抢发信号——AG-UI 首事件必须是 RUN_STARTED,信号挂起到 live 首个 RUN_STARTED 之后", async () => {
  const local = new StreamingLocalRunner();
  local.running = true;
  const { runner } = buildRunner(async () => [], [], local);

  const received: BaseEvent[] = [];
  const subscription = runWithIdentity(USER_A, () =>
    runner.connect({ threadId: "fresh-live" }).subscribe((event) => received.push(event)),
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(received.length, 0); // 信号不得是首事件(verifyEvents 强校验)

  local.live.next(started("fresh-live"));
  assert.deepEqual(received.map((event) => event.type), [EventType.RUN_STARTED, EventType.CUSTOM]);
  assert.equal((received[1] as unknown as { value?: { replayed?: number } }).value?.replayed, 0);

  // 信号只补一次:后续事件原样透传
  local.live.next(textDelta("m1", "开始"));
  assert.equal(received.length, 3);
  subscription.unsubscribe();
});

test("已完成 run 的整段回放:追平信号插在末尾 RUN_FINISHED 之前(不得出现在终态之后)", async () => {
  const local = new StreamingLocalRunner();
  local.memoryAvailable = true;
  local.completeAfterReplay = true;
  local.replay = [
    started("done-run"),
    textStart("m1"),
    textDelta("m1", "已"), textDelta("m1", "闭环"),
    textEnd("m1"),
    finished("done-run"),
  ];
  const { runner } = buildRunner(async () => [], [], local);

  const events = await runWithIdentity(USER_A, () =>
    collect(runner.connect({ threadId: "done-run" })),
  );
  assert.deepEqual(events.map((event) => event.type), [
    EventType.RUN_STARTED,
    EventType.TEXT_MESSAGE_START,
    EventType.TEXT_MESSAGE_CONTENT,
    EventType.TEXT_MESSAGE_END,
    EventType.CUSTOM, // 信号在终态之前,verifyEvents 通过
    EventType.RUN_FINISHED,
  ]);
  assert.equal(
    (events[1 + 3] as unknown as { name?: string }).name,
    REPLAY_CAUGHT_UP_EVENT,
  );
  assert.equal(
    (events.find((event) => event.type === EventType.TEXT_MESSAGE_CONTENT) as unknown as { delta?: string }).delta,
    "已闭环",
  );
});

test("裸 RUN_ERROR 历史记录(首帧前失败)不劫持插入点:信号插在真正闭合 run 的终态之前", async () => {
  const local = new StreamingLocalRunner();
  local.memoryAvailable = true;
  local.completeAfterReplay = true;
  // run A 正常完成;run B 在上游首帧前失败,历史里只落下一条裸 RUN_ERROR
  local.replay = [
    started("mixed"),
    textStart("m1"), textDelta("m1", "结论"), textEnd("m1"),
    finished("mixed"),
    { type: EventType.RUN_ERROR, message: "INCOMPLETE_STREAM" } as BaseEvent,
  ];
  const { runner } = buildRunner(async () => [], [], local);

  const events = await runWithIdentity(USER_A, () =>
    collect(runner.connect({ threadId: "mixed" })),
  );
  // 信号必须在 RUN_FINISHED(闭合了开着的 run A)之前,而不是裸 RUN_ERROR 之前
  assert.deepEqual(events.map((event) => event.type), [
    EventType.RUN_STARTED,
    EventType.TEXT_MESSAGE_START,
    EventType.TEXT_MESSAGE_CONTENT,
    EventType.TEXT_MESSAGE_END,
    EventType.CUSTOM,
    EventType.RUN_FINISHED,
    EventType.RUN_ERROR,
  ]);
});
