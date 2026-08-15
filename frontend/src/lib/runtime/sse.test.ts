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
    onFatal?: (code: string, message: string) => void;
    initialLastEventId?: number | null;
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

  await suite.test("首次连接携带 state 快照提供的 Last-Event-ID", async (t) => {
    const originalFetch = globalThis.fetch;
    t.after(() => { globalThis.fetch = originalFetch; });

    let receivedHeader: string | null = null;
    let signal: AbortSignal | undefined;
    globalThis.fetch = ((_input: RequestInfo | URL, init?: RequestInit) => {
      receivedHeader = new Headers(init?.headers).get("Last-Event-ID");
      signal = init?.signal as AbortSignal;
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), {
          once: true,
        });
      });
    }) as typeof fetch;

    const handle = subscribeSse("/events", {
      initialLastEventId: 42,
      onEvent: () => undefined,
    });
    assert.equal(receivedHeader, "42");

    handle.close();
    await flushMicrotasks();
    assert.equal(signal?.aborted, true);
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

  // 401 是**不可重试**故障：改前它走 catch 的重连分支，退避封顶 8s ⇒ 一个未登录标签页
  // 永久每 8 秒打一次后端（内网 access 日志实测被这条刷满），用户还只看到一直转圈。
  await suite.test("401 带 login_url：跳登录页，不重连", async (t) => {
    const originalFetch = globalThis.fetch;
    const originalWindow = (globalThis as { window?: unknown }).window;
    t.after(() => {
      globalThis.fetch = originalFetch;
      (globalThis as { window?: unknown }).window = originalWindow;
    });

    let assigned: string | null = null;
    (globalThis as { window?: unknown }).window = {
      location: { host: "openops.example.com", assign: (u: string) => { assigned = u; } },
    };

    let calls = 0;
    globalThis.fetch = (async () => {
      calls += 1;
      return {
        ok: false,
        status: 401,
        json: async () => ({ error: { code: "IAM-001", login_url: "https://{host}/login" } }),
      } as unknown as Response;
    }) as typeof fetch;

    let fatal: string | null = null;
    const states: State[] = [];
    subscribeSse("/events", {
      onEvent: () => undefined,
      onStateChange: (state) => states.push(state),
      onFatal: (_code, message) => { fatal = message; },
    });
    await flushMicrotasks();

    assert.equal(calls, 1, "401 不得重试");
    assert.equal(assigned, "https://openops.example.com/login", "{host} 应替成当前域名");
    assert.equal(fatal, null, "已跳登录页就不再走 onFatal");
    assert.deepEqual(states, ["connecting"]);
  });

  await suite.test("401 无 login_url：onFatal 上报，不重连", async (t) => {
    const originalFetch = globalThis.fetch;
    t.after(() => { globalThis.fetch = originalFetch; });

    let calls = 0;
    globalThis.fetch = (async () => {
      calls += 1;
      return {
        ok: false,
        status: 401,
        json: async () => ({ error: { code: "IAM-001", message: "未登录" } }),
      } as unknown as Response;
    }) as typeof fetch;

    const fatals: Array<[string, string]> = [];
    subscribeSse("/events", {
      onEvent: () => undefined,
      onFatal: (code, message) => { fatals.push([code, message]); },
    });
    await flushMicrotasks();

    assert.equal(calls, 1, "401 不得重试");
    assert.deepEqual(fatals, [["IAM-001", "未登录"]]);
  });

  await suite.test("请求带 credentials:include（真 IAM cookie 通路）", async (t) => {
    const originalFetch = globalThis.fetch;
    t.after(() => { globalThis.fetch = originalFetch; });

    let credentials: RequestCredentials | undefined;
    globalThis.fetch = ((_input: RequestInfo | URL, init?: RequestInit) => {
      credentials = init?.credentials;
      return new Promise<Response>(() => undefined);
    }) as typeof fetch;

    const handle = subscribeSse("/events", { onEvent: () => undefined });
    assert.equal(credentials, "include");
    handle.close();
  });

  // 非 401 的失败仍必须重连——别把降噪做成"任何错误都不重试"。
  await suite.test("503 仍走退避重连", async (t) => {
    const originalFetch = globalThis.fetch;
    const originalSetTimeout = globalThis.setTimeout;
    t.after(() => {
      globalThis.fetch = originalFetch;
      globalThis.setTimeout = originalSetTimeout;
    });

    let calls = 0;
    globalThis.fetch = (async () => {
      calls += 1;
      return { ok: false, status: 503, json: async () => ({}) } as unknown as Response;
    }) as typeof fetch;
    const scheduledRef: { current?: () => void } = {};
    globalThis.setTimeout = ((callback: TimerHandler) => {
      scheduledRef.current = callback as () => void;
      return { id: "retry" };
    }) as unknown as typeof setTimeout;

    let fatal = false;
    const handle = subscribeSse("/events", {
      onEvent: () => undefined,
      onFatal: () => { fatal = true; },
    });
    await flushMicrotasks();
    assert.equal(calls, 1);
    assert.equal(fatal, false, "503 不是终止态");
    assert.ok(scheduledRef.current, "应排了重连退避");
    handle.close();
  });
});
