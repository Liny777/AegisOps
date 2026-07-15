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
        delegateSubscription = runWithIdentity(identity, () =>
          this.delegate.connect(request).subscribe(subscriber),
        );
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
