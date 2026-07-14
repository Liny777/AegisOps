import { defineConfig } from "@playwright/test";

const realMode = process.env.OPENOPS_E2E_REAL === "1";
const externalBaseUrl = process.env.OPENOPS_E2E_BASE_URL;
const baseURL = externalBaseUrl || "http://127.0.0.1:5178";

/**
 * B9 E2E smoke（mock 模式）：vite 起 5178 + VITE_OPENOPS_API_MODE=mock（进程 env 优先于 .env.local），
 * 不依赖后端/PG——验 UI 结构与关键交互（33:213 八场景裁剪）。
 * 真链路需先启动 backend(:18082) 与 CopilotKit sidecar(:4002)，再执行 npm run e2e:real；
 * 也可用 OPENOPS_E2E_BASE_URL 指向已启动的真实前端，此时 Playwright 不再自行启动 Vite。
 * ⚠内网离线：`npx playwright install chromium` 需外网下载；离线机用公司 npm 镜像或跳过 e2e（pytest 已覆盖后端面）。
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  workers: 1, // demoIdentity 是模块级单例，串行避免身份互踩
  use: {
    baseURL,
    viewport: { width: 1280, height: 800 },
  },
  webServer: externalBaseUrl ? undefined : {
    command: "npm run dev -- --port 5178 --strictPort",
    url: baseURL,
    // real 模式拒绝复用可能由上一次 mock 测试遗留的 Vite，避免假阳性。
    reuseExistingServer: !process.env.CI && !realMode,
    env: { VITE_OPENOPS_API_MODE: realMode ? "real" : "mock" },
    timeout: 60_000,
  },
});
