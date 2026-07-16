import { expect, test } from "@playwright/test";

test("Mermaid zoom/fullscreen always keeps exactly one visible diagram", async ({ page }) => {
  await page.goto("/e2e/fixtures/mermaid.html");
  const block = page.locator('[data-streamdown="mermaid-block"]');
  const diagrams = page.locator('[data-streamdown="mermaid-block"] [aria-label="Mermaid chart"] svg');
  await expect(block).toHaveCount(1);
  await expect(diagrams).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('[data-streamdown="mermaid-block"] [aria-label="Mermaid chart"] svg:visible')).toHaveCount(1);

  await block.getByRole("button", { name: "Zoom in" }).click();
  await expect(page.locator('[data-streamdown="mermaid-block"] [aria-label="Mermaid chart"] svg:visible')).toHaveCount(1);
  await expect(block).toHaveCount(1);

  const fullscreenButton = block.getByRole("button", { name: "View fullscreen" });
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await fullscreenButton.click();
    await expect.poll(async () => {
      const native = await page.evaluate(() => Boolean(document.fullscreenElement));
      const fallback = await page.getByRole("button", { name: "Exit fullscreen" }).count();
      return native || fallback === 1;
    }).toBe(true);
    await expect(page.locator('[data-streamdown="mermaid-block"] [aria-label="Mermaid chart"] svg:visible')).toHaveCount(1);

    if (attempt === 0) {
      await fullscreenButton.click(); // 再次点击同一按钮退出
    } else {
      await page.keyboard.press("Escape");
    }
    await expect.poll(() => page.evaluate(() => Boolean(document.fullscreenElement))).toBe(false);
    await expect(page.getByRole("button", { name: "Exit fullscreen" })).toHaveCount(0);
    await expect(page.locator('[data-streamdown="mermaid-block"] [aria-label="Mermaid chart"] svg:visible')).toHaveCount(1);
  }

  await expect.poll(() => page.evaluate(() => document.body.style.overflow)).toBe("");
});

test("Mermaid fullscreen fallback hides the original diagram and cleans up on Escape", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(HTMLElement.prototype, "requestFullscreen", {
      configurable: true,
      value: () => Promise.reject(new Error("fixture: fullscreen unavailable")),
    });
  });
  await page.goto("/e2e/fixtures/mermaid.html");
  const block = page.locator('[data-streamdown="mermaid-block"]');
  await expect(block.locator('[aria-label="Mermaid chart"] svg')).toBeVisible({ timeout: 15_000 });

  await block.getByRole("button", { name: "View fullscreen" }).click();
  await expect(page.getByRole("button", { name: "Exit fullscreen" })).toBeVisible();
  // Streamdown 降级层会创建第二个 DOM 实例，但原图被隐藏，屏幕上始终只显示一张。
  await expect(block.locator('[aria-label="Mermaid chart"] svg')).toHaveCount(2);
  await expect(block.locator('[aria-label="Mermaid chart"] svg:visible')).toHaveCount(1);
  const overlayRect = await page.getByRole("button", { name: "Exit fullscreen" }).evaluate((button) =>
    button.parentElement?.getBoundingClientRect().toJSON(),
  );
  const viewport = await page.evaluate(() => ({ width: innerWidth, height: innerHeight }));
  expect(overlayRect).toMatchObject({ x: 0, y: 0, ...viewport });

  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Exit fullscreen" })).toHaveCount(0);
  await expect(block.locator('[aria-label="Mermaid chart"] svg')).toHaveCount(1);
  await expect(block.locator('[aria-label="Mermaid chart"] svg:visible')).toHaveCount(1);
  await expect.poll(() => page.evaluate(() => document.body.style.overflow)).toBe("");
});

for (const width of [460, 720, 1280]) {
  test(`long input, tool and approval content do not overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/e2e/fixtures/layout.html");
    await expect(page.locator(".oa-hitl-float")).toBeVisible();
    await page.locator(".oa-hitl-reason").fill("R".repeat(2000));

    const selectors = [
      ".oa-chat-input",
      ".oa-chat-textarea",
      ".oa-tool-call-group",
      ".oa-tool-call-group-body",
      ".oa-tool-call-group-body pre",
      ".oa-hitl-float",
      ".oa-hitl-card",
      ".oa-hitl-tool",
      ".oa-hitl-summary",
      ".oa-hitl-fact-value",
      ".oa-hitl-reason",
    ];
    const overflow = await page.evaluate((items) => items.flatMap((selector) =>
      Array.from(document.querySelectorAll<HTMLElement>(selector)).map((element) => ({
        selector,
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
        left: element.getBoundingClientRect().left,
        right: element.getBoundingClientRect().right,
      })).filter((item) => item.scrollWidth > item.clientWidth + 1 || item.left < -1 || item.right > innerWidth + 1),
    ), selectors);
    expect(overflow).toEqual([]);
  });
}
