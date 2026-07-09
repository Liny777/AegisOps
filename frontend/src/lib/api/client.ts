/** 真实后端 HTTP 客户端 —— 解析 envelope / error，注入 mock 登录头（B8 换真 IAM Cookie）。 */

const BASE = import.meta.env.VITE_OPENOPS_API_BASE ?? "/api";

/** demo 身份（角色/白名单事实在后端 PG；头只声明“我是谁”）。侧栏切换驱动。 */
export const demoIdentity: { user: string; name: string } = {
  user: "0026demo01",
  name: "林一",
};

export function setDemoUser(user: string, name: string): void {
  demoIdentity.user = user;
  demoIdentity.name = name;
}

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
  opts: { method?: string; body?: unknown } = {},
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: opts.method ?? "GET",
    headers: {
      "Content-Type": "application/json",
      "X-OpenOps-Mock-User": demoIdentity.user,
      // HTTP 头仅限 ISO-8859-1：中文名须 URI 编码，后端 unquote
      "X-OpenOps-Mock-Name": encodeURIComponent(demoIdentity.name),
    },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = (json as { error?: { code?: string; message?: string; retryable?: boolean } }).error;
    throw new ApiError(err?.code ?? `HTTP_${res.status}`, err?.message ?? res.statusText, err?.retryable);
  }
  return (json as { data?: T }).data as T;
}

export const crid = (): string => "crid_" + Math.random().toString(36).slice(2, 10);
