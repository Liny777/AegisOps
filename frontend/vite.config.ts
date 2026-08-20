import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// real 模式下 /api → 后端 FastAPI（18082）；/api/copilotkit → CopilotKit sidecar（4002，
// Part B：CopilotChat 对话区，`npm run runtime` 启动）。mock 模式（默认）不走网络。
export default defineConfig(({ mode }) => {
  // 文根部署（内网 xxxx.com/openops）：打包时注入 VITE_OPENOPS_BASE=/openops/。
  // ⚠必须 loadEnv——vite 不把 .env 文件注入 process.env 给本配置用；loadEnv 兼容两种注入法：
  // shell env（Mac/Linux）与 frontend/.env.production.local（Windows Git Bash 免 MSYS 路径翻译）。
  // 本地 dev/e2e 不设 → "/"（行为不变）。Router basename / CopilotKit runtimeUrl 均随 BASE_URL。
  const env = loadEnv(mode, process.cwd(), "VITE_");
  return {
  base: env.VITE_OPENOPS_BASE || "/",
  build: {
    // WeLink 内置浏览器内核不明（桌面 CEF 可能老至 Chromium 8x）：钉死语法下限，让 esbuild 把
    // ??=/&&=/||= 等 Chrome 85+ 语法（依赖产物常见）转掉——语法级 SyntaxError 发生在模块解析期，
    // ErrorBoundary 兜不住，只有 index.html 哨兵能兜，必须从产物层消除。显式钉值也防将来升
    // Vite 默认 target 上移造成静默回归。实例方法级 API 的兜底见 src/lib/compat/polyfills.ts。
    target: ["chrome80", "edge80", "firefox78", "safari13.1"],
  },
  plugins: [react()],
  server: {
    port: 5175,
    host: "127.0.0.1",
    proxy: {
      "/api/copilotkit": { target: "http://127.0.0.1:4002", changeOrigin: true },
      "/api": { target: "http://127.0.0.1:18082", changeOrigin: true },
    },
  },
  };
});
