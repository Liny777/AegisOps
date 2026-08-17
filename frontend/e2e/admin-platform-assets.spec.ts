import { test, expect, type Page } from "@playwright/test";

/**
 * 管理台平台级资产写闭环（29.9，mock 模式）：
 * - Skill 基线：上传 → 列表出现新行（skill_key 为命名空间化 `system-{name}`）→ 删除；
 * - **同名收敛**：上传一个与既有行同展示名但键不同的 Skill（存量裸名 `inspection` vs 新键
 *   `system-巡检 inspection`）→ 旧行被清理、只剩一条同名行，且提示条显式说明清理了几条。
 *   这是本特性的核心：两行并存时 resolve_skill_alias 精确键优先会解析到过期旧行；
 * - MCP 服务（新一级页）：注册 → 列表出现 → 同名重注册为「原地更新」→ 删除。
 * mock 数据见 mockData.adminTables.skills / .mcps。
 */

async function gotoAdmin(page: Page, sub: string) {
  await page.goto(`/admin/${sub}?as=admin`);
  await expect(page.getByRole("main").getByText(sub === "skills" ? "Skill 基线" : "MCP 服务"))
    .toBeVisible({ timeout: 15_000 });
}

/** 行内「删除」动作单元格。**不能**用 getByText("删除")——同名列头也在 main 里，会先被选中且点不动。 */
const deleteCell = (page: Page) => page.locator('span[title="删除"]');

/** 通过隐藏的 file input 投喂一个最小 ZIP（魔数 PK）——mock 只按文件名派生 skill_key。 */
async function pickZip(page: Page, filename: string) {
  await page.locator('input[type="file"]').setInputFiles({
    name: filename, mimeType: "application/zip", buffer: Buffer.from([0x50, 0x4b, 0x03, 0x04]),
  });
}

test("Skill 基线：上传平台 Skill → 同名收敛为单行 → 删除", async ({ page }) => {
  await gotoAdmin(page, "skills");

  // ---- 上传新 Skill：键为 system-{原始名} ----
  await page.getByRole("button", { name: "上传 Skill" }).click();
  await expect(page.getByText("上传平台 Skill")).toBeVisible();
  await pickZip(page, "demo-skill.zip");
  await page.getByRole("button", { name: "上传", exact: true }).click();
  await expect(page.getByText("已上传「demo-skill」")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("system-demo-skill")).toBeVisible();

  // ---- 再传一次同一个：加版本，**不**报"清理了 N 条"（幸存行按 key 命中，不算收敛） ----
  await page.getByRole("button", { name: "上传 Skill" }).click();
  await pickZip(page, "demo-skill.zip");
  await page.getByRole("button", { name: "上传", exact: true }).click();
  await expect(page.getByText("已更新「demo-skill」")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/清理了 \d+ 条同名旧记录/)).toHaveCount(0);
  await expect(page.getByText("system-demo-skill")).toHaveCount(1);

  // ---- 同名换键：seed 行展示名「巡检 inspection」、键是裸名 inspection → 应被收敛掉 ----
  await expect(page.getByText("inspection", { exact: true })).toBeVisible();  // 收敛前的裸名键
  await page.getByRole("button", { name: "上传 Skill" }).click();
  await pickZip(page, "巡检 inspection.zip");
  await page.getByRole("button", { name: "上传", exact: true }).click();
  await expect(page.getByText(/已上传「巡检 inspection」，并清理了 1 条同名旧记录/))
    .toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("inspection", { exact: true })).toHaveCount(0);   // 裸名旧行没了
  // exact 必须开：提示条与 skill_key 单元格（system-巡检 inspection）都含这个子串，松匹配会数到 3
  await expect(page.getByRole("main").getByText("巡检 inspection", { exact: true })).toHaveCount(1);

  // ---- 删除（破坏性动作走 confirm） ----
  page.once("dialog", (d) => {
    expect(d.message()).toContain("确认删除平台 Skill");
    void d.accept();
  });
  await deleteCell(page).first().click();
  await expect(page.getByText(/已删除「/)).toBeVisible({ timeout: 10_000 });
});

test("MCP 服务：注册平台 MCP → 同名重注册为原地更新 → 删除", async ({ page }) => {
  await gotoAdmin(page, "mcps");
  // 独立 mock 表，不该回退成模板表（getAdminTable 末尾有 `?? adminTables.templates` 的兜底）
  await expect(page.getByRole("main").getByText("服务名称")).toBeVisible();
  await expect(page.getByRole("main").getByText("template_key")).toHaveCount(0);

  // 长 endpoint 必须**完整可见**：管理面不脱敏（用户面才截前 12 字符），且单元格 wrap 折行不省略
  const LONG_URL = "https://mcpgateway.internal.corp.example.com:8443/api/v1/tenants/prod-payment"
    + "/servers/alarm-and-recovery-server/streamable/mcp?region=cn-north-4";
  await page.getByRole("button", { name: "注册 MCP" }).click();
  await expect(page.getByText("注册平台 MCP")).toBeVisible();
  await page.getByPlaceholder("如：cmdb-mcp-server").fill("cmdb-mcp-server");
  await page.getByPlaceholder("https://cmdb.internal/mcp").fill(LONG_URL);
  await page.getByRole("button", { name: "注册", exact: true }).click();
  await expect(page.getByText("已注册「cmdb-mcp-server」")).toBeVisible({ timeout: 10_000 });
  // 断言整串在场（不是 "https://mcpgat…"），且该单元格没被裁剪
  const epCell = page.getByText(LONG_URL, { exact: true });
  await expect(epCell).toBeVisible();
  expect(await epCell.evaluate((el) =>
    el.scrollWidth <= el.clientWidth + 1 && el.scrollHeight <= el.clientHeight + 1)).toBe(true);

  // 同名重注册 = 原地更新（保住 tool 标注与模板绑定的复合键），不新增行
  await page.getByRole("button", { name: "注册 MCP" }).click();
  await page.getByPlaceholder("如：cmdb-mcp-server").fill("cmdb-mcp-server");
  await page.getByPlaceholder("https://cmdb.internal/mcp").fill("https://cmdb-v2.internal/mcp");
  await page.getByRole("button", { name: "注册", exact: true }).click();
  await expect(page.getByText(/已更新「cmdb-mcp-server」的注册信息/)).toBeVisible({ timeout: 10_000 });
  // exact 必须开：提示条也含该名字，松匹配会把它一并数进去
  await expect(page.getByRole("main").getByText("cmdb-mcp-server", { exact: true })).toHaveCount(1);
  await expect(page.getByText("https://cmdb-v2.internal/mcp")).toBeVisible();

  page.once("dialog", (d) => {
    expect(d.message()).toContain("确认删除平台 MCP");
    void d.accept();
  });
  await deleteCell(page).last().click();
  await expect(page.getByText("已删除「cmdb-mcp-server」")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("main").getByText("cmdb-mcp-server", { exact: true })).toHaveCount(0);
});
