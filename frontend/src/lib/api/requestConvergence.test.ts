import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { createServer } from "vite";

type Api = {
  listConversations(options?: { signal?: AbortSignal }): Promise<Array<{ id: string; title: string }>>;
  ensureRun(instanceId: string, options?: { signal?: AbortSignal }): Promise<string>;
  renameRun(runId: string, title: string): Promise<void>;
  getRunState(runId: string, options?: { signal?: AbortSignal }): Promise<Record<string, unknown>>;
  getAvailableSkills(
    instanceId: string,
    options?: { signal?: AbortSignal },
  ): Promise<Array<{ skill_id: string }>>;
};

const jsonResponse = (data: unknown) => new Response(JSON.stringify({ data }), {
  status: 200,
  headers: { "Content-Type": "application/json" },
});

test("API 请求收敛", async (suite) => {
  const server = await createServer({
    root: fileURLToPath(new URL("../../../", import.meta.url)),
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  suite.after(() => server.close());
  const { api } = await server.ssrLoadModule("/src/lib/api/index.ts") as { api: Api };

  await suite.test("并发 ensureRun 共享列表查询且只创建一次", async (t) => {
    const originalFetch = globalThis.fetch;
    t.after(() => { globalThis.fetch = originalFetch; });

    let listCalls = 0;
    let createCalls = 0;
    let releaseList!: () => void;
    const listGate = new Promise<void>((resolve) => { releaseList = resolve; });
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/openops/v1/agent-runs") && (init?.method ?? "GET") === "GET") {
        listCalls += 1;
        await listGate;
        return jsonResponse([]);
      }
      if (url.endsWith("/openops/v1/agent-runs") && init?.method === "POST") {
        createCalls += 1;
        return jsonResponse({ run: { agent_run_id: "run-created" } });
      }
      throw new Error(`unexpected request: ${init?.method ?? "GET"} ${url}`);
    }) as typeof fetch;

    const first = api.ensureRun("agent-one");
    const second = api.ensureRun("agent-one");
    assert.equal(listCalls, 1);
    releaseList();

    assert.equal(await first, "run-created");
    assert.equal(await second, "run-created");
    assert.equal(listCalls, 1);
    assert.equal(createCalls, 1);
  });

  await suite.test("getRunState 将 AbortSignal 传到底层 fetch", async (t) => {
    const originalFetch = globalThis.fetch;
    t.after(() => { globalThis.fetch = originalFetch; });

    let receivedSignal: AbortSignal | undefined;
    globalThis.fetch = ((_input: RequestInfo | URL, init?: RequestInit) => {
      receivedSignal = init?.signal as AbortSignal;
      return new Promise<Response>((_resolve, reject) => {
        receivedSignal?.addEventListener(
          "abort",
          () => reject(new DOMException("Aborted", "AbortError")),
          { once: true },
        );
      });
    }) as typeof fetch;

    const controller = new AbortController();
    const request = api.getRunState("run-slow", { signal: controller.signal });
    controller.abort();

    await assert.rejects(request, (error: unknown) => (
      error instanceof DOMException && error.name === "AbortError"
    ));
    assert.equal(receivedSignal, controller.signal);
  });

  await suite.test("历史列表命中缓存，Run 写操作后显式失效", async (t) => {
    const originalFetch = globalThis.fetch;
    t.after(() => { globalThis.fetch = originalFetch; });

    let listCalls = 0;
    let title = "修改前";
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/openops/v1/agent-runs") && (init?.method ?? "GET") === "GET") {
        listCalls += 1;
        return jsonResponse([{
          agent_run_id: "run-history",
          agent_team_instance_id: "agent-history",
          run_status: "active",
          run_title: title,
        }]);
      }
      if (url.endsWith("/openops/v1/agent-runs/run-history:rename") && init?.method === "POST") {
        title = "修改后";
        return jsonResponse({});
      }
      throw new Error(`unexpected request: ${init?.method ?? "GET"} ${url}`);
    }) as typeof fetch;

    const first = await api.listConversations();
    const cached = await api.listConversations();
    assert.equal(first[0]?.title, "修改前");
    assert.equal(cached[0]?.title, "修改前");
    assert.equal(listCalls, 1);

    await api.renameRun("run-history", "修改后");
    const refreshed = await api.listConversations();
    assert.equal(refreshed[0]?.title, "修改后");
    assert.equal(listCalls, 2);
  });

  await suite.test("可执行 Skill 按 Agent 实例隔离缓存", async (t) => {
    const originalFetch = globalThis.fetch;
    t.after(() => { globalThis.fetch = originalFetch; });

    const calls = new Map<string, number>();
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const match = String(input).match(/agent-teams\/([^/]+)\/available-skills$/);
      if (!match?.[1]) throw new Error(`unexpected request: ${String(input)}`);
      const instanceId = match[1];
      calls.set(instanceId, (calls.get(instanceId) ?? 0) + 1);
      return jsonResponse([{
        skill_key: `skill-${instanceId}`,
        display_name: instanceId,
        source_type: "platform",
      }]);
    }) as typeof fetch;

    const [first, cached, other] = await Promise.all([
      api.getAvailableSkills("agent-a"),
      api.getAvailableSkills("agent-a"),
      api.getAvailableSkills("agent-b"),
    ]);
    assert.equal(first[0]?.skill_id, "skill-agent-a");
    assert.equal(cached[0]?.skill_id, "skill-agent-a");
    assert.equal(other[0]?.skill_id, "skill-agent-b");
    assert.equal(calls.get("agent-a"), 1);
    assert.equal(calls.get("agent-b"), 1);
  });
});
