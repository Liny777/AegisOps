import { test, expect } from "@playwright/test";

/**
 * 7x24 告警接管切片 e2e v2（mock 模式，不依赖后端）——四幕：
 * ① 清单（事件视角）：未接管行 shield-off + 操作列灰色不可点；已接管行「查看处理会话」
 *   跳 run_alert_*，且会话历史侧栏不含告警 run（conversation-row 仍 3）。
 * ② 筛选：状态+接管两下拉联动过滤 → 「筛选 2」计数徽标 → 清除恢复 → 搜索 MySQL 收敛 → 清除恢复。
 * ③ 配置编辑器（两步向导）：第一步 名称/类型/级别/提示词（"/" 呼出 skill 菜单插入），
 *   第二步 近7天/近3天命中预览（计数随窗口切换）→ 确认后新行出现且提示词列非空。
 * ④ 批量：勾选两行 → 批量禁用（Toggle 灭 + 统计归零 + 选中清空）→ 再勾选批量删除（行数/统计减少）。
 * 锚点一律 data-testid / 本切片专属文案（mock 专属编号 ALM-*），不复用 smoke 的精确断言串。
 */

test("清单：未接管行灰态不可点，已接管行跳会话且历史无告警 run", async ({ page }) => {
  await page.goto("/alerts");
  await expect(page.getByText("命中本 Agent 订阅规则的告警自动排队诊断")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("alerts-incident-row").first()).toBeVisible();

  // 未接管行：shield-off 图标 + 操作列灰色 cursor:not-allowed（非链接、点了不跳）
  const noneRow = page.getByTestId("alerts-incident-row").filter({ hasText: "未接管" }).first();
  await expect(noneRow.locator(".ti-shield-off")).toBeVisible();
  const disabledAction = noneRow.getByText("查看处理会话");
  await expect(disabledAction).toHaveCSS("cursor", "not-allowed");

  // 已接管行（mock 专属编号，run_clickable=true）：点「查看处理会话」跳 run_alert_*
  const doneRow = page.getByTestId("alerts-incident-row").filter({ hasText: "ALM-20260730-000101" });
  await expect(doneRow.getByText("已完成")).toBeVisible();
  await doneRow.getByText("查看处理会话").click();
  await page.waitForURL(/\/agent-runs\/run_alert_/);

  // 决策 6：告警诊断会话可直开（复用聊天工作台），但绝不混进左侧会话历史
  await page.goto("/");
  await expect(page.getByTestId("conversation-row")).toHaveCount(3); // mock 基线 conv_1..3（无 run_alert_*）
});

test("筛选：状态+接管联动过滤、计数徽标与清除、搜索收敛", async ({ page }) => {
  await page.goto("/alerts");
  await expect(page.getByTestId("alerts-incident-row").first()).toBeVisible({ timeout: 15_000 });
  const baseline = await page.getByTestId("alerts-incident-row").count();
  expect(baseline).toBeGreaterThan(2);

  // 告警状态=已关闭 + 接管状态=未接管 联动过滤
  await page.getByTestId("alerts-filter-status").selectOption("closed");
  await page.getByTestId("alerts-filter-takeover").selectOption("none");
  await expect
    .poll(async () => page.getByTestId("alerts-incident-row").count())
    .toBeLessThan(baseline);
  const filtered = page.getByTestId("alerts-incident-row");
  const filteredCount = await filtered.count();
  expect(filteredCount).toBeGreaterThan(0);
  for (let i = 0; i < filteredCount; i++) {
    await expect(filtered.nth(i)).toContainText("已关闭");
    await expect(filtered.nth(i)).toContainText("未接管");
  }

  // 激活筛选计数徽标 + 清除筛选恢复
  await expect(page.getByText("筛选 2")).toBeVisible();
  await page.getByText("清除筛选").click();
  await expect
    .poll(async () => page.getByTestId("alerts-incident-row").count())
    .toBe(baseline);
  await expect(page.getByText("筛选 2")).toHaveCount(0);

  // 搜索 MySQL：只剩 MySQL 行；清除按钮恢复
  await page.getByPlaceholder("搜索告警编号、类型或对象").fill("MySQL");
  await expect
    .poll(async () => page.getByTestId("alerts-incident-row").count())
    .toBeLessThan(baseline);
  const mysqlRows = page.getByTestId("alerts-incident-row");
  const mysqlCount = await mysqlRows.count();
  expect(mysqlCount).toBeGreaterThan(0);
  for (let i = 0; i < mysqlCount; i++) {
    await expect(mysqlRows.nth(i)).toContainText("MySQL");
  }
  await page.getByTestId("alerts-search-clear").click();
  await expect
    .poll(async () => page.getByTestId("alerts-incident-row").count())
    .toBe(baseline);
});

test("配置编辑器：两步向导——第一步表单+斜杠选 skill，第二步窗口预览后确认建规则", async ({ page }) => {
  await page.goto("/settings/alerts");
  await expect(page.getByRole("button", { name: "添加告警策略" }).first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("alerts-rule-row").first()).toBeVisible();
  const rulesBefore = await page.getByTestId("alerts-rule-row").count();

  await page.getByRole("button", { name: "添加告警策略" }).first().click();
  const editor = page.getByTestId("alerts-rule-editor");
  await expect(editor).toBeVisible();

  // 第一步只留 名称/类型/级别/提示词：预览与监控策略区块都不在
  await expect(editor.getByTestId("alerts-rule-owner-only")).toBeVisible();
  await expect(editor.getByTestId("alerts-rule-preview")).toHaveCount(0);
  await expect(editor.getByText("最近 3 天该类型告警")).toHaveCount(0);
  await expect(editor.getByText("监控策略", { exact: true })).toHaveCount(0);
  await editor.getByPlaceholder("策略名称（必填）").fill("MySQL 核心库接管");

  // 策略类型可多选：加选 PostgreSQL → 全取消时「下一步」禁用 + 警示 → 回到只选 MySQL（保住下方预览计数断言）
  await editor.getByText("PostgreSQL", { exact: true }).click();
  await editor.getByText("MySQL", { exact: true }).click();  // 取消 MySQL，只剩 PostgreSQL
  await editor.getByText("PostgreSQL", { exact: true }).click();  // 再取消 → 空
  await expect(editor.getByText("至少选择一个策略类型")).toBeVisible();
  await expect(editor.getByRole("button", { name: "下一步" })).toBeDisabled();
  await editor.getByText("MySQL", { exact: true }).click();  // 重选 MySQL

  // 提示词 "/" 呼出 skill 菜单（mock 档 4 条），选 /inspect 插入光标处
  const ta = editor.getByPlaceholder(/诊断提示词/);
  await ta.fill("先用 ");
  await ta.pressSequentially("/insp");
  await expect(editor.getByTestId("alerts-skill-menu")).toBeVisible();
  await editor.getByTestId("alerts-skill-menu").getByText("/inspect", { exact: true }).click();
  await expect(ta).toHaveValue("先用 /inspect ");
  await ta.pressSequentially("排查，再按五步法给结论。");

  // 第二步：默认近 7 天预览，切近 3 天计数收窄（mock 日期相对生成：D1/D2 在 3 天窗、D5/D6 仅 7 天窗）
  await editor.getByRole("button", { name: "下一步" }).click();
  await expect(editor.getByTestId("alerts-rule-preview")).toBeVisible();
  await expect(editor.getByTestId("alerts-rule-window")).toHaveValue("7");
  await expect
    .poll(async () => editor.getByTestId("alerts-rule-preview-row").count())  // mock delay 120ms，等数据到位
    .toBeGreaterThan(0);
  const count7 = await editor.getByTestId("alerts-rule-preview-row").count();
  await expect(editor.getByText(`最近 ${count7} 条告警`)).toBeVisible();
  await expect(editor.getByText("未分派").first()).toBeVisible();          // 状态徽标列在位
  await expect(editor.getByText("企业ID")).toBeVisible();                  // 企业 ID 展示列（2026-08-10）
  // 编号跳转：mock 假链按企业分发拼 ?alarmCode=（首行应为可点 <a>）
  const firstNo = editor.getByTestId("alerts-rule-preview-row").first().locator("a").first();
  await expect(firstNo).toHaveAttribute("href", /alarmCode=/);
  await editor.getByTestId("alerts-rule-window").selectOption("3");
  await expect
    .poll(async () => editor.getByTestId("alerts-rule-preview-row").count())
    .toBeLessThan(count7);

  // 上一步回退：第一步表单原样保留 → 再下一步 → 确认保存
  await editor.getByRole("button", { name: "上一步" }).click();
  await expect(editor.getByPlaceholder("策略名称（必填）")).toHaveValue("MySQL 核心库接管");
  await editor.getByRole("button", { name: "下一步" }).click();
  await editor.getByRole("button", { name: "确认", exact: true }).click();

  await expect
    .poll(async () => page.getByTestId("alerts-rule-row").count())
    .toBe(rulesBefore + 1);
  const newRow = page.getByTestId("alerts-rule-row").filter({ hasText: "MySQL 核心库接管" });
  await expect(newRow).toBeVisible();
  // 提示词列非空（斜杠插入的 /inspect 一并落库，单行省略仍在 DOM）
  await expect(newRow.getByText(/先用 \/inspect/)).toBeVisible();
});

test("批量：勾选两行批量禁用后 Toggle 灭，再批量删除减行数并清空选中", async ({ page }) => {
  await page.goto("/settings/alerts");
  await expect(page.getByTestId("alerts-rule-row").first()).toBeVisible({ timeout: 15_000 });
  const rowsBefore = await page.getByTestId("alerts-rule-row").count();
  expect(rowsBefore).toBe(4); // mock 基线：支付实例 4 条策略（3 启用 1 禁用，含一条深链接管建的通用规则）
  await expect(page.getByText(/已启用 3 条/)).toBeVisible();

  // 未选中时「批量操作」隐藏
  await expect(page.getByRole("button", { name: /批量操作/ })).toHaveCount(0);

  // 勾选前两行（均为启用态）→ 批量禁用
  await page.getByTestId("alerts-rule-checkbox").nth(0).check();
  await page.getByTestId("alerts-rule-checkbox").nth(1).check();
  await page.getByRole("button", { name: /批量操作/ }).click();
  const menu = page.getByTestId("alerts-batch-menu");
  await expect(menu).toBeVisible();
  await menu.getByText("批量禁用").click();

  // 两行 Toggle 变灭（data-enabled 归 false）+ 统计归零 + 选中清空（批量按钮隐藏）
  await expect(page.getByTestId("alerts-rule-row").nth(0)).toHaveAttribute("data-enabled", "false");
  await expect(page.getByTestId("alerts-rule-row").nth(1)).toHaveAttribute("data-enabled", "false");
  await expect(page.getByText(/已启用 1 条/)).toBeVisible(); // 剩 PostgreSQL 一条启用
  await expect(page.getByRole("button", { name: /批量操作/ })).toHaveCount(0);

  // 再勾选两行 → 批量删除（菜单红色警示项）→ 行数与统计减少、选中清空
  await page.getByTestId("alerts-rule-checkbox").nth(0).check();
  await page.getByTestId("alerts-rule-checkbox").nth(1).check();
  await page.getByRole("button", { name: /批量操作/ }).click();
  await page.getByTestId("alerts-batch-menu").getByText("批量删除").click();
  await expect
    .poll(async () => page.getByTestId("alerts-rule-row").count())
    .toBe(rowsBefore - 2);
  await expect(page.getByText(/共 2 条策略/)).toBeVisible();
  await expect(page.getByRole("button", { name: /批量操作/ })).toHaveCount(0);
});

test("白名单未开通：导航层锁定 + 直链空态兜底（mock 缝切换）", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("openops.mock.alertGranted", "0"));
  // 侧栏「告警清单」锁定：置灰不可点，点击不发生跳转
  await page.goto("/");
  const lockedNav = page.locator('[title="告警接管清单"]');
  await expect(lockedNav).toBeVisible({ timeout: 15_000 });
  await expect(lockedNav).toHaveCSS("cursor", "not-allowed");
  await lockedNav.click();
  await expect(page).not.toHaveURL(/\/alerts/);
  // 直链仍可达（导航锁不是权限闸），页面级空态兜底
  await page.goto("/alerts");
  await expect(page.getByTestId("alerts-not-granted")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("请联系平台管理员为你开通")).toBeVisible();
  // 设置页：菜单项锁定 + 主区空态兜底
  await page.goto("/settings/alerts");
  await expect(page.locator('div[title^="未开通"]', { hasText: "告警接管配置" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("alerts-not-granted")).toBeVisible();
});
