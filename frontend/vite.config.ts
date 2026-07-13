import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// real 模式下 /api → 后端 FastAPI（18082）；/api/copilotkit → CopilotKit sidecar（4002，
// Part B：CopilotChat 对话区，`npm run runtime` 启动）。mock 模式（默认）不走网络。
export default defineConfig({
  // 文根部署（内网 xxxx.com/aegisops）：打包时 VITE_OPENOPS_BASE=/aegisops/ 注入；
  // 本地 dev/e2e 不设 → "/"（行为不变）。Router basename / CopilotKit runtimeUrl 均随 BASE_URL。
  base: process.env.VITE_OPENOPS_BASE || "/",
  plugins: [react()],
  server: {
    port: 5175,
    host: "127.0.0.1",
    proxy: {
      "/api/copilotkit": { target: "http://127.0.0.1:4002", changeOrigin: true },
      "/api": { target: "http://127.0.0.1:18082", changeOrigin: true },
    },
  },
});
