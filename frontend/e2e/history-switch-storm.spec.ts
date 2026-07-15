import { expect, test, type BrowserContext, type Page, type Request } from "@playwright/test";

const isReal = process.env.OPENOPS_E2E_REAL === "1";
const runLoad = process.env.OPENOPS_E2E_LOAD === "1";

interface RequestMetrics {
  stateCalls: number;
  historyCalls: number;
  runtimeInfoCalls: number;
  copilotMethods: Record<string, number>;
  failed: Array<{ url: string; error: string }>;
  serverErrors: Array<{ url: string; status: number }>;
  restDurations: number[];
  activeOpenOpsSse: Set<Request>;
  activeCopilotRequests: Set<Request>;
  copilotRequests: CopilotRequestRecord[];
}

interface CopilotRequestRecord {
  method: string;
  threadId: string | null;
  outcome: "pending" | "finished" | "failed";
  error?: string;
}

interface SidecarHealth {
  activity: {
    activeHttpRequests: number;
    activeBrowserStreams: number;
    activeUpstreamRuns: number;
    sharedConnects: number;
  };
  resources: {
    heapUsedBytes: number;
    rssBytes: number;
    openFileDescriptors: number | null;
    eventLoopLagMeanMs: number;
    eventLoopLagMaxMs: number;
  };
}

const sidecarHealthUrl = process.env.OPENOPS_E2E_SIDECAR_HEALTH_URL ??
  "http://127.0.0.1:4002/healthz";

async function readSidecarHealth(): Promise<SidecarHealth> {
  return fetch(sidecarHealthUrl).then((response) => {
    if (!response.ok) throw new Error(`sidecar health failed: HTTP ${response.status}`);
    return response.json() as Promise<SidecarHealth>;
  });
}

async function waitForSidecarIdle(timeoutMs = 20_000): Promise<SidecarHealth> {
  const deadline = Date.now() + timeoutMs;
  let latest = await readSidecarHealth();
  while (Date.now() < deadline) {
    if (Object.values(latest.activity).every((count) => count === 0)) return latest;
    await new Promise((resolve) => setTimeout(resolve, 100));
    latest = await readSidecarHealth();
  }
  throw new Error(`sidecar did not return to idle: ${JSON.stringify(latest.activity)}`);
}

async function sampleIdleResourceLowWatermark(sampleCount = 20): Promise<SidecarHealth> {
  const samples: SidecarHealth[] = [await waitForSidecarIdle()];
  for (let index = 1; index < sampleCount; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 100));
    const sample = await readSidecarHealth();
    if (!Object.values(sample.activity).every((count) => count === 0)) {
      samples.push(await waitForSidecarIdle());
    } else {
      samples.push(sample);
    }
  }
  // 单次 process.memoryUsage() 会随 V8 GC 相位产生明显锯齿。采用空闲窗口的
  // 低分位，既不依赖测试专用 --expose-gc，也不会把一次 GC 前采样误判为泄漏。
  return [...samples].sort((left, right) =>
    left.resources.heapUsedBytes - right.resources.heapUsedBytes
  )[Math.floor((samples.length - 1) * 0.2)]!;
}

function expectResourceBelowBaselineCeiling(
  current: SidecarHealth["resources"],
  baseline: SidecarHealth["resources"],
): void {
  const heapGrowth = Math.max(0, current.heapUsedBytes - baseline.heapUsedBytes) /
    Math.max(1, baseline.heapUsedBytes);
  expect(heapGrowth, "heap 不得超过预热基线 10%").toBeLessThanOrEqual(0.1);
  if (baseline.openFileDescriptors !== null && current.openFileDescriptors !== null) {
    const allowedFdDrift = Math.max(1, Math.ceil(baseline.openFileDescriptors * 0.1));
    expect(
      Math.max(0, current.openFileDescriptors - baseline.openFileDescriptors),
      "FD 不得超过预热基线 10%",
    ).toBeLessThanOrEqual(allowedFdDrift);
  }
}

function observeRequests(page: Page): RequestMetrics {
  const started = new Map<Request, number>();
  const copilotByRequest = new Map<Request, CopilotRequestRecord>();
  const metrics: RequestMetrics = {
    stateCalls: 0,
    historyCalls: 0,
    runtimeInfoCalls: 0,
    copilotMethods: {},
    failed: [],
    serverErrors: [],
    restDurations: [],
    activeOpenOpsSse: new Set(),
    activeCopilotRequests: new Set(),
    copilotRequests: [],
  };

  page.on("request", (request) => {
    started.set(request, performance.now());
    const url = new URL(request.url());
    if (/\/agent-runs\/[^/]+\/state$/.test(url.pathname)) metrics.stateCalls += 1;
    if (/\/agent-runs$/.test(url.pathname) && request.method() === "GET") metrics.historyCalls += 1;
    if (url.pathname.includes("/events/stream")) metrics.activeOpenOpsSse.add(request);
    if (url.pathname.endsWith("/api/copilotkit")) {
      metrics.activeCopilotRequests.add(request);
      try {
        const body = request.postDataJSON() as {
          method?: unknown;
          threadId?: unknown;
          body?: { threadId?: unknown };
          params?: { threadId?: unknown; input?: { threadId?: unknown } };
        } | null;
        const method = String(body?.method ?? "");
        const candidateThreadId = body?.body?.threadId ?? body?.params?.threadId ??
          body?.params?.input?.threadId ?? body?.threadId;
        const record: CopilotRequestRecord = {
          method: method || "unknown",
          threadId: typeof candidateThreadId === "string" ? candidateThreadId : null,
          outcome: "pending",
        };
        metrics.copilotRequests.push(record);
        copilotByRequest.set(request, record);
        metrics.copilotMethods[method || "unknown"] = (metrics.copilotMethods[method || "unknown"] ?? 0) + 1;
        if (method === "info" || method === "runtime/info") metrics.runtimeInfoCalls += 1;
      } catch {
        const record: CopilotRequestRecord = { method: "unknown", threadId: null, outcome: "pending" };
        metrics.copilotRequests.push(record);
        copilotByRequest.set(request, record);
      }
    }
  });
  page.on("requestfinished", (request) => {
    metrics.activeOpenOpsSse.delete(request);
    metrics.activeCopilotRequests.delete(request);
    const begin = started.get(request);
    started.delete(request);
    if (begin === undefined) return;
    const url = new URL(request.url());
    if (url.pathname.endsWith("/api/copilotkit")) {
      const record = copilotByRequest.get(request);
      if (record) record.outcome = "finished";
      copilotByRequest.delete(request);
      return;
    }
    if (url.pathname.includes("/events/stream")) return;
    if (url.pathname.includes("/api/openops/")) metrics.restDurations.push(performance.now() - begin);
  });
  page.on("requestfailed", (request) => {
    metrics.activeOpenOpsSse.delete(request);
    metrics.activeCopilotRequests.delete(request);
    started.delete(request);
    const failure = {
      url: request.url(),
      error: request.failure()?.errorText ?? "unknown",
    };
    metrics.failed.push(failure);
    if (new URL(request.url()).pathname.endsWith("/api/copilotkit")) {
      const record = copilotByRequest.get(request);
      if (record) {
        record.outcome = "failed";
        record.error = failure.error;
      }
      copilotByRequest.delete(request);
    }
  });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (
      response.status() >= 500 &&
      (url.pathname.includes("/api/openops/") || url.pathname.endsWith("/api/copilotkit"))
    ) {
      metrics.serverErrors.push({ url: response.url(), status: response.status() });
    }
  });
  return metrics;
}

function expectCopilotRequestsConverged(metrics: RequestMetrics, finalRunId: string): void {
  const failed = metrics.copilotRequests.filter((request) => request.outcome === "failed");
  expect(failed.filter((request) => (
    request.method !== "agent/connect" || request.error !== "net::ERR_ABORTED"
  ))).toEqual([]);

  const finalConnects = metrics.copilotRequests.filter((request) => (
    request.method === "agent/connect" && request.threadId === finalRunId
  ));
  expect(finalConnects.length).toBeGreaterThan(0);
  expect(finalConnects.at(-1)?.outcome).not.toBe("failed");
}

function percentile(values: number[], quantile: number): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * quantile) - 1)] ?? 0;
}

async function waitForReady(page: Page): Promise<void> {
  await page.goto("./");
  await expect(page.getByTestId("conversation-row").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("workbench-session")).toHaveCount(1);
  await expect(page.getByText("实时", { exact: true })).toBeVisible({ timeout: 30_000 });
}

async function burst(
  page: Page,
  count: number,
  intervalMs: number,
  finalRowHint = 7,
): Promise<string> {
  const rows = page.getByTestId("conversation-row");
  const rowCount = await rows.count();
  expect(rowCount).toBeGreaterThanOrEqual(8);
  const selectableRows = Math.min(8, rowCount);
  const finalIndex = ((finalRowHint % selectableRows) + selectableRows) % selectableRows;
  const finalRunId = await rows.nth(finalIndex).getAttribute("data-run-id");
  expect(finalRunId).toBeTruthy();

  const blankDetected = await page.evaluate(async ({ total, interval, finalRow, availableRows }) => {
    let blank = false;
    const sample = () => {
      const main = document.querySelector("main");
      const hosts = document.querySelectorAll(
        '[data-testid="workbench-session"], [data-testid="workbench-cold-start"]',
      );
      if (!main || hosts.length !== 1 || !(main as HTMLElement).offsetParent) blank = true;
    };
    const sampler = window.setInterval(sample, 16);
    try {
      for (let index = 0; index < total; index += 1) {
        const rowIndex = index === total - 1 ? finalRow : index % Math.min(8, availableRows);
        const currentRows = document.querySelectorAll<HTMLElement>('[data-testid="conversation-row"]');
        currentRows[rowIndex]?.click();
        await new Promise((resolve) => window.setTimeout(resolve, interval));
      }
      sample();
      return blank;
    } finally {
      window.clearInterval(sampler);
    }
  }, { total: count, interval: intervalMs, finalRow: finalIndex, availableRows: rowCount });
  expect(blankDetected).toBe(false);
  await expect(page).toHaveURL(new RegExp(`/agent-runs/${finalRunId}$`));
  return finalRunId!;
}

test.describe("真实历史会话快速切换", () => {
  test.skip(!isReal, "仅在真实 backend + sidecar 链路运行");

  test("50 次连点只初始化最后会话且页面不白屏", async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    const metrics = observeRequests(page);
    await waitForReady(page);
    const baseline = { ...metrics };

    const finalRunId = await burst(page, 50, 25);
    const settleStartedAt = performance.now();
    await expect(page.getByTestId("workbench-session")).toHaveAttribute("data-run-id", finalRunId, {
      timeout: 10_000,
    });
    await expect(page.getByTestId("workbench-switch-overlay")).toHaveCount(0, { timeout: 10_000 });
    await expect(page.getByText("实时", { exact: true })).toBeVisible({ timeout: 10_000 });
    const finalInteractiveMs = performance.now() - settleStartedAt;
    await expect(page.locator("header").getByText(/当前使用：/)).not.toContainText("正在读取 Agent");
    await expect(page.getByTestId("workbench-session")).toBeVisible();

    const burstStateCalls = metrics.stateCalls - baseline.stateCalls;
    const burstHistoryCalls = metrics.historyCalls - baseline.historyCalls;
    expect(burstStateCalls).toBe(1);
    expect(burstHistoryCalls).toBe(0);
    // 每个 resolved run 有独立 Provider；latest-wins 后只允许最终 Provider
    // 额外读取一次 runtime info，旧 Provider 的首次读取计入 baseline。
    expect(metrics.runtimeInfoCalls - baseline.runtimeInfoCalls).toBeLessThanOrEqual(1);
    expect(metrics.activeOpenOpsSse.size).toBeLessThanOrEqual(1);
    expect(metrics.activeCopilotRequests.size).toBeLessThanOrEqual(1);
    expectCopilotRequestsConverged(metrics, finalRunId);
    expect(metrics.serverErrors).toEqual([]);
    expect(pageErrors).toEqual([]);

    const health = await readSidecarHealth();
    expect(health.activity.activeBrowserStreams).toBeLessThanOrEqual(2);
    expect(health.activity.sharedConnects).toBeLessThanOrEqual(1);

    const report = {
      finalRunId,
      burstStateCalls,
      burstHistoryCalls,
      runtimeInfoCalls: metrics.runtimeInfoCalls - baseline.runtimeInfoCalls,
      copilotMethods: metrics.copilotMethods,
      failedRequests: metrics.failed,
      activeOpenOpsSse: metrics.activeOpenOpsSse.size,
      activeCopilotRequests: metrics.activeCopilotRequests.size,
      restP50Ms: percentile(metrics.restDurations, 0.5),
      restP95Ms: percentile(metrics.restDurations, 0.95),
      restP99Ms: percentile(metrics.restDurations, 0.99),
      finalInteractiveMs,
      health: health.activity,
    };
    console.log(`[history-switch-metrics] ${JSON.stringify(report)}`);
    expect(report.restP95Ms).toBeLessThan(500);
    expect(report.restP99Ms).toBeLessThan(1_000);
    expect(finalInteractiveMs).toBeLessThan(1_000);
    await testInfo.attach("history-switch-metrics", {
      body: Buffer.from(JSON.stringify(report, null, 2)),
      contentType: "application/json",
    });
  });

  test("从设置页冷进入时主区始终有骨架且 burst 仍只初始化最后会话", async ({ page }) => {
    test.setTimeout(120_000);
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    const metrics = observeRequests(page);

    await page.goto("./settings");
    await expect(page.getByTestId("conversation-row").first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("workbench-session")).toHaveCount(0);
    const baselineStateCalls = metrics.stateCalls;

    // 首次路由变化的同一帧就必须有稳定主区，不能等待 120ms 调度结束才出现内容。
    await page.getByTestId("conversation-row").first().click();
    await expect(page.getByTestId("workbench-cold-start")).toBeVisible();

    const finalRunId = await burst(page, 50, 25);
    await expect(page.getByTestId("workbench-session")).toHaveAttribute("data-run-id", finalRunId, {
      timeout: 10_000,
    });
    await expect(page.getByTestId("workbench-cold-start")).toHaveCount(0);
    await expect(page.getByText("实时", { exact: true })).toBeVisible({ timeout: 10_000 });

    expect(metrics.stateCalls - baselineStateCalls).toBe(1);
    expectCopilotRequestsConverged(metrics, finalRunId);
    expect(metrics.serverErrors).toEqual([]);
    expect(pageErrors).toEqual([]);
  });

  test("运行中切到其他历史 Run 后任务继续，返回原 Run 可接续审批", async ({ page }) => {
    test.skip(runLoad, "压力矩阵单独运行，不重复启动长任务");
    test.setTimeout(120_000);
    const question = `后台运行切换验收-${Date.now()}`;
    const copilotMethods: string[] = [];
    page.on("request", (request) => {
      if (!new URL(request.url()).pathname.endsWith("/api/copilotkit")) return;
      try {
        const method = String((request.postDataJSON() as { method?: unknown } | null)?.method ?? "");
        if (method) copilotMethods.push(method);
      } catch {
        // 非 JSON 请求不影响 stop 断言。
      }
    });

    await waitForReady(page);
    await page.getByTestId("new-conversation").click();
    await page.waitForURL(/\/agent-teams\/[^/]+\/chat\?run_id=/, { timeout: 30_000 });
    const sourceRunId = new URL(page.url()).searchParams.get("run_id");
    expect(sourceRunId).toBeTruthy();
    await expect(page.getByTestId("workbench-session")).toHaveAttribute("data-run-id", sourceRunId!, {
      timeout: 30_000,
    });
    await expect(page.getByTestId("copilot-thread-gate")).toHaveAttribute("data-thread-ready", "true", {
      timeout: 30_000,
    });

    const otherRunId = await page.getByTestId("conversation-row").evaluateAll((rows, current) =>
      rows.map((row) => row.getAttribute("data-run-id")).find((id) => id && id !== current) ?? null,
    sourceRunId);
    expect(otherRunId).toBeTruthy();

    const input = page.locator(".copilot-chat-panel textarea");
    await input.fill(question);
    await input.press("Enter");
    await expect(page.getByText(question, { exact: true })).toBeVisible();
    await expect.poll(async () => (await readSidecarHealth()).activity.activeUpstreamRuns, {
      timeout: 15_000,
    }).toBeGreaterThan(0);

    await page.locator(`[data-testid="conversation-row"][data-run-id="${otherRunId}"]`).click();
    await expect(page.getByTestId("workbench-session")).toHaveAttribute("data-run-id", otherRunId!, {
      timeout: 15_000,
    });
    await expect(page.getByTestId("workbench-switch-overlay")).toHaveCount(0, { timeout: 15_000 });
    await expect(page.getByText(question, { exact: true })).toHaveCount(0);
    // 离开源 Run 只脱离浏览器消费者；底层任务仍继续，且不得隐式发 stop。
    await new Promise((resolve) => setTimeout(resolve, 700));
    expect((await readSidecarHealth()).activity.activeUpstreamRuns).toBeGreaterThan(0);
    expect(copilotMethods).not.toContain("agent/stop");

    await page.locator(`[data-testid="conversation-row"][data-run-id="${sourceRunId}"]`).click();
    await expect(page.getByTestId("workbench-session")).toHaveAttribute("data-run-id", sourceRunId!, {
      timeout: 15_000,
    });
    await expect(page.getByTestId("copilot-thread-gate")).toHaveAttribute("data-thread-ready", "true", {
      timeout: 15_000,
    });
    await expect(page.locator(".oa-hitl-card")).toBeVisible({ timeout: 60_000 });
    await expect(page.locator(".oa-chat-user-message").getByText(question, { exact: true }))
      .toHaveCount(1);
    expect(copilotMethods).not.toContain("agent/stop");

    await page.locator(".oa-hitl-card").getByRole("button", { name: "拒绝" }).click();
    await expect.poll(async () => (await readSidecarHealth()).activity.activeUpstreamRuns, {
      timeout: 30_000,
    }).toBe(0);

    // 新 run 必须得到全新的 Provider/Agent 生命周期，不能继承源会话消息。
    await page.getByTestId("new-conversation").click();
    await page.waitForURL(/\/agent-teams\/[^/]+\/chat\?run_id=/, { timeout: 30_000 });
    await expect(page.getByTestId("copilot-thread-gate")).toHaveAttribute("data-thread-ready", "true", {
      timeout: 30_000,
    });
    await expect(page.getByText(question, { exact: true })).toHaveCount(0);
  });

  test("三轮 1/3/5 浏览器上下文三档压力回归", async ({ browser }, testInfo) => {
    test.skip(!runLoad, "设置 OPENOPS_E2E_LOAD=1 才运行本地压力矩阵");
    test.setTimeout(300_000);
    const baseURL = String(testInfo.project.use.baseURL);
    const report: Array<Record<string, unknown>> = [];
    const roundResources: Array<Record<string, unknown>> = [];
    let preheatedResources: SidecarHealth["resources"] | null = null;

    // 第一轮完整矩阵负责预热 V8/CopilotKit；第二、三轮复用相同的 5 个身份，
    // 避免把有意增加 owner/thread 基数造成的缓存扩张误判为连接泄漏。
    await waitForSidecarIdle();

    for (const round of [1, 2, 3]) {
      for (const users of [1, 3, 5]) {
        for (const intervalMs of [25, 75, 150]) {
          const contexts: BrowserContext[] = [];
          const pages: Page[] = [];
          let scenarioResult: Record<string, unknown> | null = null;
          try {
            for (let index = 0; index < users; index += 1) {
              const context = await browser.newContext({ baseURL });
              // 前端真实 IAM 模式仍会携带固定 mock 头；固定且彼此不同的 cookie
              // 同时验证 owner 隔离，并让三轮测量保持相同的 owner/thread 基数。
              await context.addCookies([{
                name: "openops_e2e_owner",
                value: `history-load-user-${index}`,
                url: new URL(baseURL).origin,
                sameSite: "Lax",
              }]);
              const page = await context.newPage();
              contexts.push(context);
              pages.push(page);
            }
            const errors = pages.map(() => [] as string[]);
            pages.forEach((page, index) =>
              page.on("pageerror", (error) => errors[index]?.push(error.message)));
            const metrics = pages.map(observeRequests);
            await Promise.all(pages.map(waitForReady));
            const baselines = metrics.map((item) => item.stateCalls);
            const baselineRestCounts = metrics.map((item) => item.restDurations.length);
            const startedAt = performance.now();
            const finalRuns = await Promise.all(
              pages.map((page, index) => burst(page, 50, intervalMs, index)),
            );
            expect(new Set(finalRuns).size, "并发用户必须落到不同的最终 Run").toBe(users);
            const settleStartedAt = performance.now();
            await Promise.all(pages.map((page, index) => expect(page.getByTestId("workbench-session"))
              .toHaveAttribute("data-run-id", finalRuns[index]!, { timeout: 10_000 })));
            await Promise.all(pages.map((page) => expect(page.getByText("实时", { exact: true }))
              .toBeVisible({ timeout: 10_000 })));
            const finalInteractiveMs = performance.now() - settleStartedAt;
            expect(finalInteractiveMs).toBeLessThan(1_000);
            const elapsedMs = performance.now() - startedAt;
            const stateCalls = metrics.map((item, index) => item.stateCalls - (baselines[index] ?? 0));
            const restDurations = metrics.flatMap((item, index) =>
              item.restDurations.slice(baselineRestCounts[index] ?? 0));
            const restP95Ms = percentile(restDurations, 0.95);
            const restP99Ms = percentile(restDurations, 0.99);
            // latest-wins 只解析最终目标；若最终目标就是页面当前 Run，正确行为是
            // 完全不发 /state，否则只允许最终 Run 的一次初始化。
            if (intervalMs < 120) {
              expect(stateCalls.every((calls) => calls === 0 || calls === 1)).toBe(true);
            } else {
              expect(stateCalls.every((calls) => calls >= 1 && calls <= 50)).toBe(true);
            }
            expect(restP95Ms).toBeLessThan(500);
            expect(restP99Ms).toBeLessThan(1_000);
            expect(errors.flat()).toEqual([]);
            metrics.forEach((item, index) => {
              expectCopilotRequestsConverged(item, finalRuns[index]!);
              expect(item.serverErrors).toEqual([]);
            });
            const unexpectedTimeouts = metrics.flatMap((item) => item.failed).filter((failure) =>
              /timed?out|timeout/i.test(failure.error));
            expect(unexpectedTimeouts).toEqual([]);

            const health = await readSidecarHealth();
            expect(health.activity.activeBrowserStreams).toBeLessThanOrEqual(users * 2);
            scenarioResult = {
              round,
              users,
              intervalMs,
              elapsedMs,
              finalInteractiveMs,
              stateCalls,
              restP95Ms,
              restP99Ms,
              finalRuns,
              activeHealth: health.activity,
            };
          } finally {
            await Promise.all(contexts.map((context) => context.close()));
          }

          const settledHealth = await waitForSidecarIdle();
          report.push({
            ...scenarioResult,
            settledHealth: settledHealth.activity,
            settledResources: settledHealth.resources,
          });
        }
      }

      const settledRound = await sampleIdleResourceLowWatermark();
      if (preheatedResources === null) preheatedResources = settledRound.resources;
      else expectResourceBelowBaselineCeiling(settledRound.resources, preheatedResources);
      roundResources.push({ round, resources: settledRound.resources });
    }

    const loadReport = { scenarios: report, roundResources };
    await testInfo.attach("history-switch-load-matrix", {
      body: Buffer.from(JSON.stringify(loadReport, null, 2)),
      contentType: "application/json",
    });
    console.log(`[history-switch-load-matrix] ${JSON.stringify(loadReport)}`);
  });
});
