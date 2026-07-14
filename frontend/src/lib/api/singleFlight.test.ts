import assert from "node:assert/strict";
import test from "node:test";

import { SingleFlightCache, isAbortError } from "./singleFlight";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

test("SingleFlightCache 并发读取只启动一个底层请求并缓存结果", async () => {
  const result = deferred<string[]>();
  let loads = 0;
  const cache = new SingleFlightCache<string[]>(() => {
    loads += 1;
    return result.promise;
  });

  const first = cache.get();
  const second = cache.get();
  assert.equal(loads, 1);

  result.resolve(["run-1"]);
  assert.deepEqual(await first, ["run-1"]);
  assert.deepEqual(await second, ["run-1"]);
  assert.deepEqual(await cache.get(), ["run-1"]);
  assert.equal(loads, 1);
});

test("调用方 AbortError 不会中止其他调用方共享的底层请求", async () => {
  const result = deferred<string>();
  const loadSignals: AbortSignal[] = [];
  const cache = new SingleFlightCache<string>((signal) => {
    loadSignals.push(signal);
    return result.promise;
  });
  const controller = new AbortController();

  const cancelled = cache.get({ signal: controller.signal });
  const survivor = cache.get();
  controller.abort();

  await assert.rejects(cancelled, (error) => isAbortError(error));
  assert.equal(loadSignals[0]?.aborted, false);
  result.resolve("ok");
  assert.equal(await survivor, "ok");
});

test("显式失效会中止旧底层请求，下一次读取启动新 generation", async () => {
  const requests: Array<ReturnType<typeof deferred<string>>> = [];
  const signals: AbortSignal[] = [];
  const cache = new SingleFlightCache<string>((signal) => {
    const request = deferred<string>();
    requests.push(request);
    signals.push(signal);
    signal.addEventListener("abort", () => {
      request.reject(new DOMException("Aborted", "AbortError"));
    }, { once: true });
    return request.promise;
  });

  const staleResult = cache.get().catch((error) => error as Error);
  cache.invalidate();
  assert.equal(signals[0]?.aborted, true);
  assert.equal(isAbortError(await staleResult), true);

  const fresh = cache.get();
  assert.equal(requests.length, 2);
  requests[1]?.resolve("fresh");
  assert.equal(await fresh, "fresh");
  assert.equal(cache.peek(), "fresh");
});
