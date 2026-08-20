import "./lib/compat/polyfills"; // 必须第一行：老 WebView 的 API 兜底要先于所有依赖模块求值
import "@fontsource-variable/geist"; // 32 号规范：字体族 Geist Variable，fallback Inter
import "@fontsource-variable/inter";
import "@tabler/icons-webfont/dist/tabler-icons.css";
import "./theme/global.css";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { GlobalErrorBoundary } from "./lib/bootGuard/GlobalErrorBoundary";
import { installGlobalHandlers } from "./lib/bootGuard/installGlobalHandlers";

installGlobalHandlers();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <GlobalErrorBoundary>
      <App />
    </GlobalErrorBoundary>
  </React.StrictMode>,
);
