/**
 * fetch 流式 SSE 客户端（参考 frontend-v2 EventSource 模式，但 mock 鉴权走请求头，
 * EventSource 不支持自定义头 → 用 fetch+ReadableStream 手解析；B8 真 IAM(Cookie) 后可换回 EventSource）。
 * 支持：Last-Event-ID 断线补发、自动重连（指数退避）、resync 信号回调。
 */
import { demoIdentity } from "../api/client";

export interface SseHandle {
  close: () => void;
}

export function subscribeSse(
  url: string,
  opts: {
    onEvent: (data: unknown, id: number | null) => void;
    onResync?: () => void;
    onStateChange?: (s: "connecting" | "open" | "reconnecting") => void;
  },
): SseHandle {
  let closed = false;
  let lastId: number | null = null;
  let retry = 0;

  const connect = async () => {
    while (!closed) {
      opts.onStateChange?.(retry === 0 ? "connecting" : "reconnecting");
      try {
        const res = await fetch(url, {
          headers: {
            Accept: "text/event-stream",
            "X-OpenOps-Mock-User": demoIdentity.user,
            "X-OpenOps-Mock-Name": encodeURIComponent(demoIdentity.name),
            ...(lastId != null ? { "Last-Event-ID": String(lastId) } : {}),
          },
        });
        if (!res.ok || !res.body) throw new Error(`SSE HTTP ${res.status}`);
        opts.onStateChange?.("open");
        retry = 0;
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done || closed) break;
          buf += decoder.decode(value, { stream: true });
          let idx: number;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
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
              opts.onResync?.();
              continue;
            }
            if (!dataLines.length) continue;
            if (id != null) lastId = id;
            try {
              opts.onEvent(JSON.parse(dataLines.join("\n")), id);
            } catch {
              /* 非 JSON 数据忽略 */
            }
          }
        }
        if (closed) return;
        throw new Error("stream ended");
      } catch {
        if (closed) return;
        retry += 1;
        await new Promise((r) => setTimeout(r, Math.min(1000 * 2 ** Math.min(retry, 4), 8000)));
      }
    }
  };
  void connect();
  return { close: () => { closed = true; } };
}
