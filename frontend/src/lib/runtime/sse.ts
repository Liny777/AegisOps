/**
 * fetch 流式 SSE 客户端（参考 frontend-v2 EventSource 模式，但 mock 鉴权走请求头，
 * EventSource 不支持自定义头 → 用 fetch+ReadableStream 手解析；B8 真 IAM(Cookie) 后可换回 EventSource）。
 * 支持：Last-Event-ID 断线补发、自动重连（指数退避）、resync 信号回调。
 * 重连只针对**可恢复**故障：401 就地终止（见 connect() 内注释），不进退避循环。
 */
import { type ApiErrorBody, demoIdentity, redirectToLoginIfUnauthorized } from "../api/client";

export interface SseHandle {
  close: () => void;
}

export function subscribeSse(
  url: string,
  opts: {
    onEvent: (data: unknown, id: number | null) => void;
    onResync?: () => void;
    onStateChange?: (s: "connecting" | "open" | "reconnecting") => void;
    /**
     * 不可重试的终止（当前只有 401 未登录）。调用方据此提示用户，而不是让 UI 无限转圈。
     * 能跳登录页时不会触发——那种情况浏览器已经在离开本页了。
     */
    onFatal?: (code: string, message: string) => void;
    /** `/state.last_event_seq`：首次连接即从快照游标之后补发，避免为防竞态重复拉 state。 */
    initialLastEventId?: number | null;
  },
): SseHandle {
  let closed = false;
  let lastId: number | null = typeof opts.initialLastEventId === "number"
    && Number.isFinite(opts.initialLastEventId)
    ? opts.initialLastEventId
    : null;
  let retry = 0;
  let activeController: AbortController | null = null;
  let activeReader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  let cancelRetryWait: (() => void) | null = null;

  const waitForRetry = (delay: number): Promise<boolean> => {
    if (closed) return Promise.resolve(false);
    return new Promise((resolve) => {
      let settled = false;
      let timer: ReturnType<typeof setTimeout> | null = null;

      const finish = (shouldRetry: boolean) => {
        if (settled) return;
        settled = true;
        if (timer != null) clearTimeout(timer);
        if (cancelRetryWait === cancel) cancelRetryWait = null;
        resolve(shouldRetry);
      };
      const cancel = () => finish(false);

      cancelRetryWait = cancel;
      timer = setTimeout(() => finish(true), delay);
      // close() may have run between entering waitForRetry() and installing the timer.
      if (closed) cancel();
    });
  };

  const connect = async () => {
    while (!closed) {
      opts.onStateChange?.(retry === 0 ? "connecting" : "reconnecting");
      if (closed) return;

      const controller = new AbortController();
      activeController = controller;
      let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
      let shouldRetry = false;
      try {
        const res = await fetch(url, {
          signal: controller.signal,
          // B9：带上公司 IAM cookie，与 apiFetch (client.ts) 同口径。真部署是同源
          // （nginx 转 /api），cookie 随请求走，SSE 才拿得到登录态。
          // ⚠跨源 VITE_OPENOPS_API_BASE 下本请求会被浏览器预检直接拦掉（后端
          // CORS 是 allow_origins=["*"] 且无 allow_credentials，通配符与
          // credentials:include 互斥）——但 apiFetch 早就是这个状态，即跨源模式对
          // 整个 JSON API 本来就不通，不是 SSE 独有。要支持跨源须后端改 CORS 白名单。
          credentials: "include",
          headers: {
            Accept: "text/event-stream",
            "X-OpenOps-Mock-User": demoIdentity.user,
            "X-OpenOps-Mock-Name": encodeURIComponent(demoIdentity.name),
            ...(lastId != null ? { "Last-Event-ID": String(lastId) } : {}),
          },
        });
        // 401 不是网络抖动，重连一万次也还是 401——退避封顶 8s 就意味着一个未登录标签页
        // 永久每 8 秒打一次后端（内网 access 日志实测被这条刷满）。就地终止：能跳登录就跳，
        // 跳不了就把 onFatal 交给调用方提示，绝不进 catch 的重连分支。
        if (res.status === 401) {
          closed = true;
          const err = await res.json().catch(() => ({})).then(
            (j) => (j as { error?: ApiErrorBody }).error,
          );
          if (!redirectToLoginIfUnauthorized(res.status, err)) {
            opts.onFatal?.(err?.code ?? "UNAUTHORIZED", err?.message ?? "登录已失效，请重新登录");
          }
          return;
        }
        if (!res.ok || !res.body) throw new Error(`SSE HTTP ${res.status}`);
        if (closed || controller.signal.aborted) {
          void res.body.cancel().catch(() => undefined);
          return;
        }
        opts.onStateChange?.("open");
        if (closed || controller.signal.aborted) return;
        retry = 0;
        reader = res.body.getReader();
        activeReader = reader;
        const decoder = new TextDecoder();
        let buf = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done || closed || controller.signal.aborted) break;
          buf += decoder.decode(value, { stream: true });
          let idx: number;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
            if (closed || controller.signal.aborted) return;
            const chunk = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            let ev = "message";
            let id: number | null = null;
            const dataLines: string[] = [];
            for (const line of chunk.split("\n")) {
              if (line.startsWith(":")) continue; // 心跳
              if (line.startsWith("event:")) ev = line.slice(6).trim();
              else if (line.startsWith("id:")) id = Number(line.slice(3).trim());
              else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
            }
            if (ev === "resync") {
              if (!closed && !controller.signal.aborted) opts.onResync?.();
              continue;
            }
            if (!dataLines.length) continue;
            if (id != null) lastId = id;
            try {
              if (!closed && !controller.signal.aborted) {
                opts.onEvent(JSON.parse(dataLines.join("\n")), id);
              }
            } catch {
              /* 非 JSON 数据忽略 */
            }
          }
        }
        if (closed || controller.signal.aborted) return;
        throw new Error("stream ended");
      } catch {
        if (closed || controller.signal.aborted) return;
        retry += 1;
        shouldRetry = true;
      } finally {
        if (activeReader === reader) activeReader = null;
        if (activeController === controller) activeController = null;
        try {
          reader?.releaseLock();
        } catch {
          // cancel/read 尚在收尾时 releaseLock 可能失败；资源已由 AbortController 关闭。
        }
      }

      if (!shouldRetry || closed) return;
      const delay = Math.min(1000 * 2 ** Math.min(retry, 4), 8000);
      if (!(await waitForRetry(delay)) || closed) return;
    }
  };
  void connect();
  return {
    close: () => {
      if (closed) return;
      closed = true;
      cancelRetryWait?.();
      cancelRetryWait = null;
      activeController?.abort();
      const reader = activeReader;
      activeReader = null;
      if (reader) void reader.cancel().catch(() => undefined);
    },
  };
}
