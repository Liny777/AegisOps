// index.html 内联哨兵定义的静态兜底 UI 入口（React 挂载后自动 no-op，见 index.html）。
declare global {
  interface Window {
    __openopsShowFatal?: (msg: string) => void;
  }
}

/** main.tsx 渲染前调用一次：把「React 树外」的致命错误从纯白屏变成可读提示。
 *  分工：渲染期错误归 GlobalErrorBoundary；这里只兜 React 未挂载/已卸载的场景与
 *  Vite 官方 preloadError（路由预取 chunk 失败）。 */
export function installGlobalHandlers(): void {
  // capture 相：<script>/<link> 资源加载失败的 error 事件不冒泡，冒泡相根本收不到
  window.addEventListener(
    "error",
    (e) => {
      console.error("[OpenOps][global-error]", e.message || e, e.error);
      maybeShowStaticFatal(e.message || "页面脚本执行失败");
    },
    true,
  );
  // 挂载后偶发 rejection ≠ 应用死亡（CopilotKit/rxjs 有零星无主 rejection）：log-only，
  // 只有 React 未挂载时才升级为静态兜底——不把可用页面变错误页
  window.addEventListener("unhandledrejection", (e) => {
    console.error("[OpenOps][unhandled-rejection]", e.reason);
    maybeShowStaticFatal(e.reason instanceof Error ? e.reason.message : String(e.reason));
  });
  // ⚠刻意不监听 vite:preloadError：preventDefault 会让 Vite 的 preload 助手不再 rethrow，
  // import() 以 undefined resolve → React lazy 读 _result.default 抛 TypeError → 边界当成
  // 非 chunk 错误渲染错误的通用错误页（生产产物实测）。放任 rethrow，lazy 正常 reject 真错误，
  // GlobalErrorBoundary 是 chunk 失败自动 reload 的唯一接管点。
}

function maybeShowStaticFatal(message: string): void {
  const root = document.getElementById("root");
  if (root && root.childElementCount > 0) return; // React 已挂载：交给 ErrorBoundary
  window.__openopsShowFatal?.(message);
}
