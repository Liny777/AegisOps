import { test, expect } from "@playwright/test";

/**
 * B9 smoke（mock 模式，33:213 八场景裁剪为可离线跑的六幕）：
 * 对话工作台 / mock 发送 / 初始化向导 / Agent 清单 / 管理台 forbidden→admin 切换→模板编辑器 / 审计页。
 * 约束：demoIdentity 是模块级（full reload 重置为普通用户）——管理员流程必须 SPA 内导航（历史坑）。
 */

test("对话工作台：composer 与活动栏渲染", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("活动 · 调查时间线")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByPlaceholder(/描述你的排障任务/)).toBeVisible();
});

test("mock 发送：回执气泡出现", async ({ page }) => {
  await page.goto("/");
  const input = page.getByPlaceholder(/描述你的排障任务/);
  await input.waitFor({ timeout: 15_000 });
  await input.fill("查一下支付链路状态");
  await input.press("Enter");
  await expect(page.getByText(/任务已受理/)).toBeVisible({ timeout: 10_000 });
});

test("初始化向导：三步骨架（2/3/4 已合并为「配置 Agent」）", async ({ page }) => {
  await page.goto("/init");
  await expect(page.getByText("选择模板").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("配置 Agent").first()).toBeVisible();
  await expect(page.getByText("激活 Agent").first()).toBeVisible();
  // 进合并页：四区块关键元素在位
  await page.getByRole("button", { name: "下一步" }).click();
  await expect(page.getByPlaceholder(/感知快恢Agent/)).toBeVisible();
  await expect(page.getByText("身份确认")).toBeVisible();
  await expect(page.getByText("模型供应商")).toBeVisible();
  await expect(page.getByText("系统看护范围")).toBeVisible();
  await expect(page.getByRole("button", { name: "添加自定义模型" })).toBeVisible();
});

test("全部 Agents 清单（/agents）", async ({ page }) => {
  await page.goto("/agents");
  await expect(page.getByText("支付域感知快恢").first()).toBeVisible({ timeout: 15_000 });
});

test("管理台：普通用户 403 → SPA 内切管理员 → 模板编辑器（含 S1 新增角色入口）", async ({ page }) => {
  // 直开 /admin：full reload 身份=普通用户 → 403
  await page.goto("/admin/templates");
  await expect(page.getByText("403 · 无权访问")).toBeVisible({ timeout: 15_000 });
  // 回工作台 → 侧栏「进入管理台」（in-app 切换角色，勿 reload）
  await page.getByRole("button", { name: "返回工作台" }).click();
  await page.getByText("进入管理台").click();
  await expect(page.getByRole("main").getByText("模板管理")).toBeVisible({ timeout: 15_000 });
  // 打开编辑器：S1 增删角色/预算输入在位
  await page.getByText("编辑", { exact: false }).last().click();
  await expect(page.getByRole("button", { name: "新增角色" })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("max_children（1..10）")).toBeVisible();
  // 禁用版本按钮仅 active 版本存在时显示（mock detail 无 active_version）——real 面已浏览器实测
  await expect(page.getByRole("button", { name: "保存草稿" })).toBeVisible();
});

test("审计页：Trace 过滤输入在位", async ({ page }) => {
  await page.goto("/admin/templates"); // 先 403（普通用户）
  await page.getByRole("button", { name: "返回工作台" }).click();
  await page.getByText("进入管理台").click();
  await page.getByText("审计回放").click();
  await expect(page.getByPlaceholder(/audit_trace_id/)).toBeVisible({ timeout: 15_000 });
});
