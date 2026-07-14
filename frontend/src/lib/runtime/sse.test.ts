import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { createServer } from "vite";

type State = "connecting" | "open" | "reconnecting";
type SubscribeSse = (
  url: string,
  opts: {
    onEvent: (data: unknown, id: number | null) => void;
    onResync?: () => void;
    onStateChange?: (state: State) => void;
  },
) => { close: () => void };

const flushMicrotasks = async () => {
  for (let index = 0; index < 5; index += 1) await Promise.resolve();
};

test("subscribeSse 关闭语义", async (suite) => {
  // 通过 Vite 加载模块，让 Node 测试获得与浏览器构建一致的 import.meta.env。
  const server = await createServer({
    root: fileURLToPath(new URL("../../../", import.meta.url)),
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  suite.after(() => server.close());
  const { subscribeSse } = await server.ssrLoadModule("/src/lib/runtime/sse.ts") as {
    subscribeSse: SubscribeSse;
  };

  await suite.test("close 会中止尚未返回的 fetch，且不会产生后续回调", async (t) => {
    const originalFetch = globalThis.fetch;
    t.after(() => { globalThis.fetch = originalFetch; });

    let calls = 0;
    const signalRef: { current?: AbortSignal } = {};
    const states: State[] = [];
    let eventCount = 0;
    globalThis.fetch = ((_input: RequestInfo | URL, init?: RequestInit) => {
      calls += 1;
      signalRef.current = init?.signal as AbortSignal;
      return new Promise<Response>((_resolve, reject) => {
        signalRef.current?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), {
          once: true,
        });
      });
    }) as typeof fetch;

    const handle = subscribeSse("/events", {
      onEvent: () => { eventCount += 1; },
      onStateChange: (state) => states.push(state),
    });
    assert.equal(calls, 1);
    assert.equal(signalRef.current?.aborted, false);

    handle.close();
    await flushMicrotasks();

    assert.equal(signalRef.current?.aborted, true);
    assert.equal(calls, 1);
    assert.equal(eventCount, 0);
    assert.deepEqual(states, ["connecting"]);
  });

  await suite.test("close 会取消活动 reader，并丢弃关闭后才完成的读取", async (t) => {
    const originalFetch = globalThis.fetch;
    t.after(() => { globalThis.fetch = originalFetch; });

    const signalRef: { current?: AbortSignal } = {};
    let cancelCount = 0;
    let releaseCount = 0;
    let finishRead: ((result: ReadableStreamReadResult<Uint8Array>) => void) | null = null;
    const states: State[] = [];
    const events: unknown[] = [];
    const reader = {
      read: () => new Promise<ReadableStreamReadResult<Uint8Array>>((resolve) => {
        finishRead = resolve;
      }),
      cancel: () => {
        cancelCount += 1;
        // 即使底层在 cancel 后交付最后一个 chunk，也不应再进入业务回调。
        finishRead?.({
          done: false,
          value: new TextEncoder().encode("id: 1\ndata: {\"late\":true}\n\n"),
        });
        return Promise.resolve();
      },
      releaseLock: () => { releaseCount += 1; },
    };
    const body = { getReader: () => reader } as unknown as ReadableStream<Uint8Array>;
    globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      signalRef.current = init?.signal as AbortSignal;
      return { ok: true, status: 200, body } as Response;
    }) as typeof fetch;

    const handle = subscribeSse("/events", {
      onEvent: (event) => events.push(event),
      onStateChange: (state) => states.push(state),
    });
    await flushMicrotasks();
    assert.deepEqual(states, ["connecting", "open"]);

    handle.close();
    await flushMicrotasks();

    assert.equal(signalRef.current?.aborted, true);
    assert.equal(cancelCount, 1);
    assert.equal(releaseCount, 1);
    assert.deepEqual(events, []);
  });

  await suite.test("close 会取消重连退避，过期计时器也不能启动新连接", async (t) => {
    const originalFetch = globalThis.fetch;
    const originalSetTimeout = globalThis.setTimeout;
    const originalClearTimeout = globalThis.clearTimeout;
    t.after(() => {
      globalThis.fetch = originalFetch;
      globalThis.setTimeout = originalSetTimeout;
      globalThis.clearTimeout = originalClearTimeout;
    });

    let calls = 0;
    const scheduledRef: { current?: () => void } = {};
    let timerCleared = false;
    const timerToken = { id: "retry" };
    globalThis.fetch = (async () => {
      calls += 1;
      throw new Error("network down");
    }) as typeof fetch;
    globalThis.setTimeout = ((callback: TimerHandler) => {
      scheduledRef.current = callback as () => void;
      return timerToken;
    }) as unknown as typeof setTimeout;
    globalThis.clearTimeout = ((handle: unknown) => {
      if (handle === timerToken) timerCleared = true;
    }) as typeof clearTimeout;

    const states: State[] = [];
    const handle = subscribeSse("/events", {
      onEvent: () => undefined,
      onStateChange: (state) => states.push(state),
    });
    await flushMicrotasks();
    assert.equal(calls, 1);
    assert.ok(scheduledRef.current);

    const expiredTimer = scheduledRef.current;
    handle.close();
    assert.equal(timerCleared, true);
    expiredTimer?.();
    await flushMicrotasks();

    assert.equal(calls, 1);
    assert.deepEqual(states, ["connecting"]);
  });
});
