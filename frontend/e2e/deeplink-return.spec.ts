import { test, expect } from "@playwright/test";

/**
 * WeLink 深链回位（lib/loginReturn.ts，mock 模式）：IAM 登录回跳白名单写死站点根，
 * 401 跳登录前存 sessionStorage、HomeRedirect 落 "/" 时恢复——此处用预埋存储模拟登录往返。
 * 另验 /agent-runs/:id/chat 尾段归一与通配 404 页（曾静默弹回首页把深链故障掩成「莫名到了 chat」）。
 * ⚠ addInitScript 每次导航都重跑，而回位是「取出即删」语义——必须用一次性 seeded 标记防重播种，
 *   否则第二次导航会把刚消费掉的深链又种回去，一次性断言必假绿。
 */

const KEY = "openops.loginReturn.path";

/** 浏览器内执行：只在本 tab 首次导航时播种（seeded 标记跨导航存活，防重播种）。 */
function seedOnce({ key, path, offsetMs }: { key: string; path: string; offsetMs: number }) {
  if (sessionStorage.getItem("openops.e2e.loginReturn.seeded")) return;
  sessionStorage.setItem("openops.e2e.loginReturn.seeded", "1");
  sessionStorage.setItem(key, JSON.stringify({ p: path, ts: Date.now() + offsetMs }));
}

test("IAM 往返回位：落首页恢复深链，一次性消费后不再劫持", async ({ page }) => {
  await page.addInitScript(seedOnce, { key: KEY, path: "/agent-runs/conv_2", offsetMs: 0 });
  await page.goto("/");
  await expect(page).toHaveURL(/\/agent-runs\/conv_2$/, { timeout: 15_000 });
  await expect(page.getByText("活动 · 调查时间线")).toBeVisible({ timeout: 15_000 });
  // 一次性：再开首页应走常规分流进 chat，而不是又被旧深链劫持
  await page.goto("/");
  await page.waitForURL(/\/agent-teams\/.+\/chat/, { timeout: 15_000 });
});

test("新外部意图覆盖旧存根：带 ?q= 进站时残留深链不劫持，问题进专属新 run", async ({ page }) => {
  await page.addInitScript(seedOnce, { key: KEY, path: "/agent-runs/conv_2", offsetMs: 0 });
  const q = "新意图不被旧深链劫持-8c2e";
  await page.goto(`/?q=${encodeURIComponent(q)}`);
  // ExternalJump 专属新 run（run_demo_ 前缀），而不是被旧存根劫持进 conv_2
  await page.waitForURL(/\/agent-runs\/run_demo_/, { timeout: 15_000 });
  await expect(page.getByText(q)).toBeVisible({ timeout: 15_000 });
});

test("过期存根不回位：TTL 外直接走常规分流", async ({ page }) => {
  await page.addInitScript(seedOnce, { key: KEY, path: "/agent-runs/conv_2", offsetMs: -16 * 60 * 1000 });
  await page.goto("/");
  await page.waitForURL(/\/agent-teams\/.+\/chat/, { timeout: 15_000 });
});

test("open redirect 形态不回位：站外路径被拒", async ({ page }) => {
  await page.addInitScript(seedOnce, { key: KEY, path: "//evil.example.com/phish", offsetMs: 0 });
  await page.goto("/");
  await page.waitForURL(/\/agent-teams\/.+\/chat/, { timeout: 15_000 });
  expect(page.url()).not.toContain("evil.example.com");
});

test("/agent-runs/:id/chat 尾段容错：URL 归一且工作台正常渲染", async ({ page }) => {
  await page.goto("/agent-runs/conv_2/chat");
  await expect(page).toHaveURL(/\/agent-runs\/conv_2$/, { timeout: 15_000 });
  await expect(page.getByText("活动 · 调查时间线")).toBeVisible({ timeout: 15_000 });
});

test("未知路径显式 404：不再静默弹回首页，按钮可回工作台", async ({ page }) => {
  await page.goto("/no/such/page");
  await expect(page.getByText("页面不存在或链接已失效")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "回到工作台" }).click();
  await page.waitForURL(/\/agent-teams\/.+\/chat/, { timeout: 15_000 });
  await expect(page.getByText("活动 · 调查时间线")).toBeVisible({ timeout: 15_000 });
});
