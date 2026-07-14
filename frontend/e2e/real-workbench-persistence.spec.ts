import { expect, test } from "@playwright/test";

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

    const input = page.locator(".copilot-chat-panel textarea");
    const assistantMessages = page.locator(".oa-chat-assistant-message");
    const assistantBefore = await assistantMessages.count();
    await input.fill(question);
    await input.press("Enter");
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
});
