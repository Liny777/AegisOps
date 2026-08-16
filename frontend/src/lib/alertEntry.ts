// 告警外链直达（?entry_source=alert）—— 深链上下文捕获，范式照 autosend.ts：
//
// 告警平台拼 `<站点>/?entry_source=alert&alert_category=<类别>&alert_severity=<级别>` 跳进来，
// 落地后由消费方（后续阶段的直达链）据此自动建规/开诊断会话。三参必须整体有效才捕获——
// 缺一或级别非法即整体作废（宁缺勿错：残缺上下文自动建规比不建更糟）。
//
// 关键时序（autosend b5c5c79 同教训）：capture 必须在任何 fetch 的 401→IAM 重定向之前同步执行——
// 先落 sessionStorage 再清 URL，IAM 登录往返回来 URL 无参时从 sessionStorage 恢复。
// [alertentry] console 信标兼作压缩后部署校验 token（grep alertentry dist/assets/*.js）。
const KEY = "openops.alertEntry.ctx";
const PARAMS = ["entry_source", "alert_category", "alert_severity"] as const;

export interface AlertEntryContext {
  source: "alert";
  category: string;
  severity: "fatal" | "critical" | "warning" | "info";
}

/** 级别归一：英文枚举原样过，"1"-"4" 按内网数字表映射（backend/src/infra/external/
 *  alert_inet_contract.py:52-54 LEVEL_TO_SEVERITY 同表）；其余（含 "0"=SLO）→ null。 */
export function normalizeAlertSeverity(raw: string | null): AlertEntryContext["severity"] | null {
  switch (raw?.trim()) {
    case "fatal": case "1": return "fatal";
    case "critical": case "2": return "critical";
    case "warning": case "3": return "warning";
    case "info": case "4": return "info";
    default: return null;
  }
}

/** 纯函数解析（单测面）：entry_source!=="alert" / 缺 alert_category / severity 非法 → null。 */
export function parseAlertEntryParams(search: string): AlertEntryContext | null {
  const sp = new URLSearchParams(search);
  if (sp.get("entry_source") !== "alert") return null;
  const category = sp.get("alert_category")?.trim();
  if (!category) return null;
  const severity = normalizeAlertSeverity(sp.get("alert_severity"));
  if (!severity) return null;
  return { source: "alert", category, severity };
}

/** App 模块加载时同步调用一次：URL 三参 → sessionStorage，并抹掉地址栏（只删自家参数，
 *  ?q= 等他家参数保留；刷新/StrictMode 不重发）。 */
export function captureAlertContext(): void {
  if (typeof window === "undefined") return;
  try {
    const sp = new URLSearchParams(window.location.search);
    const ctx = parseAlertEntryParams(window.location.search);
    if (ctx) {
      sessionStorage.setItem(KEY, JSON.stringify(ctx));
      console.info("[alertentry] captured alert entry (category=%s severity=%s)", ctx.category, ctx.severity);
    } else if (sessionStorage.getItem(KEY) && !PARAMS.some((p) => sp.has(p))) {
      console.info("[alertentry] pending context restored from sessionStorage (IAM 往返)");
    }
    if (PARAMS.some((p) => sp.has(p))) {
      for (const p of PARAMS) sp.delete(p);
      const qs = sp.toString();
      window.history.replaceState({}, "", window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash);
    }
  } catch (e) {
    console.warn("[alertentry] capture failed:", e); // 隐私模式 sessionStorage 可能不可用——放弃该特性不阻塞进站
  }
}

/** 只读窥视（守卫分流/展示提示用），不清除。 */
export function peekAlertContext(): AlertEntryContext | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as AlertEntryContext) : null;
  } catch {
    return null;
  }
}

/** 取出并清除（直达链触发点调用，一次性）。串损坏/取失败都归 null——与 autosend 同取舍：宁丢勿重发。 */
export function consumeAlertContext(): AlertEntryContext | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (raw) sessionStorage.removeItem(KEY);
    return raw ? (JSON.parse(raw) as AlertEntryContext) : null;
  } catch {
    return null;
  }
}
