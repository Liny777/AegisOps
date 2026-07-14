import type { BaseEvent, Message } from "@ag-ui/client";
import {
  AgentRunner,
  supportsLocalThreadEndpoints,
  type AgentRunnerConnectRequest,
  type AgentRunnerIsRunningRequest,
  type AgentRunnerRunRequest,
  type AgentRunnerStopRequest,
  type LocalThreadEndpointRecord,
} from "@copilotkit/runtime/v2";
import { createHash } from "node:crypto";
import { Observable } from "rxjs";
import { identityOwnerScope } from "./identity";

export type LifecycleEvent = Record<string, boolean | number | string | null | undefined>;
export type LifecycleLogger = (event: LifecycleEvent) => void;

export interface RunnerActivity {
  activeBrowserStreams: number;
  activeUpstreamRuns: number;
  sharedConnects: number;
}

interface SharedConnect {
  source: Observable<BaseEvent>;
}

function scopedThreadId(ownerScope: string, threadId: string): string {
  return `openops-owner:${ownerScope}:${threadId}`;
}

function threadRef(ownerScope: string, threadId: string): string {
  return createHash("sha256")
    .update(ownerScope)
    .update("\0")
    .update(threadId)
    .digest("hex")
    .slice(0, 12);
}

/**
 * Keeps exactly one durable `connect()` source per active owner/thread pair.
 *
 * Browser SSE subscriptions are deliberately disposable: navigating away only
 * unsubscribes that browser consumer. The durable subscription remains attached
 * to the in-memory runner until the upstream run completes, so navigation never
 * becomes an implicit stop operation and a later browser can replay the stream.
 * The delegate's InMemoryAgentRunner already returns a hot, unbounded replay
 * observable; retaining that source avoids creating a second unbounded cache.
 */
export class SharedConnectAgentRunner extends AgentRunner {
  readonly ɵsupportsLocalThreadEndpoints: boolean;
  private readonly sharedConnects = new Map<string, SharedConnect>();
  private readonly activeRuns = new Set<symbol>();
  private activeBrowserStreams = 0;

  constructor(
    private readonly delegate: AgentRunner,
    private readonly log: LifecycleLogger = () => undefined,
  ) {
    super();
    // Preserve the InMemoryAgentRunner REST thread capabilities advertised in
    // CopilotKit runtime info. Wrapping the runner must not silently disable
    // transcript/state restoration endpoints.
    this.ɵsupportsLocalThreadEndpoints = supportsLocalThreadEndpoints(delegate);
  }

  activity(): RunnerActivity {
    return {
      activeBrowserStreams: this.activeBrowserStreams,
      activeUpstreamRuns: this.activeRuns.size,
      sharedConnects: this.sharedConnects.size,
    };
  }

  run(request: AgentRunnerRunRequest): Observable<BaseEvent> {
    const ownerScope = identityOwnerScope();
    const source = this.delegate.run({
      ...request,
      // The AG-UI input keeps the public run/thread id. Only the runner's
      // in-memory storage key is scoped, so backend URLs and emitted events are
      // unchanged while another identity cannot connect to or stop this run.
      threadId: scopedThreadId(ownerScope, request.threadId),
    });
    const token = Symbol(request.threadId);
    const ref = threadRef(ownerScope, request.threadId);
    let finished = false;

    this.activeRuns.add(token);
    this.log({ event: "upstream_run_started", threadRef: ref });

    const finish = (outcome: "complete" | "error") => {
      if (finished) return;
      finished = true;
      this.activeRuns.delete(token);
      this.log({ event: "upstream_run_finished", threadRef: ref, outcome });
    };

    source.subscribe({
      // InMemoryAgentRunner.run() returns a hot ReplaySubject. This observer is
      // intentionally event-less: it tracks completion after the browser has
      // gone without allocating a second unbounded replay buffer.
      error: () => finish("error"),
      complete: () => finish("complete"),
    });

    return this.trackBrowserStream(source, "run", ownerScope, request.threadId);
  }

  connect(request: AgentRunnerConnectRequest): Observable<BaseEvent> {
    const ownerScope = identityOwnerScope();
    const key = scopedThreadId(ownerScope, request.threadId);
    let shared = this.sharedConnects.get(key);
    if (!shared) {
      const ref = threadRef(ownerScope, request.threadId);
      let finished = false;
      const finish = (outcome: "complete" | "error") => {
        if (finished) return;
        finished = true;
        if (this.sharedConnects.get(key) === shared) {
          this.sharedConnects.delete(key);
        }
        this.log({ event: "shared_connect_finished", threadRef: ref, outcome });
      };

      this.log({ event: "shared_connect_started", threadRef: ref });
      try {
        const source = this.delegate.connect({ ...request, threadId: key });
        shared = { source };
        this.sharedConnects.set(key, shared);
        // The lifecycle observer is the durable attachment that survives all
        // browser consumers. It observes only terminal signals and does not
        // allocate or copy the delegate's replayed events.
        source.subscribe({
          error: () => finish("error"),
          complete: () => finish("complete"),
        });
      } catch (error) {
        finish("error");
        throw error;
      }
    }

    return this.trackBrowserStream(shared.source, "connect", ownerScope, request.threadId);
  }

  isRunning(request: AgentRunnerIsRunningRequest): Promise<boolean> {
    const ownerScope = identityOwnerScope();
    return this.delegate.isRunning({
      ...request,
      threadId: scopedThreadId(ownerScope, request.threadId),
    });
  }

  async stop(request: AgentRunnerStopRequest): Promise<boolean | undefined> {
    const ownerScope = identityOwnerScope();
    const ref = threadRef(ownerScope, request.threadId);
    this.log({ event: "stop_requested", threadRef: ref });
    try {
      const stopped = await this.delegate.stop({
        ...request,
        threadId: scopedThreadId(ownerScope, request.threadId),
      });
      this.log({ event: "stop_finished", threadRef: ref, stopped: stopped ?? null });
      return stopped;
    } catch (error) {
      this.log({ event: "stop_finished", threadRef: ref, outcome: "error" });
      throw error;
    }
  }

  listThreads(): LocalThreadEndpointRecord[] {
    const prefix = `${scopedThreadId(identityOwnerScope(), "")}`;
    return this.localThreadDelegate()
      .listThreads()
      .filter((thread) => thread.id.startsWith(prefix))
      .map((thread) => ({ ...thread, id: thread.id.slice(prefix.length) }));
  }

  getThreadMessages(threadId: string): Message[] {
    return this.localThreadDelegate().getThreadMessages(
      scopedThreadId(identityOwnerScope(), threadId),
    );
  }

  getThreadEvents(threadId: string): BaseEvent[] {
    return this.localThreadDelegate().getThreadEvents(
      scopedThreadId(identityOwnerScope(), threadId),
    );
  }

  getThreadState(threadId: string): Record<string, unknown> | null {
    return this.localThreadDelegate().getThreadState(
      scopedThreadId(identityOwnerScope(), threadId),
    );
  }

  clearThreads(): never {
    // CopilotKit's local runner exposes only a process-wide clear operation.
    // Once thread storage is owner-scoped, forwarding it would let any caller
    // erase every user's transcript. Reject until the delegate offers a
    // per-owner/per-thread deletion API.
    throw new Error("Global thread clearing is disabled for owner-scoped runtime state");
  }

  private localThreadDelegate() {
    if (!supportsLocalThreadEndpoints(this.delegate)) {
      throw new Error("The wrapped runner does not support local thread endpoints");
    }
    return this.delegate;
  }

  private trackBrowserStream(
    source: Observable<BaseEvent>,
    kind: "connect" | "run",
    ownerScope: string,
    threadId: string,
  ): Observable<BaseEvent> {
    return new Observable<BaseEvent>((subscriber) => {
      const ref = threadRef(ownerScope, threadId);
      let closed = false;
      this.activeBrowserStreams += 1;
      this.log({ event: "browser_stream_opened", kind, threadRef: ref });

      const subscription = source.subscribe(subscriber);
      return () => {
        subscription.unsubscribe();
        if (closed) return;
        closed = true;
        this.activeBrowserStreams = Math.max(0, this.activeBrowserStreams - 1);
        this.log({ event: "browser_stream_closed", kind, threadRef: ref });
      };
    });
  }
}
