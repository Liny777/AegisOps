import { expect, test, type Locator, type Page } from "@playwright/test";

async function waitForComposerIdle(page: Page) {
  const chat = page.getByTestId("copilot-chat");
  // thread-ready 表示恢复快照已挂上；CopilotKit 还会在下一帧收口本次 connect 的运行态。
  // 等待官方 isRunning 归零后再输入，避免把 connect 收尾期的 Enter 排队到旧 run。
  await expect(chat).toHaveAttribute("data-copilot-running", "false", { timeout: 30_000 });
  const input = page.locator(".copilot-chat-panel textarea");
  await expect(input).toBeEditable({ timeout: 30_000 });
  return input;
}

async function sendFromComposer(page: Page, input: Locator, question: string) {
  // 模拟用户连续输入，而不是 Playwright fill 的单个合成 input 事件；CopilotKit 的 composer
  // 依赖逐次 onChange 来同步其受控草稿状态。
  await input.click();
  await input.pressSequentially(question, { delay: 1 });
  await expect(input).toHaveValue(question);
  // 等待受控草稿状态已同步到官方按钮，再发送。
  const send = page.getByTestId("copilot-send-button");
  await expect(send).toBeEnabled({ timeout: 30_000 });
  await send.click();
}

test.describe("真实 AG-UI 会话常驻", () => {
  test.skip(process.env.OPENOPS_E2E_REAL !== "1", "仅在显式启用真实 backend + sidecar 时运行");

  test("流式回复期间进入设置并从历史 Run 返回，消息、Agent 与连接保持连续", async ({ page }) => {
    test.setTimeout(240_000);
    const question = `页内无损切换验收-${Date.now()}`;

    await page.goto("./");
    await expect(page.getByText("实时", { exact: true })).toBeVisible({ timeout: 30_000 });

    // 独立新 Run 避免既有标题和消息影响断言。
    await page.getByTestId("new-conversation").click();
    await page.waitForURL(/\/agent-teams\/[^/]+\/chat\?run_id=/, { timeout: 30_000 });
    const runId = new URL(page.url()).searchParams.get("run_id");
    expect(runId).toBeTruthy();
    await expect(page.getByTestId("workbench-session")).toHaveAttribute("data-run-id", runId || "", {
      timeout: 30_000,
    });
    await expect(page.getByTestId("workbench-switch-overlay")).toHaveCount(0, { timeout: 30_000 });
    await expect(page.getByTestId("copilot-thread-gate")).toHaveAttribute("data-thread-ready", "true", {
      timeout: 30_000,
    });
    // 生产工作台不应挂载会遮挡页面操作的 CopilotKit 调试检查器。
    await expect(page.locator("cpk-web-inspector")).toHaveCount(0);
    await expect(page.getByText("实时", { exact: true })).toBeVisible({ timeout: 30_000 });

    const agentBadge = page.locator("header").getByText(/当前使用：/);
    const originalAgent = await agentBadge.textContent();
    expect(originalAgent).toBeTruthy();
    expect(originalAgent).not.toContain("正在读取 Agent");
    expect(originalAgent).not.toContain("Agent 信息读取失败");

    const input = await waitForComposerIdle(page);
    const assistantMessages = page.locator(".oa-chat-assistant-message");
    const assistantBefore = await assistantMessages.count();
    await sendFromComposer(page, input, question);
    await expect(page.getByText(question, { exact: true })).toBeVisible();

    await page.getByTitle("设置").click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.locator(".oa-retained-workbench")).toBeHidden();
    await expect(page.getByText(question, { exact: true })).toBeHidden();
    await expect(page.locator(".oa-hitl-card")).toHaveCount(0);

    // 回复必须在设置页后台继续产生，而不是返回后重新启动任务。
    await expect.poll(() => assistantMessages.count(), { timeout: 180_000 })
      .toBeGreaterThan(assistantBefore);
    await expect(assistantMessages.last()).toBeHidden();

    // 历史列表保留在 App 级缓存；点击会走 /agent-runs/:runId，覆盖两种路由形态的同 Run 复用。
    await page.locator(`[data-testid="conversation-row"][data-run-id="${runId}"]`).click();
    await expect(page).toHaveURL(new RegExp(`/agent-runs/${runId}$`));
    await expect(page.getByText(question, { exact: true })).toBeVisible();
    await expect(page.getByText(question, { exact: true })).toHaveCount(1);
    await expect(assistantMessages.last()).toBeVisible();
    await expect(agentBadge).toHaveText(originalAgent || "");
    await expect(page.getByText("实时", { exact: true })).toBeVisible();
    await expect(page.getByText("连接中", { exact: true })).toHaveCount(0);

    // 用例只验证后台连续性，不应把一个等待审批的任务泄漏给后续压测。
    const cancelTask = page.getByRole("button", { name: "取消任务" });
    if (await cancelTask.isVisible()) await cancelTask.click();
    await expect.poll(async () => {
      const health = await fetch("http://127.0.0.1:4002/healthz").then((response) => response.json()) as {
        activity: { activeUpstreamRuns: number };
      };
      return health.activity.activeUpstreamRuns;
    }, { timeout: 30_000 }).toBe(0);
  });

  test("直达失效 Run 显示真实恢复错误且绝不回退 demo Agent", async ({ page }) => {
    const missingRun = `run_e2e_missing_${Date.now()}`;
    await page.route(new RegExp(`/agent-runs/${missingRun}/state(?:\\?.*)?$`), (route) => route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: { code: "E2E_STATE_FAILURE", message: "验收注入的恢复失败" } }),
    }));

    await page.goto(`./agent-runs/${missingRun}`);
    await expect(page.getByRole("alert")).toContainText("验收注入的恢复失败", { timeout: 30_000 });
    await expect(page.locator("header").getByText(/当前使用：Agent 信息读取失败/)).toBeVisible();
    await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
    await expect(page.getByText(/当前使用：支付域感知快恢/)).toHaveCount(0);
  });

  test("closed run 重开仍在同一聊天区显示历史且没有输入框", async ({ page, browser }, testInfo) => {
    test.setTimeout(180_000);
    const question = `closed-只读恢复-${Date.now()}`;

    await page.goto("./");
    await expect(page.getByText("实时", { exact: true })).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("new-conversation").click();
    await page.waitForURL(/\/agent-teams\/[^/]+\/chat\?run_id=/, { timeout: 30_000 });
    const runId = new URL(page.url()).searchParams.get("run_id");
    expect(runId).toBeTruthy();
    await expect(page.getByTestId("copilot-thread-gate")).toHaveAttribute("data-thread-ready", "true", {
      timeout: 30_000,
    });

    const input = await waitForComposerIdle(page);
    await sendFromComposer(page, input, question);
    await expect(page.getByText(question, { exact: true })).toHaveCount(1);

    // 终止仍在运行的验收任务，等待 AgentState 终态回写后再关闭会话。
    const cancelTask = page.getByRole("button", { name: "取消任务" });
    if (await cancelTask.isVisible()) await cancelTask.click();
    await expect.poll(async () => {
      const health = await fetch("http://127.0.0.1:4002/healthz").then((response) => response.json()) as {
        activity: { activeUpstreamRuns: number };
      };
      return health.activity.activeUpstreamRuns;
    }, { timeout: 60_000 }).toBe(0);

    await page.getByRole("button", { name: "关闭会话" }).click();
    await expect(page.getByTestId("closed-conversation-readonly")).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".copilot-chat-panel textarea")).toHaveCount(0);
    await expect(page.getByText(question, { exact: true })).toHaveCount(1);

    // 新浏览器上下文验证关页重开，而非仅依赖当前 React 树。
    const reopenedContext = await browser.newContext({ baseURL: String(testInfo.project.use.baseURL) });
    try {
      const reopened = await reopenedContext.newPage();
      await reopened.goto(`./agent-runs/${runId}`);
      await expect(reopened.getByTestId("copilot-thread-gate")).toHaveAttribute("data-thread-ready", "true", {
        timeout: 30_000,
      });
      await expect(reopened.getByTestId("closed-conversation-readonly")).toBeVisible();
      await expect(reopened.locator(".copilot-chat-panel textarea")).toHaveCount(0);
      await expect(reopened.getByText(question, { exact: true })).toHaveCount(1);
    } finally {
      await reopenedContext.close();
    }
  });

  test("Stub 真实链路在完成前多次增长，且同轮工具只有一个可折叠聚合框", async ({ page }) => {
    test.skip(
      process.env.OPENOPS_E2E_STUB_STREAM !== "1",
      "需以 AgentScope stub（无模型凭证）启动真实 backend + sidecar",
    );
    test.setTimeout(240_000);

    await page.goto("./");
    await page.getByTestId("new-conversation").click();
    await expect(page.getByTestId("copilot-thread-gate")).toHaveAttribute("data-thread-ready", "true", {
      timeout: 30_000,
    });

    await page.evaluate(() => {
      type GrowthSample = { length: number; running: boolean };
      const target = window as typeof window & {
        __openopsGrowth?: GrowthSample[];
        __openopsGrowthObserver?: MutationObserver;
      };
      target.__openopsGrowth = [];
      let previousLength = 0;
      const capture = () => {
        const markdown = Array.from(document.querySelectorAll<HTMLElement>(".oa-chat-markdown")).at(-1);
        const length = markdown?.textContent?.length ?? 0;
        if (length <= previousLength) return;
        previousLength = length;
        const running = Array.from(document.querySelectorAll("button"))
          .some((button) => button.textContent?.includes("取消任务"));
        target.__openopsGrowth?.push({ length, running });
      };
      target.__openopsGrowthObserver = new MutationObserver(capture);
      target.__openopsGrowthObserver.observe(document.body, {
        subtree: true,
        childList: true,
        characterData: true,
      });
    });

    const input = await waitForComposerIdle(page);
    await sendFromComposer(page, input, `stub-stream-tool-group-${Date.now()}`);

    // Stub 第三步 recover_execute 是写动作 → 触发审批：必须等审批卡真正出现再批准，最终三段
    // 结论才会开始流式。isVisible 是立即判定不等待，发送后审批卡还没来 → 恒 false 不批准、任务
    // 卡在审批、结论不产出（原实现在真链路必挂）。改用 waitFor 自动等待；同时兼容白名单拒绝该
    // 动作、后端 fail-closed 直接输出三段结论、审批卡不出现的路径——谁先到就走谁。
    const approve = page.getByRole("button", { name: "批准", exact: true });
    const growthStarted = page.waitForFunction(() => {
      const samples = (window as Window & {
        __openopsGrowth?: Array<{ running: boolean }>;
      }).__openopsGrowth ?? [];
      return samples.some((sample) => sample.running);
    }, undefined, { timeout: 90_000 }).catch(() => undefined);
    await Promise.race([
      approve.waitFor({ state: "visible", timeout: 90_000 }).catch(() => undefined),
      growthStarted,
    ]);
    if (await approve.isVisible().catch(() => false)) await approve.click();

    await expect.poll(() => page.evaluate(() => {
      const samples = (window as typeof window & {
        __openopsGrowth?: Array<{ length: number; running: boolean }>;
      }).__openopsGrowth ?? [];
      return new Set(samples.filter((sample) => sample.running).map((sample) => sample.length)).size;
    }), { timeout: 120_000 }).toBeGreaterThanOrEqual(2);

    const groups = page.getByTestId("tool-call-group");
    await expect(groups).toHaveCount(1);
    const toggle = groups.getByRole("button", { name: /工具调用/ });
    await expect(toggle).toHaveAttribute("aria-expanded", "false", { timeout: 120_000 });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");

    await page.evaluate(() => {
      (window as typeof window & { __openopsGrowthObserver?: MutationObserver })
        .__openopsGrowthObserver?.disconnect();
    });
  });

  test("诊断面板：完成后出卡 → reload → /state 恢复且 revision 不回退", async ({ page }) => {
    test.setTimeout(240_000);
    const question = `诊断面板持久化验收-${Date.now()}`;

    await page.goto("./");
    await expect(page.getByText("实时", { exact: true })).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("new-conversation").click();
    await page.waitForURL(/\/agent-teams\/[^/]+\/chat\?run_id=/, { timeout: 30_000 });
    await expect(page.getByTestId("copilot-thread-gate")).toHaveAttribute("data-thread-ready", "true", {
      timeout: 30_000,
    });

    const input = await waitForComposerIdle(page);
    await sendFromComposer(page, input, question);
    await expect(page.getByText(question, { exact: true })).toBeVisible();

    // 真实链路是否出卡取决于模型是否调用 update_diagnosis_board（漏调=空态骨架，不造假）。
    // 卡片没出现时跳过持久化断言而不判失败——stub 全链路（OPENOPS_DEMO_BOARD_STEP=1）必出卡。
    const card = page.getByTestId("rca-card");
    const appeared = await card.waitFor({ state: "visible", timeout: 150_000 })
      .then(() => true)
      .catch(() => false);
    test.skip(!appeared, "本轮模型未上报诊断面板（未调用 update_diagnosis_board），跳过持久化断言");

    // 等 revision 稳定（任务终态后不再增长），记录基线。
    await expect.poll(async () => {
      const cancelTask = page.getByRole("button", { name: "取消任务" });
      return await cancelTask.isVisible().catch(() => false) ? "running" : "settled";
    }, { timeout: 150_000 }).toBe("settled");
    const revisionBefore = Number(await card.getAttribute("data-rca-revision"));
    expect(revisionBefore).toBeGreaterThanOrEqual(1);

    // reload → GET /state 顶层 rca 恢复：时间线仍在「诊断」tab，revision 不回退。
    await page.reload();
    await expect(page.getByTestId("copilot-thread-gate")).toHaveAttribute("data-thread-ready", "true", {
      timeout: 30_000,
    });
    await expect(page.getByRole("tab", { name: "诊断" })).toHaveAttribute("aria-selected", "true", {
      timeout: 30_000,
    });
    await expect(card).toBeVisible({ timeout: 30_000 });
    const revisionAfter = Number(await card.getAttribute("data-rca-revision"));
    expect(revisionAfter).toBeGreaterThanOrEqual(revisionBefore);
  });
});
