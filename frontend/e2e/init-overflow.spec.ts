import { expect, test } from "@playwright/test";

// 初始化向导「能力识别 / 模型 / 看护范围」卡片的长名回归：管理员/用户可填入超长自由文本 name，
// 加固后应始终省略号截断、不撑破卡片、页面无横向溢出。修复前（标题 whiteSpace:nowrap 缺
// overflow/ellipsis + minWidth:0）会在此断言处失败。
for (const width of [375, 460, 720, 1280]) {
  test(`初始化向导长名卡片在 ${width}px 不撑破框（省略号截断，无横向溢出）`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/e2e/fixtures/init-overflow.html");

    const capTitle = page.getByTestId("cap-title").first();
    await expect(capTitle).toBeVisible();

    // 1) 页面不产生横向滚动——长名没有撑破任何容器。
    const page1 = await page.evaluate(() => ({
      docScroll: document.documentElement.scrollWidth,
      bodyScroll: document.body.scrollWidth,
      inner: window.innerWidth,
    }));
    expect(page1.docScroll).toBeLessThanOrEqual(page1.inner + 1);
    expect(page1.bodyScroll).toBeLessThanOrEqual(page1.inner + 1);

    // 2) 没有任何元素的左右边界越出视口。
    const bleeding = await page.evaluate(() =>
      Array.from(document.querySelectorAll<HTMLElement>("*"))
        .map((el) => {
          const r = el.getBoundingClientRect();
          return { tag: el.tagName, cls: typeof el.className === "string" ? el.className : "", left: r.left, right: r.right };
        })
        .filter((r) => r.right > window.innerWidth + 1 || r.left < -1),
    );
    expect(bleeding).toEqual([]);

    // 3) 能力名走了省略号截断（被裁而非撑开容器）。
    const clipped = await capTitle.evaluate((el) => el.scrollWidth > el.clientWidth);
    expect(clipped).toBe(true);

    // 4)「已识别」徽标始终可见（未被超长标题挤没）。
    await expect(page.getByText("已识别").first()).toBeVisible();

    // 5) 每张模型卡的名字都有可见宽度（未被 extra/baseUrl 在 flex 收缩中挤成 0 宽）——
    //    覆盖平台卡与带 extra 的自定义卡两条路径。
    const llmLabelWidths = await page
      .getByTestId("llm-label")
      .evaluateAll((els) => els.map((el) => (el as HTMLElement).clientWidth));
    expect(llmLabelWidths.length).toBeGreaterThanOrEqual(2);
    for (const w of llmLabelWidths) expect(w).toBeGreaterThan(8);
  });
}
