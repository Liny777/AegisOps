import { test, expect } from "@playwright/test";

/**
 * 白屏加固（GlobalErrorBoundary + chunkError 节流，mock 模式）：
 * dev server 下懒加载按源码路径请求（/src/settings/SettingsHome.tsx），route abort 即可模拟
 * 「重新部署后旧 tab chunk hash 失效」。预期链：lazy reject → 边界捕获 → 自动整页 reload 一次
 * （写 60s 节流时间戳）→ 仍失败 → 节流命中 → 可读错误页（而非白屏）。
 */

test("chunk 失败：自动刷新一次后仍失败 → 全局错误页而非白屏", async ({ page }) => {
  await page.route("**/SettingsHome*", (r) => r.abort());
  await page.goto("/settings");
  // 首次失败触发自动 reload，reload 后再失败落错误页——给足两轮加载时间
  await expect(page.getByTestId("global-error-fallback")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("页面资源加载失败")).toBeVisible();
  await expect(page.getByText(/系统刚发布了新版本/)).toBeVisible();
});

test("节流窗口内不再自动刷新：直接落错误页", async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem("openops.bootguard.reloadAt", String(Date.now())));
  await page.route("**/SettingsHome*", (r) => r.abort());
  await page.goto("/settings");
  await expect(page.getByTestId("global-error-fallback")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("页面资源加载失败")).toBeVisible();
});
