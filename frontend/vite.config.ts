import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// real 模式下 /api → 后端 FastAPI mock（18082）。mock 模式（默认）不走网络。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    host: "127.0.0.1",
    proxy: {
      "/api": { target: "http://127.0.0.1:18082", changeOrigin: true },
    },
  },
});
