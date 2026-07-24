import { EventType, type BaseEvent, type Message } from "@ag-ui/client";
import {
  AgentRunner,
  supportsLocalThreadEndpoints,
  type AgentRunnerConnectRequest,
  type AgentRunnerIsRunningRequest,
  type AgentRunnerRunRequest,
  type AgentRunnerStopRequest,
  type LocalThreadEndpointRecord,
} from "@copilotkit/runtime/v2";
import { createHash, randomUUID } from "node:crypto";
import { performance } from "node:perf_hooks";
import { Observable, type Subscription } from "rxjs";
import {
  identityHeaders,
  identityOwnerScope,
  runWithIdentity,
  type Identity,
} from "./identity";
import {
  type LifecycleLogger,
  type RunnerActivity,
  SharedConnectAgentRunner,
} from "./shared-runner";

export type TranscriptMessage = Extract<Message, { role: "user" | "assistant" }>;

export interface TranscriptLoadRequest {
  threadId: string;
  identity: Identity;
  signal: AbortSignal;
}

export type TranscriptLoader = (request: TranscriptLoadRequest) => Promise<TranscriptMessage[]>;

export class TranscriptLoadError extends Error {
  constructor(
    readonly code: string,
    message = "Conversation history could not be restored",
  ) {
    super(message);
    this.name = "TranscriptLoadError";
  }
}

function transcriptUrl(backendBase: string, threadId: string): string {
  return `${backendBase}/api/openops/v1/agent-runs/${encodeURIComponent(threadId)}/messages`;
}

function isTranscriptMessage(value: unknown): value is TranscriptMessage {
  if (!value || typeof value !== "object") return false;
  const message = value as Record<string, unknown>;
  return (
    typeof message.id === "string" &&
    message.id.length > 0 &&
    (message.role === "user" || message.role === "assistant") &&
    typeof message.content === "string"
  );
}

/** Strict backend adapter: auth/network/schema failures must fail `/connect`. */
export function createBackendTranscriptLoader(
  backendBase: string,
  fetchImpl: typeof fetch = fetch,
): TranscriptLoader {
  const base = backendBase.replace(/\/+$/, "");
  return async ({ threadId, identity, signal }) => {
    let response: Response;
    try {
      response = await fetchImpl(transcriptUrl(base, threadId), {
        method: "GET",
        headers: new Headers({ Accept: "application/json", ...identity }),
        signal,
      });
    } catch (error) {
      if (signal.aborted) throw error;
      throw new TranscriptLoadError("NETWORK_ERROR");
    }

    if (!response.ok) {
      throw new TranscriptLoadError(`HTTP_${response.status}`);
    }

    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new TranscriptLoadError("INVALID_RESPONSE");
    }
    const data = (payload as { data?: unknown } | null)?.data;
    if (!Array.isArray(data) || !data.every(isTranscriptMessage)) {
      throw new TranscriptLoadError("INVALID_RESPONSE");
    }
    return data;
  };
}

function hashedThreadRef(ownerScope: string, threadId: string): string {
  return createHash("sha256")
    .update(ownerScope)
    .update("\0")
    .update(threadId)
    .digest("hex")
    .slice(0, 12);
}

function roundedDuration(startedAt: number): number {
  return Math.round((performance.now() - startedAt) * 10) / 10;
}

function restoreErrorCode(error: unknown, aborted: boolean): string {
  if (aborted) return "ABORTED";
  if (error instanceof TranscriptLoadError) return error.code;
  return "INTERNAL_ERROR";
}

interface LocalStatus {
  available: boolean;
  messageCount: number;
}

/** 回放追平信号:回放段(合并后)发完、live 透传开始前注入的 CUSTOM 事件名。
 *  刻意不用 `openops.*` 前缀——前端 OpenOpsCustomEventBridge 只转发 openops.*,
 *  旧版前端遇到新 sidecar 会自然忽略,不会把无 envelope 的合成事件灌进活动线。 */
export const REPLAY_CAUGHT_UP_EVENT = "oa.replay.caught_up";

const DELTA_EVENT_KEYS: Partial<Record<string, (event: Record<string, unknown>) => string>> = {
  [EventType.TEXT_MESSAGE_CONTENT]: (e) => `text:${String(e.messageId ?? "")}`,
  [EventType.TOOL_CALL_ARGS]: (e) => `args:${String(e.toolCallId ?? "")}`,
  [EventType.REASONING_MESSAGE_CONTENT]: (e) => `reasoning:${String(e.messageId ?? "")}`,
  [EventType.THINKING_TEXT_MESSAGE_CONTENT]: (e) => `thinking:${String(e.messageId ?? "")}`,
};

function deltaKey(event: BaseEvent): string | null {
  const keyOf = DELTA_EVENT_KEYS[event.type as string];
  return keyOf ? keyOf(event as unknown as Record<string, unknown>) : null;
}

/** 压缩回放段:把**连续**的同源增量事件(同 message 的文本 delta / 同 toolCall 的
 *  args delta / reasoning 增量)合并成一条,其余事件原序原样保留。
 *
 *  进行中 run 的内存回放是 ReplaySubject 逐条重放(几百条增量),浏览器端呈现
 *  「重新打字」;已完成 run 早有 compactEvents 合并,但它面向终止良好的完整流,
 *  对未封口的 in-flight 消息不安全——这里只做保结构的连续段合并:不产生也不
 *  丢弃任何 START/END,合并后的 delta 发出后 live 段继续追加天然可续。 */
export function compactReplaySegment(events: BaseEvent[]): BaseEvent[] {
  const out: BaseEvent[] = [];
  for (const event of events) {
    const key = deltaKey(event);
    const prev = out[out.length - 1];
    if (key !== null && prev && deltaKey(prev) === key) {
      const prevDelta = String((prev as unknown as Record<string, unknown>).delta ?? "");
      const nextDelta = String((event as unknown as Record<string, unknown>).delta ?? "");
      out[out.length - 1] = { ...prev, delta: prevDelta + nextDelta } as BaseEvent;
      continue;
    }
    out.push(event);
  }
  return out;
}

function caughtUpEvent(replayed: number): BaseEvent {
  return {
    type: EventType.CUSTOM,
    name: REPLAY_CAUGHT_UP_EVENT,
    value: { replayed },
  } as BaseEvent;
}

/**
 * Adds durable AgentState hydration to the owner-scoped in-memory runner.
 *
 * The in-memory stream remains authoritative whenever it is active or has
 * events. Only a true local miss reaches the backend. A second local check
 * after the fetch prevents a stale snapshot from replacing a run that started
 * while persistence was loading.
 */
export class PersistentConnectAgentRunner extends AgentRunner {
  readonly ɵsupportsLocalThreadEndpoints: boolean;

  constructor(
    private readonly delegate: SharedConnectAgentRunner,
    private readonly loadTranscript: TranscriptLoader,
    private readonly log: LifecycleLogger = () => undefined,
  ) {
    super();
    this.ɵsupportsLocalThreadEndpoints = supportsLocalThreadEndpoints(delegate);
  }

  activity(): RunnerActivity {
    return this.delegate.activity();
  }

  run(request: AgentRunnerRunRequest): Observable<BaseEvent> {
    return this.delegate.run(request);
  }

  connect(request: AgentRunnerConnectRequest): Observable<BaseEvent> {
    const identity = { ...identityHeaders() };
    const ownerScope = identityOwnerScope(identity);
    const ref = hashedThreadRef(ownerScope, request.threadId);

    return new Observable<BaseEvent>((subscriber) => {
      const abortController = new AbortController();
      const startedAt = performance.now();
      let delegateSubscription: Subscription | undefined;
      let restoreFinished = false;

      this.log({ event: "transcript_restore_started", threadRef: ref });

      const finish = (
        source: "backend" | "memory" | null,
        messageCount: number,
        errorCode?: string,
      ) => {
        if (restoreFinished) return;
        restoreFinished = true;
        this.log({
          event: "transcript_restore_finished",
          threadRef: ref,
          source,
          messageCount,
          durationMs: roundedDuration(startedAt),
          errorCode: errorCode ?? null,
        });
      };

      const attachMemory = (status: LocalStatus) => {
        finish("memory", status.messageCount);
        if (subscriber.closed) return;
        // 回放段「秒到」:ReplaySubject 的缓冲值在订阅的同一 tick 内同步交付,首个
        // 落在后续微任务的事件即 live 边界——订阅期间先缓冲,微任务边界一到就把
        // 回放段合并成整条发出,再注入追平信号,live 段原样透传。
        //
        // 追平信号必须尊重 AG-UI verifyEvents 的 run 边界(浏览器端强校验):CUSTOM
        // 不得是首事件、不得出现在 RUN_FINISHED/RUN_ERROR 之后。按段末开合状态选注入点:
        // 段末开着(in-flight run)→ 追加在段尾;段末闭合(已完成 run 的整段回放)→ 插到
        // 最后一个终态事件之前(与下方 MISS 快照路径同形);段内没有任何 RUN_STARTED
        // (连上时上游首帧未到)→ 挂起,待 live 段首个 RUN_STARTED 之后补发;错误/终止
        // 路径直接放弃信号(前端 5s 超时兜底)。
        let replayBuffer: BaseEvent[] | null = [];
        let markerPending = false;
        const flushReplay = (terminal: boolean) => {
          if (replayBuffer === null) return;
          const segment = replayBuffer;
          replayBuffer = null;
          if (subscriber.closed) return;
          const compacted = compactReplaySegment(segment);
          let hasStart = false;
          let openAtEnd = false;
          for (const event of compacted) {
            if (event.type === EventType.RUN_STARTED) {
              hasStart = true;
              openAtEnd = true;
            } else if (event.type === EventType.RUN_FINISHED || event.type === EventType.RUN_ERROR) {
              openAtEnd = false;
            }
          }
          if (!hasStart) {
            for (const event of compacted) subscriber.next(event);
            markerPending = !terminal;
          } else if (openAtEnd) {
            for (const event of compacted) subscriber.next(event);
            subscriber.next(caughtUpEvent(segment.length));
          } else {
            // 信号插到「最后一个真正闭合了开着的 run」的终态事件之前。裸终态记录
            // (run 在其前一刻并未开着——如上游首帧前失败落下的 [RUN_ERROR])不是
            // 合法插入点:插上去会落在 verifyEvents 的 finished 态里被拒;找不到
            // 合法位置就放弃信号(前端 5s 超时兜底)。
            let insertAt = -1;
            let open = false;
            for (let index = 0; index < compacted.length; index += 1) {
              const type = compacted[index]!.type;
              if (type === EventType.RUN_STARTED) {
                open = true;
              } else if (type === EventType.RUN_FINISHED || type === EventType.RUN_ERROR) {
                if (open) insertAt = index;
                open = false;
              }
            }
            compacted.forEach((event, index) => {
              if (index === insertAt) subscriber.next(caughtUpEvent(segment.length));
              subscriber.next(event);
            });
          }
          if (segment.length) {
            this.log({ event: "replay_compacted", threadRef: ref, rawEvents: segment.length });
          }
        };
        delegateSubscription = runWithIdentity(identity, () =>
          this.delegate.connect(request).subscribe({
            next: (event) => {
              if (replayBuffer !== null) {
                replayBuffer.push(event);
                return;
              }
              subscriber.next(event);
              if (markerPending && event.type === EventType.RUN_STARTED) {
                markerPending = false;
                subscriber.next(caughtUpEvent(0));
              }
            },
            error: (err) => {
              flushReplay(true);
              subscriber.error(err);
            },
            complete: () => {
              flushReplay(true);
              subscriber.complete();
            },
          }),
        );
        queueMicrotask(() => flushReplay(false));
      };

      void (async () => {
        try {
          const beforeLoad = await this.localStatus(request, identity);
          if (beforeLoad.available) {
            attachMemory(beforeLoad);
            return;
          }

          const messages = await this.loadTranscript({
            threadId: request.threadId,
            identity,
            signal: abortController.signal,
          });
          if (subscriber.closed) return;

          const afterLoad = await this.localStatus(request, identity);
          if (afterLoad.available) {
            attachMemory(afterLoad);
            return;
          }

          const hydrationRunId = `history-${randomUUID()}`;
          subscriber.next({
            type: EventType.RUN_STARTED,
            threadId: request.threadId,
            runId: hydrationRunId,
          } as BaseEvent);
          subscriber.next({
            type: EventType.MESSAGES_SNAPSHOT,
            messages,
          } as BaseEvent);
          // 快照本身就是「秒到」;补追平信号让前端「正在同步对话」提示统一熄灭
          subscriber.next(caughtUpEvent(messages.length));
          subscriber.next({
            type: EventType.RUN_FINISHED,
            threadId: request.threadId,
            runId: hydrationRunId,
            outcome: { type: "success" },
          } as BaseEvent);
          finish("backend", messages.length);
          subscriber.complete();
        } catch (error) {
          const code = restoreErrorCode(error, abortController.signal.aborted || subscriber.closed);
          finish(null, 0, code);
          if (!subscriber.closed && !abortController.signal.aborted) {
            subscriber.error(new TranscriptLoadError(code));
          }
        }
      })();

      return () => {
        if (!restoreFinished) abortController.abort();
        delegateSubscription?.unsubscribe();
      };
    });
  }

  isRunning(request: AgentRunnerIsRunningRequest): Promise<boolean> {
    return this.delegate.isRunning(request);
  }

  stop(request: AgentRunnerStopRequest): Promise<boolean | undefined> {
    return this.delegate.stop(request);
  }

  listThreads(): LocalThreadEndpointRecord[] {
    return this.delegate.listThreads();
  }

  getThreadMessages(threadId: string): Message[] {
    return this.delegate.getThreadMessages(threadId);
  }

  getThreadEvents(threadId: string): BaseEvent[] {
    return this.delegate.getThreadEvents(threadId);
  }

  getThreadState(threadId: string): Record<string, unknown> | null {
    return this.delegate.getThreadState(threadId);
  }

  clearThreads(): never {
    return this.delegate.clearThreads();
  }

  private async localStatus(
    request: AgentRunnerConnectRequest,
    identity: Identity,
  ): Promise<LocalStatus> {
    return runWithIdentity(identity, async () => {
      const running = await this.delegate.isRunning({ threadId: request.threadId });
      const events = this.delegate.getThreadEvents(request.threadId);
      return {
        available: running || events.length > 0,
        messageCount: this.delegate.getThreadMessages(request.threadId).length,
      };
    });
  }
}
