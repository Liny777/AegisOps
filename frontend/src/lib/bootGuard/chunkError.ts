// 白屏加固·chunk 失效判定与自动刷新节流（纯逻辑，GlobalErrorBoundary 与 vite:preloadError 共用）。
//
// 重新部署后旧 tab 里 lazy chunk 的 hash 失效 → import() reject → Suspense 不兜 reject，
// 整树卸载白屏。对这类错误自动整页 reload 一次拉新 index.html；60s 节流防坏缓存 reload 死循环。

export const RELOAD_KEY = "openops.bootguard.reloadAt"; // 命名对齐 openops.autosend.q 惯例
const RELOAD_WINDOW_MS = 60_000;
const CHUNK_ERROR_PATTERNS = [
  "failed to fetch dynamically imported module", // Chromium
  "importing a module script failed", // WebKit
  "error loading dynamically imported module", // Firefox
  "unable to preload css", // Vite CSS 预载失败
];

export function isChunkLoadError(err: unknown): boolean {
  if (!err) return false;
  const msg = (err instanceof Error ? err.message : String(err)).toLowerCase();
  return CHUNK_ERROR_PATTERNS.some((p) => msg.includes(p));
}

export interface StorageLike {
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
}

/** 自动整页 reload 节流闸：60s 内只放行一次；storage/clock 注入便于单测。
 *  隐私模式 sessionStorage 抛异常 → false（不 reload，直接落错误页）。 */
export function shouldAutoReload(storage: StorageLike, now: number): boolean {
  try {
    const last = Number(storage.getItem(RELOAD_KEY) || 0);
    if (now - last < RELOAD_WINDOW_MS) return false;
    storage.setItem(RELOAD_KEY, String(now));
    return true;
  } catch {
    return false;
  }
}
