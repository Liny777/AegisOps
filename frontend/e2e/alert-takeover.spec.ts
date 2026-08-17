// 告警平台深链一键接管（mock 档）：?q= 自动发问链 + 三参上下文 → 诊断闭环 → 按钮 → 确认卡 → 三态结果卡。
// mock 恒走 fallbackChat（Workbench 自建列表），copilot 路径不在 e2e 覆盖面内。
import { test, expect, type Page } from "@playwright/test";

const Q = "帮我诊断EKS容器内存持续增长";
const deepLink = (params: Record<string, string>) =>
  `/?q=${encodeURIComponent(Q)}&${new URLSearchParams(params).toString()}`;

/** 落地专属 run → 自动发问 → mock 两段式推进到「五步全绿定格」（rca.status=concluded 后按钮才 armed）。 */
async function landAndDiagnose(page: Page, params: Record<string, string>) {
  await page.goto(deepLink(params));
  await page.waitForURL(/\/agent-runs\/run_demo_/, { timeout: 15_000 });
  await expect(page.getByText(Q)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("diagnosis-progress")).toContainText("诊断完成", { timeout: 15_000 });
}

const button = (page: Page) => page.getByTestId("alert-takeover-button");
const card = (page: Page) => page.getByTestId("alert-takeover-card");
const result = (page: Page) => page.getByTestId("alert-takeover-result");

test("深链主链路：诊断闭环后出按钮 → 确认卡预填 → 确认并入既有规则", async ({ page }) => {
  // agt_pay_fast_recovery 预置通用规则 MySQL[致命]（无监控策略限定）→ 普通级别走合并
  await landAndDiagnose(page, { entry_source: "alert", alert_category: "MySQL", alert_severity: "warning" });
  await expect(button(page)).toBeVisible({ timeout: 15_000 });
  expect(page.url()).not.toContain("entry_source="); // 三参已抹（刷新/分享不重放）
  expect(page.url()).not.toContain("alert_category=");

  await button(page).click();
  await expect(card(page)).toBeVisible();
  await expect(card(page).getByRole("textbox").first()).toHaveValue("MySQL普通告警接管规则");
  await expect(card(page)).toContainText("MySQL");
  await expect(card(page)).toContainText("普通");

  await card(page).getByRole("button", { name: "确认" }).click();
  await expect(result(page)).toBeVisible({ timeout: 10_000 });
  await expect(result(page)).toContainText("已并入现有规则");
  await expect(result(page)).toContainText("致命 → 致命、普通"); // 全集对照，防误解为只剩新级别
  await expect(button(page)).toHaveCount(0); // 终态：本会话按钮不复现
});

test("已被既有规则覆盖：提示无需重复创建", async ({ page }) => {
  await landAndDiagnose(page, { entry_source: "alert", alert_category: "MySQL", alert_severity: "fatal" });
  await button(page).click();
  await expect(card(page).getByRole("textbox").first()).toHaveValue("MySQL致命告警接管规则");
  await card(page).getByRole("button", { name: "确认" }).click();
  await expect(result(page)).toContainText("无需重复创建", { timeout: 10_000 });
});

test("新类型 EKS：创建成功并落进规则配置页", async ({ page }) => {
  await landAndDiagnose(page, { entry_source: "alert", alert_category: "EKS", alert_severity: "fatal" });
  await button(page).click();
  await card(page).getByRole("button", { name: "确认" }).click();
  await expect(result(page)).toContainText("告警接管策略已创建成功", { timeout: 10_000 });
  await expect(result(page)).toContainText("EKS致命告警接管规则");
  await expect(result(page)).toContainText("默认五步诊断");

  // 走 SPA 内导航：整页 goto 会重置 mock 内存态，看不到刚建的规则
  await page.getByTitle("设置").click();
  await expect(page.getByTestId("alerts-rule-row").filter({ hasText: "EKS致命告警接管规则" }))
    .toBeVisible({ timeout: 15_000 });
});

test("未开通白名单：点击直接提示联系管理员，不出表单", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("openops.mock.alertGranted", "0"));
  await landAndDiagnose(page, { entry_source: "alert", alert_category: "MySQL", alert_severity: "warning" });
  await button(page).click();
  await expect(result(page)).toContainText("请联系平台管理员开通");
  await expect(card(page)).toHaveCount(0);
});

test("取消：卡片消失、按钮回位，重开草稿重置", async ({ page }) => {
  await landAndDiagnose(page, { entry_source: "alert", alert_category: "MySQL", alert_severity: "warning" });
  await button(page).click();
  await card(page).getByRole("textbox").first().fill("我改过的名字");
  await card(page).getByRole("button", { name: "取消" }).click();
  await expect(card(page)).toHaveCount(0);
  await expect(button(page)).toBeVisible();

  await button(page).click();
  await expect(card(page).getByRole("textbox").first()).toHaveValue("MySQL普通告警接管规则");
});

test("级别数字写法 alert_severity=1 归一为致命", async ({ page }) => {
  await landAndDiagnose(page, { entry_source: "alert", alert_category: "MySQL", alert_severity: "1" });
  await button(page).click();
  await expect(card(page)).toContainText("致命");
});

test("负例：无告警参数 / 模板外类型 / info 级别均不出按钮", async ({ page }) => {
  await landAndDiagnose(page, {}); // 纯 ?q= 外链，行为与既有一致
  await expect(button(page)).toHaveCount(0);

  await landAndDiagnose(page, { entry_source: "alert", alert_category: "Oracle", alert_severity: "fatal" });
  await expect(button(page)).toHaveCount(0);

  await landAndDiagnose(page, { entry_source: "alert", alert_category: "MySQL", alert_severity: "info" });
  await expect(button(page)).toHaveCount(0);
});

/* ---------------- 持久化语义（2026-08-17 拍板：关页重开可恢复，takeoverStore） ----------------
 * mock 约束：reload 后 demo rca 重置为进行中 → 「按钮直接复现」不可测（真实环境由 /state 恢复
 * concluded）；「结果卡直接复现」可测——HYDRATE 不依赖 rca。按钮复现用「reload 后再闭环一轮」验证。 */

const composer = (page: Page) => page.getByPlaceholder(/描述你的排障任务/);
const concludeOnceMore = async (page: Page, text: string) => {
  await composer(page).fill(text);
  await composer(page).press("Enter");
  await expect(page.getByTestId("diagnosis-progress")).toContainText("诊断完成", { timeout: 15_000 });
};

test("R1 防重放：确认后刷新——结果卡复现、按钮不复现；再闭环一轮仍打不动", async ({ page }) => {
  await landAndDiagnose(page, { entry_source: "alert", alert_category: "MySQL", alert_severity: "warning" });
  await button(page).click();
  await card(page).getByRole("button", { name: "确认" }).click();
  await expect(result(page)).toBeVisible({ timeout: 10_000 });

  await page.reload();
  await expect(page.getByText("活动 · 调查时间线")).toBeVisible({ timeout: 15_000 });
  await expect(result(page)).toBeVisible({ timeout: 10_000 }); // HYDRATE 恢复（不依赖 rca）
  await expect(result(page)).toContainText("已并入现有规则");
  await expect(button(page)).toHaveCount(0);

  await concludeOnceMore(page, "追问一轮"); // rca 重新闭环也打不动终态（防重放 L2）
  await expect(button(page)).toHaveCount(0);
  await expect(result(page)).toBeVisible();
});

test("R2 ctx 恢复：闭环后不点就刷新——再闭环一轮按钮复现且预填正确", async ({ page }) => {
  await landAndDiagnose(page, { entry_source: "alert", alert_category: "MySQL", alert_severity: "warning" });
  await expect(button(page)).toBeVisible({ timeout: 15_000 });

  await page.reload();
  await expect(page.getByText("活动 · 调查时间线")).toBeVisible({ timeout: 15_000 });
  await expect(button(page)).toHaveCount(0); // demo rca 重置为进行中——未闭环不 armed

  await concludeOnceMore(page, "继续排查");
  await expect(button(page)).toBeVisible({ timeout: 10_000 }); // ctx 从 takeoverStore 恢复
  await button(page).click();
  await expect(card(page).getByRole("textbox").first()).toHaveValue("MySQL普通告警接管规则");
});

test("R3 会话隔离：SPA 切走按钮/卡不残留，切回恢复终态", async ({ page }) => {
  await landAndDiagnose(page, { entry_source: "alert", alert_category: "MySQL", alert_severity: "warning" });
  await button(page).click();
  await card(page).getByRole("button", { name: "确认" }).click();
  await expect(result(page)).toBeVisible({ timeout: 10_000 });

  // SPA 内切到历史会话（禁整页 goto——会重置 mock 内存态）
  await page.getByText("对账任务超时排查").click();
  await expect(result(page)).toHaveCount(0);
  await expect(button(page)).toHaveCount(0);

  await page.goBack();
  await expect(result(page)).toBeVisible({ timeout: 10_000 }); // re-key 恢复（RESET→HYDRATE）
  await expect(result(page)).toContainText("已并入现有规则");
});

test("R4 未开通不锁死：拦截后刷新再闭环按钮复现，开通后可走到确认卡", async ({ page }) => {
  // sessionStorage 标记让 init script 可被运行时「开通」动作关掉（否则每次导航都重置回未开通）
  await page.addInitScript(() => {
    if (!sessionStorage.getItem("e2e.grant.restored")) {
      localStorage.setItem("openops.mock.alertGranted", "0");
    }
  });
  await landAndDiagnose(page, { entry_source: "alert", alert_category: "MySQL", alert_severity: "warning" });
  await button(page).click();
  await expect(result(page)).toContainText("请联系平台管理员开通");

  await page.reload();
  await expect(page.getByText("活动 · 调查时间线")).toBeVisible({ timeout: 15_000 });
  await expect(result(page)).toHaveCount(0); // not_granted 不持久化

  await concludeOnceMore(page, "再来一轮");
  await expect(button(page)).toBeVisible({ timeout: 10_000 }); // 未被终态锁死

  // 管理员开通后（access 在 ctx 恢复时探询过，需重进页面刷新探询结果）→ 可走到确认卡
  await page.evaluate(() => {
    sessionStorage.setItem("e2e.grant.restored", "1");
    localStorage.removeItem("openops.mock.alertGranted");
  });
  await page.reload();
  await expect(page.getByText("活动 · 调查时间线")).toBeVisible({ timeout: 15_000 });
  await concludeOnceMore(page, "开通后再试");
  await expect(button(page)).toBeVisible({ timeout: 10_000 });
  await button(page).click();
  await expect(card(page)).toBeVisible(); // 出表单而非未开通卡
});
