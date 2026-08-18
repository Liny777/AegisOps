import { test, expect } from "@playwright/test";

/**
 * 假设 checkpoint 卡（mock 模式，flag 门控）：localStorage 置 openops.mock.checkpoint=1 后，
 * 发送消息即弹卡（8s 假 deadline）。三幕：① 弹卡结构（两按钮 + 倒计时 chip）+ 点「继续排查」
 * 定格结果态后淡出；② 「添加假设」展开输入框、提交后定格「已补充假设」；③ 默认（无 flag）
 * 不弹卡——既有 mock 时序零变化的守护断言。
 * real 链路的 opened/extended/closed 事件流由后端 pytest（test_diagnosis_checkpoint）覆盖。
 */

const FLAG = { name: "openops.mock.checkpoint", value: "1" };

async function sendAndAwaitCard(page: import("@playwright/test").Page) {
  await page.goto("/");
  const composer = page.getByPlaceholder(/描述你的排障任务/);
  await expect(composer).toBeVisible({ timeout: 15_000 });
  await composer.fill("支付下单 P99 突增，帮我诊断");
  await composer.press("Enter");
  return page.getByTestId("hypothesis-checkpoint-card");
}

test("checkpoint 卡：弹出结构 + 继续排查定格淡出", async ({ page }) => {
  await page.addInitScript(([flag]) => localStorage.setItem(flag.name, flag.value), [FLAG]);
  const card = await sendAndAwaitCard(page);
  await expect(card).toBeVisible({ timeout: 5_000 });
  await expect(card.getByText(/是否需要添加新假设/)).toBeVisible();
  await expect(card.getByRole("button", { name: "添加假设" })).toBeVisible();
  await expect(card.getByText(/s 后自动继续/)).toBeVisible();
  await card.getByRole("button", { name: "继续排查", exact: true }).click();
  await expect(card.getByText("已确认 · 继续排查")).toBeVisible({ timeout: 3_000 });
  // linger（2.2s）后自动淡出
  await expect(page.getByTestId("hypothesis-checkpoint-card")).toHaveCount(0, { timeout: 5_000 });
});

test("checkpoint 卡：添加假设展开输入框并提交", async ({ page }) => {
  await page.addInitScript(([flag]) => localStorage.setItem(flag.name, flag.value), [FLAG]);
  const card = await sendAndAwaitCard(page);
  await expect(card).toBeVisible({ timeout: 5_000 });
  await card.getByRole("button", { name: "添加假设" }).click();
  const textarea = card.getByPlaceholder(/描述新假设/);
  await expect(textarea).toBeVisible();
  // hold 后倒计时 chip 翻成「输入中」口径
  await expect(card.getByText(/输入中/)).toBeVisible();
  // 空文本提交按钮禁用
  await expect(card.getByRole("button", { name: "提交假设" })).toBeDisabled();
  await textarea.fill("H5 网关连接池打满，导致上游超时重试放大");
  await card.getByRole("button", { name: "提交假设" }).click();
  await expect(card.getByText("已补充假设 · 正在并入候选重排")).toBeVisible({ timeout: 3_000 });
});

test("默认无 flag：发送不弹 checkpoint 卡（既有 mock 时序守护）", async ({ page }) => {
  await sendAndAwaitCard(page);
  await expect(page.getByText(/（mock 演示）任务已受理/)).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId("hypothesis-checkpoint-card")).toHaveCount(0);
});
