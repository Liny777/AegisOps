// IAM 登录往返·深链回位 —— 范式照 autosend.ts / alertEntry.ts：
//
// WeLink 等外部深链（/agent-runs/:id、/alerts/:id）落地时 webview 无 IAM Cookie，首个请求 401
// 跳公司登录页；而 OPENOPS_IAM_LOGIN_URL 的 redirect 回跳白名单写死站点根（/openops/?），
// 登录回来必落 "/"，HomeRedirect 兜底进 /agent-teams/:id/chat——深链被丢（2026-08-20 实锤）。
// 修法：401 跳走前把当前站内路径存 sessionStorage，HomeRedirect 分流时优先恢复。
// [loginreturn] console 信标兼作压缩后部署校验 token（grep loginreturn dist/assets/*.js）。
const KEY = "openops.loginReturn.path";
const TTL_MS = 15 * 60 * 1000; // 登录往返分钟级；过期即弃，防陈旧深链日后劫持首页
const MAX_LEN = 2048;

/** 防御式读 Vite base（生产 /openops，dev ""）：node 直跑单测无 import.meta.env。 */
const basePrefix = (): string =>
  ((import.meta as { env?: { BASE_URL?: string } }).env?.BASE_URL ?? "/").replace(/\/$/, "");

/** 回位无意义的拦截/分流页：首页本就是回跳落点；/not-whitelisted 是守卫兜底页——把它当深链
 *  回位会在「开通后重登」时把已开通用户送回「尚未开通」死端（该页无守卫、不自跳）。 */
const NEVER_RETURN = new Set(["/", "/not-whitelisted"]);

/** 纯函数（单测面）：location 形态 → 剥掉 base 的路由相对路径；拦截页/非法形态/超长 → null。 */
export function toRouterPath(pathname: unknown, search: unknown, base: string): string | null {
  if (typeof pathname !== "string" || !pathname.startsWith("/")) return null;
  let p = pathname;
  if (base && (p === base || p.startsWith(base + "/"))) p = p.slice(base.length) || "/";
  const full = p + (typeof search === "string" ? search : "");
  if (NEVER_RETURN.has(p.replace(/\/+$/, "") || "/") || full.length > MAX_LEN) return null;
  return full;
}

/** 纯函数（单测面）：存储串 → 合法站内路径。过期 / 绝对 URL / "//" "\\" 开头（open redirect）/
 *  坏 JSON → null。now 显著早于 ts（时钟回拨超 1 分钟）同样作废。 */
export function sanitizeStoredReturnPath(raw: string | null, now: number): string | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as { p?: unknown; ts?: unknown };
    if (typeof v.p !== "string" || typeof v.ts !== "number") return null;
    if (now - v.ts > TTL_MS || now < v.ts - 60_000) return null;
    if (!/^\/(?![/\\])/.test(v.p) || v.p.length > MAX_LEN) return null;
    return v.p;
  } catch {
    return null;
  }
}

/** 401 跳登录前调用（redirectToLoginIfUnauthorized 内——client 与 SSE 两通道共用该收口点）。
 *  任何失败都静默：丢了只是回退到现状落首页，不阻塞跳登录。 */
export function captureReturnPath(): void {
  try {
    if (typeof window === "undefined" || typeof sessionStorage === "undefined") return;
    const p = toRouterPath(window.location.pathname, window.location.search, basePrefix());
    if (!p) return;
    sessionStorage.setItem(KEY, JSON.stringify({ p, ts: Date.now() }));
    console.info("[loginreturn] captured return path before IAM redirect:", p);
  } catch {
    /* 隐私模式无 storage：放弃回位 */
  }
}

/** 主动废弃存根（新外部意图入口调用）：上一次未完成登录往返残留的深链，不能劫持这次
 *  带 ?q=/告警三参进来的新意图——否则新问题会被发进旧 run 的既有会话（82e40e6 同类污染）。 */
export function clearReturnPath(): void {
  try {
    if (typeof sessionStorage === "undefined") return;
    if (sessionStorage.getItem(KEY) !== null) {
      sessionStorage.removeItem(KEY);
      console.info("[loginreturn] stale return path cleared by fresh external intent");
    }
  } catch {
    /* 无 storage 即无存根 */
  }
}

/** 取出并清除（一次性；宁丢勿劫持——与 autosend consume 同取舍）。 */
export function consumeReturnPath(): string | null {
  try {
    if (typeof sessionStorage === "undefined") return null;
    const raw = sessionStorage.getItem(KEY);
    if (raw !== null) sessionStorage.removeItem(KEY);
    const p = sanitizeStoredReturnPath(raw, Date.now());
    if (p) console.info("[loginreturn] restoring deep link after IAM round-trip:", p);
    return p;
  } catch {
    return null;
  }
}
