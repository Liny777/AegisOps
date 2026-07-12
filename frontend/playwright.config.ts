import { defineConfig } from "@playwright/test";

/**
 * B9 E2E smoke（mock 模式）：vite 起 5178 + VITE_OPENOPS_API_MODE=mock（进程 env 优先于 .env.local），
 * 不依赖后端/PG——验 UI 结构与关键交互（33:213 八场景裁剪）。
 * 真链路（real+backend+审批）在内网/本机全链环境手动跑：OPENOPS_E2E_REAL=1 npx playwright test。
 * ⚠内网离线：`npx playwright install chromium` 需外网下载；离线机用公司 npm 镜像或跳过 e2e（pytest 已覆盖后端面）。
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  workers: 1, // demoIdentity 是模块级单例，串行避免身份互踩
  use: {
    baseURL: "http://127.0.0.1:5178",
    viewport: { width: 1280, height: 800 },
  },
  webServer: {
    command: "npm run dev -- --port 5178 --strictPort",
    url: "http://127.0.0.1:5178",
    reuseExistingServer: !process.env.CI,
    env: { VITE_OPENOPS_API_MODE: "mock" },
    timeout: 60_000,
  },
});
