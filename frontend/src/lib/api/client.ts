/** 真实后端 HTTP 客户端 —— 解析 envelope / error，注入 mock 登录头（B8 换真 IAM Cookie）。 */

// 单文根部署（内网 xxxx.com/openops）：打包时 VITE_OPENOPS_API_BASE=/openops/api，
// 由前端 nginx 剥掉 /openops 前缀后转本机后端。本地默认 "/api"（dev proxy / 同机直连不变）——
// ⚠该默认值在生产是唯一不可路由的值（网关只认 /openops），故 build-artifacts.sh 有正向断言。
// SSE 与 agui 两处绕过 request() 的调用点共用本值。
export const API_BASE = import.meta.env.VITE_OPENOPS_API_BASE ?? "/api";
const BASE = API_BASE;

/** demo 身份（角色/白名单事实在后端 PG；头只声明“我是谁”）。
 *
 * 生产真身份由后端 IAM 决定，前端不再有「切身份」按钮。dev/mock/e2e 用**身份种子**声明我是谁：
 * `?as=admin|user`（会镜像进 localStorage 以跨整页刷新持久）或直接 `localStorage['openops.demo.user']`。
 * 沿用既有 `openops.mock.*` 缝风格（index.ts）。默认普通用户，不带缝完全无感。 */
const DEMO_DEFAULT = { user: "0026demo01", name: "林一" };
const DEMO_ADMIN = { user: "admin", name: "李四（管理员）" };

function bootDemoIdentity(): { user: string; name: string } {
  try {
    if (typeof window !== "undefined" && typeof localStorage !== "undefined") {
      const as = new URLSearchParams(window.location.search).get("as");
      if (as) localStorage.setItem("openops.demo.user", as); // URL 一次性置入 → 持久到 localStorage
      if (localStorage.getItem("openops.demo.user") === "admin") return { ...DEMO_ADMIN };
    }
  } catch { /* 隐私模式 / 无 storage：回落默认 */ }
  return { ...DEMO_DEFAULT };
}

export const demoIdentity: { user: string; name: string } = bootDemoIdentity();

export class ApiError extends Error {
  code: string;
  retryable: boolean;
  constructor(code: string, message: string, retryable = false) {
    super(message);
    this.code = code;
    this.retryable = retryable;
  }
}

export async function apiFetch<T = unknown>(
  path: string,
  opts: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  // FormData（文件上传）：不设 Content-Type（浏览器自带 multipart boundary）、不 JSON.stringify；
  // 其余（鉴权头 / credentials / 信封 / 401 跳登录）与 JSON 路径完全一致。
  const isMultipart = typeof FormData !== "undefined" && opts.body instanceof FormData;
  const headers: Record<string, string> = {
    "X-OpenOps-Mock-User": demoIdentity.user,
    // HTTP 头仅限 ISO-8859-1：中文名须 URI 编码，后端 unquote
    "X-OpenOps-Mock-Name": encodeURIComponent(demoIdentity.name),
  };
  if (!isMultipart) headers["Content-Type"] = "application/json";
  const res = await fetch(`${BASE}${path}`, {
    method: opts.method ?? "GET",
    signal: opts.signal,
    // B9：带上公司 IAM cookie（后端 OPENOPS_IAM_ENABLED=true 时据此双步校验；mock 模式无害）
    credentials: "include",
    headers,
    body: opts.body === undefined ? undefined : isMultipart ? (opts.body as FormData) : JSON.stringify(opts.body),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = (json as { error?: { code?: string; message?: string; retryable?: boolean; login_url?: string } }).error;
    // B9：IAM 会话失效且后端带了登录地址 → 直接跳公司登录页（老项目 App.tsx 379-430 口径）
    if (res.status === 401 && err?.login_url) {
      // {host} 兜底替换：网关直连后端时 Host 头可能被改写成 ip:port，后端替出的域名不可用；
      // 前端永远知道正确域名（当前地址栏），残留占位符或 ip:port 域都以它为准
      window.location.assign(err.login_url.replaceAll("{host}", window.location.host));
      // 浏览器即将跳登录页：抛专用码，启动链据此保持加载态而非闪错误屏
      throw new ApiError("AUTH_REDIRECT", "正在跳转登录…", false);
    }
    throw new ApiError(err?.code ?? `HTTP_${res.status}`, err?.message ?? res.statusText, err?.retryable);
  }
  return (json as { data?: T }).data as T;
}

export const crid = (): string => "crid_" + Math.random().toString(36).slice(2, 10);
