import { expect, test, type Page } from "@playwright/test";

/**
 * 诊断定界五步法 · 右侧宽面板「定界」tab（mock 模式）。
 *
 * mock 数据形状：demo rca = 进行中（第 4 步 · 验证，revision 4）；mock send 两段式
 * （发送即进行中 → 900ms 后 mockRcaFinal：五步全 done、status=concluded、revision 5）。
 * 完成态判定只认 data-rca-status="concluded"（后端权威信号的 mock 对位）。
 */

const rail = (page: Page) => page.getByLabel("活动 · 调查时间线");
const rcaCard = (page: Page) => page.getByTestId("rca-card");
const rcaTab = (page: Page) => page.getByRole("tab", { name: "定界" });

async function gotoWorkbench(page: Page) {
  await page.goto("/");
  await expect(page.getByText("活动 · 调查时间线")).toBeVisible({ timeout: 15_000 });
}

test("载入即定界 tab + 宽面板 + stepper 当前步正确", async ({ page }) => {
  await gotoWorkbench(page);
  // 定界优先：mock 自带定界数据，载入自动落在「定界」tab（子 Agent 自动切换不互抢）
  await expect(rcaTab(page)).toHaveAttribute("aria-selected", "true");
  await expect(rcaCard(page)).toBeVisible();
  // 宽面板：默认 clamp(420px, 42vw, 760px)；1280 视口 → 42vw ≈ 537.6px
  const box = await rail(page).boundingBox();
  expect(box).toBeTruthy();
  expect(box!.width).toBeGreaterThan(500);
  expect(box!.width).toBeLessThan(580);
  // stepper 实时进度：demo 处于第 4 步（验证），role=status 可读
  await expect(rail(page).locator(".oa-rca-stepper")).toHaveAttribute(
    "aria-label",
    "定界进度：第4步 · 验证",
  );
  await expect(rcaCard(page)).toHaveAttribute("data-rca-status", "in_progress");
});

test("发送后期间按钮 disabled，随后五步全绿定格且 footer 隐藏", async ({ page }) => {
  await gotoWorkbench(page);
  const continueButton = page.getByRole("button", { name: /继续验证 H1/ });
  await expect(continueButton).toBeEnabled();

  const input = page.getByPlaceholder(/描述你的排障任务/);
  await input.fill("支付延迟继续排查");
  await input.press("Enter");
  // 任务运行中（mock 900ms 窗口）：卡片按钮必须禁用，防双发
  await expect(continueButton).toBeDisabled({ timeout: 800 });

  await expect(page.getByText(/任务已受理/)).toBeVisible({ timeout: 10_000 });
  // 完成态：status=concluded（权威信号）→ 五步全绿定格、相位已闭环、footer 整块隐藏
  await expect(rcaCard(page)).toHaveAttribute("data-rca-status", "concluded", { timeout: 5_000 });
  await expect(rail(page).locator(".oa-rca-stepper")).toHaveAttribute("aria-label", "定界进度：已完成");
  // exact：tiles 里还有「结论 · 已闭环」，子串匹配会命中两处触发 strict mode
  await expect(rail(page).getByText("已闭环", { exact: true })).toBeVisible();
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

test("手动切走 tab 后定界更新出未读徽标，回到定界即清除", async ({ page }) => {
  await gotoWorkbench(page);
  await page.getByRole("tab", { name: "全部动态" }).click();
  await expect(page.getByLabel("有新的定界更新")).toHaveCount(0);

  const input = page.getByPlaceholder(/描述你的排障任务/);
  await input.fill("再查一轮");
  await input.press("Enter");
  await expect(page.getByText(/任务已受理/)).toBeVisible({ timeout: 10_000 });
  // 完成态 revision 5 > 已读基线 4 且不在定界 tab → 徽标点亮
  await expect(page.getByLabel("有新的定界更新")).toBeVisible({ timeout: 5_000 });
  // 手动切走后不自动抢 tab（manualTabChoice）
  await expect(page.getByRole("tab", { name: "全部动态" })).toHaveAttribute("aria-selected", "true");

  await rcaTab(page).click();
  await expect(page.getByLabel("有新的定界更新")).toHaveCount(0);
});

test("切会话无旧 run 残留：闭环卡不带进新会话", async ({ page }) => {
  await gotoWorkbench(page);
  const input = page.getByPlaceholder(/描述你的排障任务/);
  await input.fill("先把这轮定界推进到闭环");
  await input.press("Enter");
  await expect(rcaCard(page)).toHaveAttribute("data-rca-status", "concluded", { timeout: 10_000 });

  // 侧栏历史会话 → /agent-runs/:id：mock 下 Workbench 重置 demo 态，ActivityRail 随 key 重挂
  await page.locator('[data-testid="conversation-row"][data-run-id="conv_2"]').click();
  await expect(page).toHaveURL(/\/agent-runs\/conv_2$/);
  await expect(rcaTab(page)).toHaveAttribute("aria-selected", "true");
  await expect(rcaCard(page)).toHaveAttribute("data-rca-status", "in_progress", { timeout: 10_000 });
  await expect(rail(page).getByText("已闭环")).toHaveCount(0);
  await expect(page.getByText("先把这轮定界推进到闭环")).toHaveCount(0);
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
