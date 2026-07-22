import { expect, test, type Page } from "@playwright/test";

/**
 * 诊断五步法 · 右侧宽面板「诊断」tab 的垂直时间线（mock 模式）。
 *
 * mock 数据形状：demo rca = 进行中（第 4 步 · 验证，revision 4，步 1-4 带 summary）；
 * mock send 两段式（发送即进行中 → 900ms 后 mockRcaFinal：五步全 done、status=concluded、
 * revision 5、phaseLabel「诊断完成」）。完成态判定只认 data-rca-status="concluded"
 * （后端权威信号的 mock 对位）。定位纪律：一律 role/testid，不裸 getByText("诊断")。
 */

const rail = (page: Page) => page.getByLabel("活动 · 调查时间线");
const rcaCard = (page: Page) => page.getByTestId("rca-card");
const rcaTab = (page: Page) => page.getByRole("tab", { name: "诊断" });
const progress = (page: Page) => page.getByTestId("diagnosis-progress");

async function gotoWorkbench(page: Page) {
  await page.goto("/");
  await expect(page.getByText("活动 · 调查时间线")).toBeVisible({ timeout: 15_000 });
}

test("载入即诊断 tab + 宽面板 + 进度行当前步正确", async ({ page }) => {
  await gotoWorkbench(page);
  // 诊断优先：mock 自带诊断数据，载入自动落在「诊断」tab
  await expect(rcaTab(page)).toHaveAttribute("aria-selected", "true");
  await expect(rcaCard(page)).toBeVisible();
  // 宽面板：默认占内容区（对话+右栏 flex 行）1/3、下限 420px 上限 760px——对话区占 2/3。
  // 期望值按 rail 父容器实宽推算，不依赖具体视口/侧栏宽度。
  const box = await rail(page).boundingBox();
  expect(box).toBeTruthy();
  const rowWidth = await rail(page).evaluate((el) => el.parentElement!.getBoundingClientRect().width);
  const expected = Math.min(760, Math.max(420, rowWidth / 3));
  expect(Math.abs(box!.width - expected)).toBeLessThan(3);
  // 进度行：demo 处于第 4 步（验证），role=status 可读 + 可见文本 4/5
  await expect(progress(page)).toHaveAttribute("aria-label", "诊断进度：第4步 · 验证");
  await expect(progress(page)).toHaveText(/诊断进度 4\/5/);
  await expect(rcaCard(page)).toHaveAttribute("data-rca-status", "in_progress");
});

test("时间线结构：active 步默认展开、收起步显每步摘要、waiting 步不可点", async ({ page }) => {
  await gotoWorkbench(page);
  const card = rcaCard(page);
  // 步 4（active）默认展开：进行中徽标 + 跨步字段 currentQ 渲染在展开卡内
  await expect(card.getByTestId("diagnosis-step-4")).toHaveAttribute("aria-expanded", "true");
  await expect(card.getByText("进行中", { exact: true })).toBeVisible();
  await expect(card.getByText(/Redis 连接饱和是慢查询导致，还是连接泄漏导致/)).toBeVisible();
  // 步 1-3（done）默认收起：一行摘要来自 mock steps[].summary
  await expect(card.getByTestId("diagnosis-step-1")).toHaveAttribute("aria-expanded", "false");
  await expect(card.getByTestId("diagnosis-step-2")).toHaveAttribute("aria-expanded", "false");
  await expect(card.getByTestId("diagnosis-step-3")).toHaveAttribute("aria-expanded", "false");
  await expect(card.getByText("范围锁定支付核心域（APP-A/B/C），主症状为下单 P99 突增")).toBeVisible();
  await expect(card.getByText("指标与日志确认 Redis active 连接打满，同期无发布无变更")).toBeVisible();
  await expect(card.getByText("生成 H1 连接泄漏 / H2 慢查询 / H3 流量突增三个假设")).toBeVisible();
  // 步 5（waiting）不可点：纯 div，不是按钮；且不显示任何摘要——结论/假设是跨步全局
  // 字段，未开始的步渲染它们会伪装成已有产出
  await expect(card.getByTestId("diagnosis-step-5")).toBeVisible();
  await expect(card.getByRole("button", { name: /根因报告/ })).toHaveCount(0);
  await expect(card.getByTestId("diagnosis-step-5").getByText(/根因倾向/)).toHaveCount(0);
});

test("点步 2 展开证据（facts/证据源可见），再点收起", async ({ page }) => {
  await gotoWorkbench(page);
  const card = rcaCard(page);
  const step2 = card.getByTestId("diagnosis-step-2");
  await step2.click();
  await expect(step2).toHaveAttribute("aria-expanded", "true");
  await expect(card.locator(".oa-rca-facts")).toBeVisible();
  await expect(card.getByText("svc-payment-api → Redis active 连接打满（1000/1000）")).toBeVisible();
  await expect(card.getByText("证据源状态")).toBeVisible();
  await step2.click();
  await expect(step2).toHaveAttribute("aria-expanded", "false");
  await expect(card.locator(".oa-rca-facts")).toHaveCount(0);
});

test("发送后按钮 disabled；闭环后「诊断完成 5/5」、五点全绿、步 5 自动展开、footer 隐藏", async ({ page }) => {
  await gotoWorkbench(page);
  const continueButton = page.getByRole("button", { name: /继续验证 H1/ });
  await expect(continueButton).toBeEnabled();

  const input = page.getByPlaceholder(/描述你的排障任务/);
  await input.fill("支付延迟继续排查");
  await input.press("Enter");
  // 任务运行中（mock 900ms 窗口）：时间线按钮必须禁用，防双发
  await expect(continueButton).toBeDisabled({ timeout: 800 });

  await expect(page.getByText(/任务已受理/)).toBeVisible({ timeout: 10_000 });
  // 完成态：status=concluded（权威信号）→ 进度行定格、相位 chip「诊断完成」
  const card = rcaCard(page);
  await expect(card).toHaveAttribute("data-rca-status", "concluded", { timeout: 5_000 });
  await expect(progress(page)).toHaveAttribute("aria-label", "诊断进度：已完成");
  await expect(progress(page)).toHaveText(/诊断完成 5\/5/);
  // exact：进度行还有「诊断完成 5/5」，子串匹配会命中两处触发 strict mode
  await expect(card.getByText("诊断完成", { exact: true })).toBeVisible();
  // 五点全绿（五步全 done）+ 步 5 自动展开显示最终结论
  await expect(card.locator('li[data-step-state="done"]')).toHaveCount(5);
  await expect(card.getByTestId("diagnosis-step-5")).toHaveAttribute("aria-expanded", "true");
  await expect(card.getByText("最终结论")).toBeVisible();
  await expect(card.getByText(/根因 H1（连接泄漏）已确认/)).toBeVisible();
  // footer 整块隐藏（无可用动作）
  await expect(page.getByRole("button", { name: /继续验证/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "采纳并生成恢复动作" })).toHaveCount(0);
});

test("点「继续验证 H1」以用户身份发消息：聊天区出现用户气泡", async ({ page }) => {
  await gotoWorkbench(page);
  await page.getByRole("button", { name: /继续验证 H1/ }).click();
  // 消息可见可审计：进入对话流成为 user 气泡（mock 路径直接 send()）
  await expect(
    page.locator(".oa-fallback-chat-list").getByText(/继续验证假设 H1/),
  ).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText(/任务已受理/)).toBeVisible({ timeout: 10_000 });
  await expect(rcaCard(page)).toHaveAttribute("data-rca-status", "concluded", { timeout: 5_000 });
});

test("手动切到子 Agent 后诊断更新出未读徽标，回到诊断即清除", async ({ page }) => {
  await gotoWorkbench(page);
  await page.getByRole("tab", { name: "子 Agent" }).click();
  await expect(page.getByLabel("有新的诊断更新")).toHaveCount(0);

  const input = page.getByPlaceholder(/描述你的排障任务/);
  await input.fill("再查一轮");
  await input.press("Enter");
  await expect(page.getByText(/任务已受理/)).toBeVisible({ timeout: 10_000 });
  // 完成态 revision 5 > 已读基线 4 且不在诊断 tab → 徽标点亮
  await expect(page.getByLabel("有新的诊断更新")).toBeVisible({ timeout: 5_000 });
  // 手动切走后不自动抢 tab（manualTabChoice）
  await expect(page.getByRole("tab", { name: "子 Agent" })).toHaveAttribute("aria-selected", "true");

  await rcaTab(page).click();
  await expect(page.getByLabel("有新的诊断更新")).toHaveCount(0);
});

test("切会话无旧 run 残留：闭环时间线不带进新会话", async ({ page }) => {
  await gotoWorkbench(page);
  const input = page.getByPlaceholder(/描述你的排障任务/);
  await input.fill("先把这轮诊断推进到闭环");
  await input.press("Enter");
  await expect(rcaCard(page)).toHaveAttribute("data-rca-status", "concluded", { timeout: 10_000 });

  // 侧栏历史会话 → /agent-runs/:id：mock 下 Workbench 重置 demo 态，ActivityRail 随 key 重挂
  await page.locator('[data-testid="conversation-row"][data-run-id="conv_2"]').click();
  await expect(page).toHaveURL(/\/agent-runs\/conv_2$/);
  await expect(rcaTab(page)).toHaveAttribute("aria-selected", "true");
  await expect(rcaCard(page)).toHaveAttribute("data-rca-status", "in_progress", { timeout: 10_000 });
  await expect(rail(page).getByText("诊断完成", { exact: true })).toHaveCount(0);
  await expect(page.getByText("先把这轮诊断推进到闭环")).toHaveCount(0);
});

test("拖拽调宽生效并持久化（localStorage + 刷新恢复）", async ({ page }) => {
  await gotoWorkbench(page);
  const handle = page.locator(".oa-rail-resize-handle");
  await expect(handle).toBeVisible();
  const before = (await rail(page).boundingBox())!;

  const handleBox = (await handle.boundingBox())!;
  const startX = handleBox.x + handleBox.width / 2;
  const startY = handleBox.y + 200;
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(startX - 150, startY, { steps: 6 });
  await page.mouse.up();

  // 左拖 150px → 变宽约 150px（clamp [420, 760] 内）
  await expect.poll(async () => (await rail(page).boundingBox())!.width)
    .toBeGreaterThan(before.width + 100);
  const widened = (await rail(page).boundingBox())!.width;
  expect(widened).toBeLessThanOrEqual(760);
  const stored = await page.evaluate(() => window.localStorage.getItem("openops.activityRail.width"));
  expect(Number(stored)).toBeGreaterThan(before.width + 100);

  await page.reload();
  await expect(page.getByText("活动 · 调查时间线")).toBeVisible({ timeout: 15_000 });
  await expect.poll(async () => (await rail(page).boundingBox())!.width)
    .toBeGreaterThan(before.width + 100);
});
