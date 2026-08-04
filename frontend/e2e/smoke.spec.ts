import { test, expect } from "@playwright/test";

/**
 * B9 smoke（mock 模式，33:213 八场景裁剪为可离线跑，现 15 幕）：
 * 对话工作台 / mock 发送 / 初始化向导 / Agent 清单 / 管理台 forbidden→admin 切换→模板编辑器 / 审计页 /
 * InitGuard 弹回 / 新建 ?new=1 旁路 / 编辑向导预填保存 / 删光后新建 picker 兜底重拉 / 插件页两 tab /
 * 外链 ?q= 三态（已初始化自动发送·未初始化向导保留·无权限引导页保留） / 审批卡批准后自动淡出。
 * 约束：demoIdentity 由 boot 期身份种子决定——默认普通用户，管理员流程用 `?as=admin` 或
 * addInitScript 置 localStorage['openops.demo.user']='admin'（持久到 localStorage，跨整页刷新有效；
 * 每 test 新 context 隔离，不泄漏到其他幕）；「进入管理台」入口仅平台管理员可见；
 * mock module 态（mockAgents 等）每个 test 新 page 即重置，编辑幕的改名不会泄漏到其他幕。
 */

test("对话工作台：多 Agent 轮次、技术轨与两 tab 面板", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("活动 · 调查时间线")).toBeVisible({ timeout: 15_000 });
  // 「全部动态」tab 已随内网反馈下线，右栏只剩「诊断 | 子 Agent」两 tab
  await expect(page.getByRole("tab", { name: "全部动态" })).toHaveCount(0);
  // 诊断优先：mock 自带诊断数据，载入自动落在「诊断」tab（子 Agent 自动切换不再抢占）
  await expect(page.getByRole("tab", { name: "诊断" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "子 Agent" }).click();
  await expect(page.getByText(/子 Agent 编排 · 2 轮/)).toBeVisible();
  await expect(page.getByRole("button", { name: /第 2 轮/ })).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("button", { name: /第 1 轮/ })).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByText("待审批").first()).toBeVisible();
  await page.getByRole("button", { name: /第 1 轮/ }).click();
  await expect(page.getByRole("img", { name: /编排图/ })).toHaveCount(2);
  await expect(page.getByRole("region", { name: /巡检 Agent 活动轨迹/ })).toHaveCount(2);
  await expect(page.getByText("已超时").first()).toBeVisible();
  await expect(page.getByText("已取消").first()).toBeVisible();
  await page.getByRole("button", { name: /查看 巡检 Agent 的活动轨迹/ }).first().click();
  await expect(page.getByRole("region", { name: /巡检 Agent 活动轨迹/ }).first()).toBeVisible();
  // 子 Agent 恒为技术轨：业务/技术视图切换钮已删
  await expect(page.getByRole("button", { name: "业务", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "技术", exact: true })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "子 Agent" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText(/dom-secret-must-not-render|dom-cookie-must-not-render/)).toHaveCount(0);
  await expect(page.getByPlaceholder(/描述你的排障任务/)).toBeVisible();
  await page.reload();
  // 刷新后 manualTabChoice 复位 → 回到默认「诊断」tab
  await expect(page.getByRole("tab", { name: "诊断" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "子 Agent" }).click();
  await expect(page.getByText(/子 Agent 编排 · 2 轮/)).toBeVisible();
});

test("窄屏活动栏以右侧覆盖层展示", async ({ page }) => {
  await page.setViewportSize({ width: 820, height: 900 });
  await page.goto("/");
  const rail = page.getByLabel("活动 · 调查时间线");
  await expect(rail).toBeVisible({ timeout: 15_000 });
  await expect(rail).toHaveCSS("position", "fixed");
  await expect(rail).toHaveCSS("right", "0px");
});

test("mock 发送：回执气泡出现", async ({ page }) => {
  await page.goto("/");
  const input = page.getByPlaceholder(/描述你的排障任务/);
  await input.waitFor({ timeout: 15_000 });
  await input.fill("查一下支付链路状态");
  await input.press("Enter");
  await expect(page.getByText(/任务已受理/)).toBeVisible({ timeout: 10_000 });
});

test("设置页往返：回复在后台继续且当前对话无损保留", async ({ page }) => {
  const question = "页内往返保留-支付回归-7f3a";
  const receiptText = "（mock 演示）任务已受理。";

  await page.goto("/");
  const input = page.getByPlaceholder(/描述你的排障任务/);
  await input.waitFor({ timeout: 15_000 });
  const chatUrl = page.url();
  const questionBubble = page.getByText(question, { exact: true });
  const receiptBubble = page.getByText(receiptText, { exact: true });
  const hitlTitle = page.getByText("需要人工批准", { exact: true });
  const liveBadge = page.getByText("实时", { exact: true });

  await expect(hitlTitle).toBeVisible();
  await expect(liveBadge).toBeVisible();
  await input.fill(question);
  await input.press("Enter");
  await expect(questionBubble).toBeVisible();
  await expect(page.getByText(/Agent 正在调查/)).toBeVisible();

  // 在 900ms mock 回执到来前，通过真实侧栏入口离开对话页。
  await page.getByTitle("设置").click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByRole("main").getByText("设置", { exact: true })).toBeVisible();

  // Workbench 仍在 DOM 中继续任务，但消息、连接徽标和 HITL 均不能覆盖设置页。
  await expect(questionBubble).toHaveCount(1);
  await expect(questionBubble).toBeHidden();
  await expect(hitlTitle).toBeHidden();
  await expect(liveBadge).toBeHidden();
  await expect(receiptBubble).toHaveCount(1, { timeout: 5_000 });
  await expect(receiptBubble).toBeHidden();

  // 浏览器返回同一 chat 路由：原消息和后台完成的回执恢复，且没有重复或空白跳点态。
  await page.goBack();
  await expect(page).toHaveURL(chatUrl);
  await expect(page.locator(".oa-fallback-chat-list")).toBeVisible();
  await expect(questionBubble).toBeVisible();
  await expect(questionBubble).toHaveCount(1);
  await expect(receiptBubble).toBeVisible();
  await expect(receiptBubble).toHaveCount(1);
  await expect(hitlTitle).toBeVisible();
  await expect(liveBadge).toBeVisible();
  await expect(page.getByText("连接中", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Agent 正在调查/)).toHaveCount(0);
});

test("管理台往返不会卸载后台运行的对话（管理员）", async ({ page }) => {
  const question = "管理台往返保留-4d91";
  const receipt = page.getByText("（mock 演示）任务已受理。", { exact: true });

  // 管理员身份种子：入口仅平台管理员可见
  await page.addInitScript(() => localStorage.setItem("openops.demo.user", "admin"));
  await page.goto("/");
  const input = page.getByPlaceholder(/描述你的排障任务/);
  await input.fill(question);
  await input.press("Enter");
  await expect(page.getByText(question, { exact: true })).toBeVisible();

  await page.getByTitle("进入管理台").click();
  await expect(page.getByRole("main").getByText("模板管理")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".oa-retained-workbench")).toBeHidden();
  await expect(receipt).toHaveCount(1, { timeout: 5_000 });
  await expect(receipt).toBeHidden();

  await page.getByTitle("返回工作台").click();
  await expect(page.getByText(question, { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(question, { exact: true })).toHaveCount(1);
  await expect(receipt).toBeVisible();
  await expect(receipt).toHaveCount(1);
  await expect(page.getByText("实时", { exact: true })).toBeVisible();
});

test("紧凑消息：复制按钮可用，窄屏 RCA/HITL 自动降列", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/");

  const botBody = page.locator(".oa-fallback-bot-body").filter({ hasText: "初步诊断" });
  await botBody.hover();
  const copyButton = botBody.getByRole("button", { name: "复制消息" });
  await expect(copyButton).toHaveCSS("position", "absolute");
  await expect(copyButton).toHaveCSS("width", "28px");
  await copyButton.click();
  await expect(botBody.getByRole("button", { name: "已复制" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain("初步诊断");

  await page.setViewportSize({ width: 700, height: 900 });
  // 诊断时间线挂在右侧面板「诊断」tab（默认选中）：窄屏 overlay 面板容器 420px（<520px）
  // → 容器查询降列 tiles 2 列 / facts 1 列（facts 在步 2 展开态内，先点开步卡）；
  // HITL 卡仍在对话流，按视口媒体查询降列。
  const rail = page.getByLabel("活动 · 调查时间线");
  await expect(rail.locator(".oa-rca-tiles")).toBeVisible();
  await expect.poll(() => rail.locator(".oa-rca-tiles").evaluate((el) =>
    getComputedStyle(el).gridTemplateColumns.split(" ").length,
  )).toBe(2);
  await rail.getByTestId("diagnosis-step-2").click();
  await expect(rail.locator(".oa-rca-facts")).toBeVisible();
  await expect.poll(() => rail.locator(".oa-rca-facts").evaluate((el) =>
    getComputedStyle(el).gridTemplateColumns.split(" ").length,
  )).toBe(1);
  await expect.poll(() => page.locator(".oa-hitl-facts").evaluate((el) =>
    getComputedStyle(el).gridTemplateColumns.split(" ").length,
  )).toBe(1);

  await page.setViewportSize({ width: 460, height: 900 });
  await expect(page.locator(".oa-fallback-chat-list")).toHaveCSS("padding-left", "12px");
  await expect(page.locator(".oa-fallback-chat-list")).toHaveCSS("padding-right", "12px");
});

test("审批卡：工具名可见 → 点击批准 → 显示已批准 → 自动淡出", async ({ page }) => {
  await page.goto("/"); // mock 工作台自带一张 pending 审批卡（recover_execute）
  await expect(page.getByText("需要人工批准")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("recover_execute").first()).toBeVisible(); // 在批哪个工具，一眼可见
  await page.getByRole("button", { name: "批准", exact: true }).click();
  await expect(page.getByText(/已批准/)).toBeVisible({ timeout: 3_000 }); // 原地显结果
  await expect(page.getByText("需要人工批准")).toHaveCount(0, { timeout: 5_000 }); // ~2.2s 后自动淡出
});

test("初始化向导：三步骨架（配置 → 确认能力清单 → 激活）", async ({ page }) => {
  // InitGuard（38f91c8）：有实例访问 /init 会被弹回工作台——本幕用「全新用户」缝进向导
  await page.addInitScript(() => localStorage.setItem("openops.mock.fresh", "1"));
  await page.goto("/init");
  // 三步名（删了「选择模板」页，step0 即配置）
  await expect(page.getByText("确认能力清单").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("激活 Agent").first()).toBeVisible();
  // step0 即配置页：四区块关键元素直接在位（无需先点下一步）
  await expect(page.getByPlaceholder(/感知快恢Agent/)).toBeVisible();
  await expect(page.getByText("身份确认")).toBeVisible();
  await expect(page.getByText("模型供应商")).toBeVisible();
  await expect(page.getByText("系统看护范围")).toBeVisible();
  // 38 号：模型段改为「选一套模型模板」——默认模板卡（is_default→「默认推荐」徽标）+ 主/子槽位行可见
  await expect(page.getByText("均衡（推荐）")).toBeVisible();
  await expect(page.getByText("默认推荐")).toBeVisible();
  await expect(page.getByText(/主 Agent：GLM-5\.1 · 子 Agent：GLM-5\.1/)).toBeVisible();
  // 停用模板不进选单（mock 清单含 1 条 disabled 行，前端按 active 过滤）
  await expect(page.getByText("旧组合（停用）")).toHaveCount(0);
  // BYO 收进高级折叠区：默认收起（按钮不可见）→ 点开后「添加自定义模型」出现
  await expect(page.getByRole("button", { name: "添加自定义模型" })).toHaveCount(0);
  await page.getByText(/高级选项：接入自带模型/).click();
  await expect(page.getByRole("button", { name: "添加自定义模型" })).toBeVisible();
});

test("管理台模型模板页：表格 + 授权（38.1）+ 新建弹窗（主/子槽位下拉）", async ({ page }) => {
  await page.goto("/admin/model-templates?as=admin");
  // 表格：种子行 + 默认徽标 + 主/子模型列
  await expect(page.getByRole("main").getByText("均衡（推荐）")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("main").getByText("经济", { exact: true })).toBeVisible();
  // 「默认」出现两处（表头列名 + 均衡行徽标）——用 count 断言避免 strict mode 撞车
  await expect(page.getByRole("main").getByText("默认", { exact: true })).toHaveCount(2);
  // 「设默认」= 表头 + 三个非默认行动作（经济 / 交易专用（受限演示）/ 旧组合）
  await expect(page.getByRole("main").getByText("设默认", { exact: true })).toHaveCount(4);
  // 38.1 授权范围列：all → 全员开放徽标 ×3；restricted 演示行 → 限 1 人
  await expect(page.getByRole("main").getByText("交易专用（受限演示）")).toBeVisible();
  await expect(page.getByRole("main").getByText("限 1 人")).toBeVisible();
  await expect(page.getByRole("main").getByText("全员开放").first()).toBeVisible();
  // 38.1 白名单授权弹窗：restricted 行（mock 第 3 行）回填「限定人员」
  // AdminConsole 表格是 div 网格无 row role——按动作列出现次序定位（mock 行序固定：均衡/经济/交易专用/旧组合）
  await page.getByRole("main").getByText("白名单授权", { exact: true }).nth(2).click();
  await expect(page.getByText(/白名单授权 · 交易专用（受限演示）/)).toBeVisible();
  await expect(page.getByRole("radio").nth(1)).toBeChecked(); // restricted 回填
  await page.getByRole("button", { name: "取消" }).click();
  // 新建弹窗：模板名输入 + 主/子 Agent 模型两个下拉 + 授权范围 radio + 全局默认勾选
  await page.getByRole("button", { name: "新建模板" }).click();
  await expect(page.getByPlaceholder(/模板名/)).toBeVisible();
  await expect(page.getByText("主 Agent 模型（任务理解与编排）")).toBeVisible();
  await expect(page.getByText(/子 Agent 模型（巡检/)).toBeVisible();
  await expect(page.getByText(/限定人员（创建后在列表/)).toBeVisible();
  await expect(page.getByText(/设为全局默认/)).toBeVisible();
});

test("管理台删除（38.2）：模板可删；被模板引用的资产拒删显横幅", async ({ page }) => {
  page.on("dialog", (d) => void d.accept()); // 两处 window.confirm 全接受
  await page.goto("/admin/model-templates?as=admin");
  await expect(page.getByRole("main").getByText("旧组合（停用）")).toBeVisible({ timeout: 15_000 });
  // 删「旧组合（停用）」（第 4 行）→ 行消失（mock splice 写闭环）
  await page.getByRole("main").getByText("删除", { exact: true }).nth(4).click(); // nth(0)=表头
  await expect(page.getByRole("main").getByText("旧组合（停用）")).toHaveCount(0);
  // 资产页：GLM-5.1 被 mock 模板引用 → 拒删 + 错误横幅提示先调整模板
  // exact:true 区分大小写：裸 "GLM-5.1" 会同时命中 model_id 列的小写 glm-5.1（getByText 串匹配大小写不敏感）
  await page.goto("/admin/model-assets?as=admin");
  await expect(page.getByRole("main").getByText("GLM-5.1", { exact: true })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("main").getByText("删除", { exact: true }).nth(1).click(); // nth(0)=表头，行序 GLM 第一
  await expect(page.getByText(/该模型被模型模板引用/)).toBeVisible();
  await expect(page.getByRole("main").getByText("GLM-5.1", { exact: true })).toBeVisible(); // 未被误删
});

test("全部 Agents 清单（/agents）", async ({ page }) => {
  await page.goto("/agents");
  await expect(page.getByText("支付域感知快恢").first()).toBeVisible({ timeout: 15_000 });
});

test("管理台：普通用户 403 + 隐藏入口 → 管理员种子进入 → 模板编辑器（含 S1 新增角色入口）", async ({ page }) => {
  // 普通用户（默认身份，无种子）：直开 /admin → 403
  await page.goto("/admin/templates");
  await expect(page.getByText("403 · 无权访问")).toBeVisible({ timeout: 15_000 });
  // Forbidden 页自带「返回工作台」（真 button）→ 回工作台；侧栏入口对普通用户不可见
  await page.getByRole("button", { name: "返回工作台" }).click();
  await expect(page.getByText("活动 · 调查时间线")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("进入管理台")).toHaveCount(0);
  // 管理员身份种子（?as=admin 持久到 localStorage）→ 侧栏入口出现，点击进管理台
  await page.goto("/?as=admin");
  await expect(page.getByText("进入管理台")).toBeVisible({ timeout: 15_000 });
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

test("审计页：Trace 过滤输入在位（管理员）", async ({ page }) => {
  await page.goto("/admin/audit?as=admin"); // 管理员种子 → RoleGuard 放行，直达审计页
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
  await expect(page.getByPlaceholder(/感知快恢Agent/)).toBeVisible({ timeout: 15_000 }); // 直达配置页（删了选择模板页）
  await expect(page.getByText("运维Agent", { exact: true })).toBeVisible();
});

test("编辑 Agent：卡片编辑 → 向导预填名称 → 改名保存 → 清单反映新名", async ({ page }) => {
  // mockAgents[1] 的 ws_gw 不在 mockWorkspaces（预填选不中范围卡），幕固定用 .first()（支付域感知快恢）
  await page.goto("/agents");
  await page.getByText("编辑", { exact: true }).first().click();
  await page.waitForURL(/\/agent-teams\/.+\/edit/, { timeout: 15_000 });
  await expect(page.getByText("运维Agent", { exact: true })).toBeVisible();
  // step0 即配置页（删了选择模板页与锁定提示），名称已预填
  await expect(page.getByPlaceholder(/感知快恢Agent/)).toHaveValue("支付域感知快恢", { timeout: 10_000 });
  await page.getByPlaceholder(/感知快恢Agent/).fill("支付域感知快恢-改");
  await page.getByRole("button", { name: "下一步" }).click();  // → step1 确认能力清单
  await page.getByRole("button", { name: "下一步" }).click();  // → step2 激活（auto-wait OModel loading 完按钮 enable）
  await page.getByRole("button", { name: "保存修改" }).click(); // mock updateAgentTeam 原地改 module 态
  await page.waitForURL(/\/agents/, { timeout: 15_000 });
  await expect(page.getByText("支付域感知快恢-改").first()).toBeVisible({ timeout: 15_000 });
});

test("删光后新建：向导激活 → 进对话页侧栏 picker 即显新 Agent（无需手动刷新）", async ({ page }) => {
  // 全新用户（=全部删光的形状：listAgents 为空）走完创建 → useSyncCurrentAgent 兜底重拉
  // 不能被 agents.length>0 门拦住（实测 bug：picker 一直「选择 Agent」直到 F5）
  await page.addInitScript(() => localStorage.setItem("openops.mock.fresh", "1"));
  await page.goto("/init");
  // step0 即配置页（删了选择模板页）：填名称 + 选范围
  await page.getByPlaceholder(/感知快恢Agent/).fill("定界Agent");
  await page.getByText("支付核心域").click(); // 选中唯一 mock workspace
  await page.getByRole("button", { name: "下一步" }).click();  // → step1 确认能力清单（OModel loading 2s）
  await page.getByRole("button", { name: "下一步" }).click();  // → step2 激活（auto-wait loading 完按钮 enable）
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
  await expect(page.getByPlaceholder(/感知快恢Agent/)).toBeVisible({ timeout: 15_000 }); // 直达配置页（删了选择模板页）
  await expect(page.getByText("你带来的问题已保留")).toBeVisible();
  await expect(page.getByText("外链带来的问题X")).toBeVisible();
});

test("外链 ?q= 无权限：开通引导页 + 「问题已保留」提示在位", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("openops.mock.nowl", "1"));
  await page.goto(`/?q=${encodeURIComponent("外链带来的问题Y")}`);
  await expect(page.getByText("尚未开通 运维Agent")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("你带来的问题已保留")).toBeVisible();
  await expect(page.getByText("外链带来的问题Y")).toBeVisible();
  expect(page.url()).not.toContain("q=");
});
