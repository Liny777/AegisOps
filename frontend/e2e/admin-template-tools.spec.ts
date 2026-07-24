import { test, expect, type Page } from "@playwright/test";

/**
 * 模板编辑器 MCP 工具勾选（mock 模式，同名冲突根治回归）：
 * - 核心：勾 omodel-mcp-server 不再联动 opsdfx-mcp（两家共享同名 query_resource/recover_execute，
 *   旧 bug 用裸工具名当身份 → 勾 A 家会把 B 家的「全选」条件被动满足）；
 * - 存量裸名读时归一为「server::tool」复合键 → 迁移提示条出现；
 * - 目录外残留名显示为可移除 chip；
 * - 资产治理「绑定/解绑」列按 server 独立（第二条写路径同源修复）。
 * mock 数据见 mockData.adminMcpToolsRaw / mockTemplateDetail。
 */

async function enterAdminTemplates(page: Page) {
  await page.goto("/?as=admin");
  await expect(page.getByText("进入管理台")).toBeVisible({ timeout: 15_000 });
  await page.getByText("进入管理台").click();
  await expect(page.getByRole("main").getByText("模板管理")).toBeVisible({ timeout: 15_000 });
}

// main 平台 MCP 绑定区里某 server 的勾选框（.first() 取 main 段——它在 DOM 中先于 sub 段渲染）
const serverCheckbox = (page: Page, server: string) =>
  page.locator(`label:has-text("${server}（")`).locator('input[type="checkbox"]').first();

test("模板编辑器：勾 omodel-mcp-server 不联动 opsdfx-mcp + 存量裸名迁移 + 残留 chip", async ({ page }) => {
  await enterAdminTemplates(page);
  await page.getByText("编辑", { exact: false }).first().click();
  await expect(page.getByText(/main 平台 MCP tool 绑定/)).toBeVisible({ timeout: 10_000 });

  const omodel = serverCheckbox(page, "omodel-mcp-server");
  const opsdfx = serverCheckbox(page, "opsdfx-mcp");

  // 起始态：query_topology（omodel 独有）已选 → main 段 omodel 部分选中未满；opsdfx 完全未选
  // （.first() 取 main 段——sub「巡检」也有同名 ServerPicker，故按 DOM 先后取第一处）
  await expect(omodel).not.toBeChecked();
  await expect(opsdfx).not.toBeChecked();
  await expect(page.getByText(/部分 1\/3/).first()).toBeVisible();

  // 存量裸名（query_topology）读时升级为复合键 → 迁移提示条（仅 main 段渲染）
  await expect(page.getByText(/已按当前目录换算为「server::工具」复合键/)).toBeVisible();
  // 目录外残留名（ghost_tool）→ 可移除 chip（仅 main 段，sub mcp_tools 无此名；
  // ghost_tool 也出现在「活动栏工具名称」区，故 .first() 取残留 chip 处）
  await expect(page.getByText("不在目录的存量绑定", { exact: false })).toBeVisible();
  await expect(page.getByText("ghost_tool").first()).toBeVisible();

  // 核心回归：勾满 omodel-mcp-server → opsdfx-mcp **保持未选**（旧 bug 会因同名工具被动勾上）
  await omodel.check();
  await expect(omodel).toBeChecked();
  await expect(opsdfx).not.toBeChecked();

  // 反向亦然：再勾 opsdfx 不影响 omodel
  await opsdfx.check();
  await expect(opsdfx).toBeChecked();
  await expect(omodel).toBeChecked();

  // 保存草稿成功（payload 归一为复合键，后端单测已断言存储形态；此处验交互闭环）
  await page.getByRole("button", { name: "保存草稿" }).click();
  await expect(page.getByText(/草稿 v\d+ 已保存/)).toBeVisible({ timeout: 10_000 });
});

test("资产治理：绑定列按 server 独立，绑 omodel 不改 opsdfx 状态", async ({ page }) => {
  await enterAdminTemplates(page);
  // 模板行「资产治理」drill → 平台资产表带「模板绑定」列
  await page.getByText("资产治理", { exact: false }).first().click();
  await expect(page.getByText("模板绑定")).toBeVisible({ timeout: 10_000 });

  // 起始：两家 server 均未绑（mock 模板 default_tools 不含其完整工具集）→ 两处「绑定」、零「解绑」
  await expect(page.getByText("绑定", { exact: true })).toHaveCount(2);
  await expect(page.getByText("解绑", { exact: true })).toHaveCount(0);

  // 绑第一家（omodel-mcp-server；弹保存成功 alert）→ 该家转「解绑」，另一家仍「绑定」
  // （若同名工具仍联动，两家会一起转「解绑」——此断言正是非联动的直接证明）
  page.once("dialog", (d) => d.accept());
  await page.getByText("绑定", { exact: true }).first().click();
  await expect(page.getByText("解绑", { exact: true })).toHaveCount(1, { timeout: 10_000 });
  await expect(page.getByText("绑定", { exact: true })).toHaveCount(1);
});
