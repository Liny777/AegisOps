import { test, expect } from "@playwright/test";

/**
 * B9 smoke（mock 模式，33:213 八场景裁剪为可离线跑，现 15 幕）：
 * 对话工作台 / mock 发送 / 初始化向导 / Agent 清单 / 管理台 forbidden→admin 切换→模板编辑器 / 审计页 /
 * InitGuard 弹回 / 新建 ?new=1 旁路 / 编辑向导预填保存 / 删光后新建 picker 兜底重拉 / 插件页两 tab /
 * 外链 ?q= 三态（已初始化自动发送·未初始化向导保留·无权限引导页保留） / 审批卡批准后自动淡出。
 * 约束：demoIdentity 是模块级（full reload 重置为普通用户）——管理员流程必须 SPA 内导航（历史坑）；
 * mock module 态（mockAgents 等）每个 test 新 page 即重置，编辑幕的改名不会泄漏到其他幕。
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

test("审批卡：工具名可见 → 点击批准 → 显示已批准 → 自动淡出", async ({ page }) => {
  await page.goto("/"); // mock 工作台自带一张 pending 审批卡（recover_execute）
  await expect(page.getByText("需要人工批准")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("recover_execute").first()).toBeVisible(); // 在批哪个工具，一眼可见
  await page.getByRole("button", { name: "批准", exact: true }).click();
  await expect(page.getByText(/已批准/)).toBeVisible({ timeout: 3_000 }); // 原地显结果
  await expect(page.getByText("需要人工批准")).toHaveCount(0, { timeout: 5_000 }); // ~2.2s 后自动淡出
});

test("初始化向导：三步骨架（2/3/4 已合并为「配置 Agent」）", async ({ page }) => {
  // InitGuard（38f91c8）：有实例访问 /init 会被弹回工作台——本幕用「全新用户」缝进向导
  await page.addInitScript(() => localStorage.setItem("openops.mock.fresh", "1"));
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
  // 编排对称化：main skills 白名单（目录勾选 chips，mock 技能目录 3 键）+ main MCP 绑定区在位
  await expect(page.getByText(/main skills 白名单/)).toBeVisible();
  await expect(page.getByText("inspection", { exact: true }).first()).toBeVisible(); // 技能目录 chip
  await expect(page.getByText(/main 平台 MCP tool 绑定/)).toBeVisible();
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

test("InitGuard：已有 Agent 访问 /init 直接跳工作台（不重复初始化）", async ({ page }) => {
  await page.goto("/init"); // 默认 demo 身份自带实例
  await page.waitForURL(/\/agent-teams\/.+\/chat/, { timeout: 15_000 });
  await expect(page.getByText("活动 · 调查时间线")).toBeVisible({ timeout: 15_000 });
});

test("新建 Agent：清单页按钮 ?new=1 旁路 InitGuard 直达向导（老用户建第二个实例）", async ({ page }) => {
  await page.goto("/agents"); // 默认 demo 身份**有实例**——本幕即守卫旁路断言
  await page.getByRole("button", { name: "新建 Agent" }).click();
  await page.waitForURL(/\/init\?new=1/, { timeout: 15_000 });
  await expect(page.getByText("选择模板").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("初始化 Agent")).toBeVisible(); // 创建态标题（非编辑态）
});

test("编辑 Agent：卡片编辑 → 向导预填名称 → 改名保存 → 清单反映新名", async ({ page }) => {
  // mockAgents[1] 的 ws_gw 不在 mockWorkspaces（预填选不中范围卡），幕固定用 .first()（支付域感知快恢）
  await page.goto("/agents");
  await page.getByText("编辑", { exact: true }).first().click();
  await page.waitForURL(/\/agent-teams\/.+\/edit/, { timeout: 15_000 });
  await expect(page.getByText("编辑 Agent")).toBeVisible(); // 编辑态标题 + 模板步锁定提示
  await expect(page.getByText(/编辑模式下模板不可更换/)).toBeVisible();
  await page.getByRole("button", { name: "下一步" }).click(); // 预填 tplId 使按钮可点（auto-wait）
  await expect(page.getByPlaceholder(/感知快恢Agent/)).toHaveValue("支付域感知快恢", { timeout: 10_000 });
  await page.getByPlaceholder(/感知快恢Agent/).fill("支付域感知快恢-改");
  await page.getByRole("button", { name: "下一步" }).click();
  await page.getByRole("button", { name: "保存修改" }).click(); // mock updateAgentTeam 原地改 module 态
  await page.waitForURL(/\/agents/, { timeout: 15_000 });
  await expect(page.getByText("支付域感知快恢-改").first()).toBeVisible({ timeout: 15_000 });
});

test("删光后新建：向导激活 → 进对话页侧栏 picker 即显新 Agent（无需手动刷新）", async ({ page }) => {
  // 全新用户（=全部删光的形状：listAgents 为空）走完创建 → useSyncCurrentAgent 兜底重拉
  // 不能被 agents.length>0 门拦住（实测 bug：picker 一直「选择 Agent」直到 F5）
  await page.addInitScript(() => localStorage.setItem("openops.mock.fresh", "1"));
  await page.goto("/init");
  await page.getByRole("button", { name: "下一步" }).click();
  await page.getByPlaceholder(/感知快恢Agent/).fill("定界Agent");
  await page.getByText("支付核心域").click(); // 选中唯一 mock workspace
  await page.getByRole("button", { name: "下一步" }).click();
  await page.getByRole("button", { name: "激活 Agent" }).click(); // mock 建完清 fresh 缝
  await page.waitForURL(/\/agent-teams\/.+\/chat/, { timeout: 15_000 });
  // 侧栏 Agent 选择器（title="选择 Agent"）应显出新实例名，而非空态占位
  await expect(page.getByTitle("选择 Agent")).toContainText("支付域感知快恢", { timeout: 15_000 });
});

test("插件页仅 Skill/MCP 两 tab（模型配置/角色提示词已并入向导）", async ({ page }) => {
  await page.goto("/agent-teams/agt_pay_fast_recovery/settings");
  await expect(page.getByText("Skill 配置")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("MCP 配置")).toBeVisible();
  await expect(page.getByText("模型配置")).toHaveCount(0);
  await expect(page.getByText("角色提示词")).toHaveCount(0);
});

test("外链 ?q= 已初始化：新建专属会话 → 自动发送 → URL 清净 → 刷新不重发", async ({ page }) => {
  const q = "外链自动发问-支付网关P0告警";
  await page.goto(`/?q=${encodeURIComponent(q)}`);
  await page.waitForURL(/\/agent-runs\/run_demo_/, { timeout: 15_000 }); // ExternalJump 专属新 run
  await expect(page.getByText(q)).toBeVisible({ timeout: 15_000 }); // 问题成为 user 气泡
  await expect(page.getByText(/任务已受理/)).toBeVisible({ timeout: 10_000 }); // mock 回执
  expect(page.url()).not.toContain("q="); // 地址栏已抹掉（刷新/分享不重发）
  await page.reload();
  await expect(page.getByText("活动 · 调查时间线")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(q)).toHaveCount(0); // sessionStorage 已消费：刷新不重发
});

test("外链 ?q= 未初始化：进向导 + 「问题已保留」提示在位", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("openops.mock.fresh", "1"));
  await page.goto(`/?q=${encodeURIComponent("外链带来的问题X")}`);
  await page.waitForURL(/\/init/, { timeout: 15_000 });
  await expect(page.getByText("选择模板").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("你带来的问题已保留")).toBeVisible();
  await expect(page.getByText("外链带来的问题X")).toBeVisible();
});

test("外链 ?q= 无权限：开通引导页 + 「问题已保留」提示在位", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("openops.mock.nowl", "1"));
  await page.goto(`/?q=${encodeURIComponent("外链带来的问题Y")}`);
  await expect(page.getByText("尚未开通 OpenOps")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("你带来的问题已保留")).toBeVisible();
  await expect(page.getByText("外链带来的问题Y")).toBeVisible();
  expect(page.url()).not.toContain("q=");
});
