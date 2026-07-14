// CopilotKit Runtime sidecar（Part B：CopilotChat 接管对话区）。
//
// 浏览器 <CopilotKit runtimeUrl=/api/copilotkit>（vite proxy → 本进程 :4002）
//   → CopilotRuntime(v2) + SharedConnectAgentRunner(InMemoryAgentRunner)
//   → HttpAgent → FastAPI per-run `/agui`（AG-UI 标准流）
//
// threadId 即 runId。浏览器离开会话只断开该消费者；后台 Run 继续执行，只有显式
// `agent/stop` 才会调用 runner.stop() 并中止上游 Agent。
import { HttpAgent } from "@ag-ui/client";
import {
  CopilotRuntime,
  InMemoryAgentRunner,
  type CopilotRuntimeLike,
} from "@copilotkit/runtime/v2";
import { createCopilotExpressHandler } from "@copilotkit/runtime/v2/express";
import express from "express";
import type { RequestHandler } from "express";
import { randomUUID } from "node:crypto";
import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { monitorEventLoopDelay, performance } from "node:perf_hooks";
import { identityFromIncoming, identityHeaders, runWithIdentity } from "./identity";
import {
  SharedConnectAgentRunner,
  type LifecycleEvent,
  type LifecycleLogger,
} from "./shared-runner";

const DEFAULT_PORT = Number(process.env.COPILOTKIT_RUNTIME_PORT ?? 4002);
const DEFAULT_HOST = process.env.COPILOTKIT_RUNTIME_HOST ?? "127.0.0.1";
const DEFAULT_BACKEND_BASE = (
  process.env.OPENOPS_BACKEND_URL ?? "http://127.0.0.1:18082"
).replace(/\/+$/, "");
const DEFAULT_AGENT_ID = process.env.COPILOTKIT_AGENT_ID ?? "sre-agent";
const COPILOT_BASE = "/api/copilotkit";
const eventLoopDelay = monitorEventLoopDelay({ resolution: 20 });
eventLoopDelay.enable();

export interface CopilotRuntimeAppOptions {
  runtime: CopilotRuntimeLike;
  runner: SharedConnectAgentRunner;
  backendBase: string;
  agentId: string;
  log?: LifecycleLogger;
}

export interface RuntimeSidecarOptions {
  backendBase?: string;
  agentId?: string;
  log?: LifecycleLogger;
  fetchImpl?: typeof fetch;
}

function structuredLog(event: LifecycleEvent): void {
  console.log(
    JSON.stringify({
      timestamp: new Date().toISOString(),
      component: "copilot-runtime",
      ...event,
    }),
  );
}

function aguiUrlForRun(backendBase: string, runId: string): string {
  return `${backendBase}/api/openops/v1/agent-runs/${encodeURIComponent(runId)}/agui`;
}

function nanosecondsToMilliseconds(value: number): number {
  return Number.isFinite(value) ? Math.round(value / 100_000) / 10 : 0;
}

function openFileDescriptorCount(): number | null {
  if (process.platform !== "linux") return null;
  try {
    return readdirSync("/proc/self/fd").length;
  } catch {
    return null;
  }
}

function processResources() {
  const memory = process.memoryUsage();
  const resources = {
    heapUsedBytes: memory.heapUsed,
    rssBytes: memory.rss,
    openFileDescriptors: openFileDescriptorCount(),
    eventLoopLagMeanMs: nanosecondsToMilliseconds(eventLoopDelay.mean),
    eventLoopLagMaxMs: nanosecondsToMilliseconds(eventLoopDelay.max),
  };
  // Each health response reports the interval since the preceding sample,
  // which makes before/after load-test comparisons meaningful.
  eventLoopDelay.reset();
  return resources;
}

/** 出站 fetch：按 RunAgentInput.threadId（=runId）重写目标 URL + 注入身份头。 */
export function createAguiFetch(backendBase: string, fetchImpl: typeof fetch = fetch): typeof fetch {
  return (input, init) => {
    let url =
      typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    if (typeof init?.body === "string") {
      try {
        const runId = (JSON.parse(init.body) as { threadId?: unknown })?.threadId;
        if (typeof runId === "string" && runId) url = aguiUrlForRun(backendBase, runId);
      } catch {
        // 非 JSON body：保持原 URL。
      }
    }
    const headers = new Headers(init?.headers as HeadersInit | undefined);
    for (const [key, value] of Object.entries(identityHeaders())) {
      if (!headers.has(key)) headers.set(key, value);
    }
    return fetchImpl(url, { ...init, headers });
  };
}

class HttpActivity {
  private activeRequests = 0;

  constructor(private readonly log: LifecycleLogger) {}

  count(): number {
    return this.activeRequests;
  }

  middleware(): RequestHandler {
    return (request, response, next) => {
      const requestId = randomUUID();
      const startedAt = performance.now();
      let finished = false;
      this.activeRequests += 1;
      this.log({
        event: "http_request_started",
        requestId,
        method: request.method,
        path: request.path,
      });

      const finish = (outcome: "aborted" | "complete") => {
        if (finished) return;
        finished = true;
        this.activeRequests = Math.max(0, this.activeRequests - 1);
        this.log({
          event: "http_request_finished",
          requestId,
          method: request.method,
          path: request.path,
          status: response.statusCode,
          outcome,
          durationMs: Math.round((performance.now() - startedAt) * 10) / 10,
        });
      };

      response.once("finish", () => finish("complete"));
      response.once("close", () => finish(response.writableFinished ? "complete" : "aborted"));
      next();
    };
  }
}

/** Build an injectable Express app; importing this module never starts a listener. */
export function createCopilotRuntimeApp(options: CopilotRuntimeAppOptions): express.Express {
  const log = options.log ?? structuredLog;
  const httpActivity = new HttpActivity(log);
  const app = express();

  // 不挂 body parser；官方 Express bridge 直接消费原始请求流，并将 response.close
  // 传播为 Fetch Request AbortSignal。
  app.use((request, _response, next) => {
    runWithIdentity(identityFromIncoming(request.headers), () => next());
  });

  app.get("/healthz", (_request, response) => {
    response.json({
      ok: true,
      backend: options.backendBase,
      agentId: options.agentId,
      activity: {
        activeHttpRequests: httpActivity.count(),
        ...options.runner.activity(),
      },
      resources: processResources(),
    });
  });

  app.use(httpActivity.middleware());
  app.use(
    createCopilotExpressHandler({
      runtime: options.runtime,
      basePath: COPILOT_BASE,
      mode: "single-route",
      cors: true,
    }),
  );

  return app;
}

/** Construct the production runtime and its observable lifecycle wrapper. */
export function createRuntimeSidecar(options: RuntimeSidecarOptions = {}) {
  const backendBase = (options.backendBase ?? DEFAULT_BACKEND_BASE).replace(/\/+$/, "");
  const agentId = options.agentId ?? DEFAULT_AGENT_ID;
  const log = options.log ?? structuredLog;
  const runner = new SharedConnectAgentRunner(new InMemoryAgentRunner(), log);
  const aguiFetch = createAguiFetch(backendBase, options.fetchImpl);
  const runtime = new CopilotRuntime({
    // url 为占位；真实目标每次请求在 aguiFetch 中按 threadId 计算。
    agents: Promise.resolve({
      [agentId]: new HttpAgent({
        url: aguiUrlForRun(backendBase, "placeholder"),
        fetch: aguiFetch,
      }),
    }),
    runner,
  });
  const app = createCopilotRuntimeApp({ runtime, runner, backendBase, agentId, log });
  return { app, runtime, runner, backendBase, agentId, log };
}

export function startRuntimeSidecar(): void {
  const sidecar = createRuntimeSidecar();
  sidecar.app.listen(DEFAULT_PORT, DEFAULT_HOST, () => {
    sidecar.log({
      event: "server_started",
      host: DEFAULT_HOST,
      port: DEFAULT_PORT,
      basePath: COPILOT_BASE,
      agentId: sidecar.agentId,
    });
  });
}

const entrypoint = process.argv[1] ? resolve(process.argv[1]) : "";
if (entrypoint && fileURLToPath(import.meta.url) === entrypoint) {
  startRuntimeSidecar();
}
