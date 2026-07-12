// CopilotKit Runtime sidecar（Part B：CopilotChat 接管对话区）。
//
// 最小实现（参考 openOps-Dev strategy-a 的 2529 行版，裁掉 a2ui catalog / grafana /
// front-door 反代 / 会话持久化——本项目这些都由 FastAPI REST 承担）：
//   浏览器 <CopilotKit runtimeUrl=/api/copilotkit>（vite proxy → 本进程 :4002）
//     → CopilotRuntime(v2) + InMemoryAgentRunner
//     → HttpAgent → FastAPI `POST /api/openops/v1/agent-runs/{runId}/agui`（AG-UI 标准流）
//
// 关键适配（与老项目的差异，B0 清单）：
// 1. 本项目 AG-UI 端点是 **per-run** 路径——CopilotChat 的 threadId 即 runId（前端约定），
//    HttpAgent 的 url 是静态占位，真正的目标在 aguiFetch 里按请求体 threadId 重写。
// 2. 身份 = mock IAM 头（X-OpenOps-Mock-User/Name），经 AsyncLocalStorage 从入站请求
//    透传到出站 fetch（identity.ts）；真 IAM（B9）后同通道带 cookie。
// 3. 停止按钮 = 浏览器 abort → 本进程 fetch abort → FastAPI 流断开 →
//    agui_service 断流取消桥停掉任务（后端半边已落）。
import { HttpAgent } from "@ag-ui/client";
import {
  CopilotRuntime,
  createCopilotRuntimeHandler,
  InMemoryAgentRunner,
} from "@copilotkit/runtime/v2";
import express from "express";
import type { RequestHandler } from "express";
import { Readable } from "node:stream";
import type { ReadableStream as WebReadableStream } from "node:stream/web";
import { identityFromIncoming, identityHeaders, runWithIdentity } from "./identity";

const port = Number(process.env.COPILOTKIT_RUNTIME_PORT ?? 4002);
const host = process.env.COPILOTKIT_RUNTIME_HOST ?? "127.0.0.1";
const backendBase = (process.env.OPENOPS_BACKEND_URL ?? "http://127.0.0.1:18082").replace(/\/+$/, "");
const agentId = process.env.COPILOTKIT_AGENT_ID ?? "sre-agent";
const COPILOT_BASE = "/api/copilotkit";

function aguiUrlForRun(runId: string): string {
  return `${backendBase}/api/openops/v1/agent-runs/${encodeURIComponent(runId)}/agui`;
}

/** 出站 fetch：按 RunAgentInput.threadId（=runId）重写目标 URL + 注入身份头。 */
const aguiFetch: typeof fetch = (input, init) => {
  let url =
    typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
  if (typeof init?.body === "string") {
    try {
      const runId = (JSON.parse(init.body) as { threadId?: unknown })?.threadId;
      if (typeof runId === "string" && runId) url = aguiUrlForRun(runId);
    } catch {
      /* 非 JSON body：保持原 url */
    }
  }
  const headers = new Headers(init?.headers as HeadersInit | undefined);
  for (const [k, v] of Object.entries(identityHeaders())) {
    if (!headers.has(k)) headers.set(k, v);
  }
  return fetch(url, { ...init, headers });
};

const runtime = new CopilotRuntime({
  // AgentsConfig 是 PromiseLike（1.61.2）；url 为占位：真实目标每次请求在 aguiFetch 按 threadId 计算
  agents: Promise.resolve({
    [agentId]: new HttpAgent({ url: aguiUrlForRun("placeholder"), fetch: aguiFetch }),
  }),
  runner: new InMemoryAgentRunner(),
});

/** web-fetch 风格 handler → express（照搬老项目 delegateToCopilot，SSE 流式回传）。 */
function delegateToCopilot(handler: (request: Request) => Promise<Response>): RequestHandler {
  return async (request, response, next) => {
    try {
      const webRequest = new Request(
        new URL(request.originalUrl, `http://${request.headers.host}`),
        {
          method: request.method,
          headers: request.headers as unknown as HeadersInit,
          body: ["GET", "HEAD"].includes(request.method) ? undefined : (request as unknown as BodyInit),
          duplex: "half",
        } as RequestInit & { duplex: "half" },
      );
      const webResponse = await handler(webRequest);
      response.status(webResponse.status);
      webResponse.headers.forEach((value, key) => response.setHeader(key, value));
      if (webResponse.body) {
        Readable.fromWeb(webResponse.body as unknown as WebReadableStream).pipe(response);
      } else {
        response.end();
      }
    } catch (error) {
      next(error);
    }
  };
}

const app = express();
// ⚠️ 不挂任何 body 解析中间件：delegateToCopilot 直接消费原始请求流。
app.use((request, _response, next) => {
  runWithIdentity(identityFromIncoming(request.headers), () => next());
});
app.get("/healthz", (_request, response) => {
  response.json({ ok: true, backend: backendBase, agentId });
});
const copilotHandler = delegateToCopilot(
  createCopilotRuntimeHandler({ runtime, basePath: COPILOT_BASE, mode: "single-route", cors: true }),
);
app.all(COPILOT_BASE, copilotHandler);      // single-route：POST basePath 本身
app.all(`${COPILOT_BASE}/*`, copilotHandler); // 兼容多路由形态的子路径探测

app.listen(port, host, () => {
  console.log(`[copilot-runtime] http://${host}:${port}${COPILOT_BASE} → ${backendBase} (agent=${agentId})`);
});
