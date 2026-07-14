// 外链跳转·自动发问（?q=）—— 老项目 openOps-Dev d4fe4f7→b5c5c79 四提交经验移植：
//
// 外部系统（告警面板等）拼 `<站点>/?q=<encodeURIComponent(问题)>` 跳进来，落地后按用户态分流：
// 无权限 → 开通引导页（问题保留，开通后继续）；有权限未初始化 → 向导（完成后自动发送）；
// 有权限已初始化 → 为该问题**专门新建 run**（防污染既有会话，老项目 82e40e6 实测教训）并自动发送。
//
// 关键时序（b5c5c79 根因）：capture 必须在任何 fetch 的 401→IAM 重定向之前同步执行——
// 先落 sessionStorage 再清 URL，IAM 登录往返回来 URL 无 ?q= 时从 sessionStorage 恢复。
// [autosend] console 信标兼作压缩后部署校验 token（grep autosend dist/assets/*.js）。
const KEY = "openops.autosend.q";
const MAX_LEN = 2000;

/** App 模块加载时同步调用一次：URL ?q= → sessionStorage，并抹掉地址栏（刷新/StrictMode 不重发）。 */
export function captureAutoQuestion(): void {
  if (typeof window === "undefined") return;
  try {
    const sp = new URLSearchParams(window.location.search);
    const raw = sp.get("q")?.trim();
    if (raw) {
      sessionStorage.setItem(KEY, raw.slice(0, MAX_LEN));
      console.info("[autosend] captured ?q= (%d chars)", raw.length);
    } else if (sessionStorage.getItem(KEY)) {
      console.info("[autosend] pending question restored from sessionStorage (IAM 往返)");
    }
    if (sp.has("q")) {
      sp.delete("q");
      const qs = sp.toString();
      window.history.replaceState({}, "", window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash);
    }
  } catch (e) {
    console.warn("[autosend] capture failed:", e); // 隐私模式 sessionStorage 可能不可用——放弃该特性不阻塞进站
  }
}

/** 只读窥视（守卫分流/展示提示用），不清除。 */
export function peekAutoQuestion(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

/** 取出并清除（发送触发点调用）。失败/发送后都不会重放——与老项目 b5c5c79 同取舍：宁丢勿卡死/重发。 */
export function consumeAutoQuestion(): string | null {
  try {
    const q = sessionStorage.getItem(KEY);
    if (q) sessionStorage.removeItem(KEY);
    return q;
  } catch {
    return null;
  }
}
