/** 单次飞行缓存：并发读取共享同一底层请求，调用方取消只取消自己的等待。 */

export interface RequestOptions {
  signal?: AbortSignal;
}

function abortReason(signal: AbortSignal): Error {
  return signal.reason instanceof Error
    ? signal.reason
    : new DOMException("The operation was aborted", "AbortError");
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException
    ? error.name === "AbortError"
    : Boolean(error && typeof error === "object" && "name" in error && error.name === "AbortError");
}

/**
 * 让每个调用方独立响应 AbortSignal，而不把共享底层请求一起取消。
 * 底层请求只在缓存显式失效时由 SingleFlightCache 中止。
 */
export function waitWithSignal<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(abortReason(signal));

  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", onAbort);
      callback();
    };
    const onAbort = () => finish(() => reject(abortReason(signal)));
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => finish(() => resolve(value)),
      (error) => finish(() => reject(error)),
    );
  });
}

export class SingleFlightCache<T> {
  private generation = 0;
  private cached: { generation: number; value: T } | null = null;
  private active: {
    generation: number;
    controller: AbortController;
    promise: Promise<T>;
  } | null = null;

  constructor(private readonly load: (signal: AbortSignal) => Promise<T>) {}

  get(options: RequestOptions = {}): Promise<T> {
    if (options.signal?.aborted) {
      return Promise.reject(abortReason(options.signal));
    }
    if (this.cached?.generation === this.generation) {
      return waitWithSignal(Promise.resolve(this.cached.value), options.signal);
    }

    if (!this.active || this.active.generation !== this.generation) {
      const generation = this.generation;
      const controller = new AbortController();
      const entry: NonNullable<typeof this.active> = {
        generation,
        controller,
        promise: null as unknown as Promise<T>,
      };
      entry.promise = this.load(controller.signal).then((value) => {
        if (this.generation === generation && !controller.signal.aborted) {
          this.cached = { generation, value };
        }
        return value;
      }).finally(() => {
        if (this.active === entry) this.active = null;
      });
      this.active = entry;
    }

    return waitWithSignal(this.active.promise, options.signal);
  }

  invalidate(): void {
    this.generation += 1;
    this.cached = null;
    this.active?.controller.abort();
    this.active = null;
  }

  peek(): T | undefined {
    return this.cached?.generation === this.generation ? this.cached.value : undefined;
  }
}

/** 按实体 key 隔离的 single-flight 缓存（例如每个 Agent 的可执行 Skill）。 */
export class KeyedSingleFlightCache<K, V> {
  private readonly entries = new Map<K, SingleFlightCache<V>>();

  get(
    key: K,
    load: (signal: AbortSignal) => Promise<V>,
    options: RequestOptions = {},
  ): Promise<V> {
    let entry = this.entries.get(key);
    if (!entry) {
      entry = new SingleFlightCache(load);
      this.entries.set(key, entry);
    }
    return entry.get(options);
  }

  invalidate(key?: K): void {
    if (key !== undefined) {
      this.entries.get(key)?.invalidate();
      this.entries.delete(key);
      return;
    }
    for (const entry of this.entries.values()) entry.invalidate();
    this.entries.clear();
  }
}
