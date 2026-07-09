/** 真实后端 HTTP 客户端 —— 解析后端 envelope / error，注入 mock 鉴权头。 */
import type { Role } from "./types";

const BASE = import.meta.env.VITE_OPENOPS_API_BASE ?? "/api";

/** demo 身份（对应后端 X-OpenOps-Mock-* 头），由侧栏 user/admin 切换驱动。 */
export const demoIdentity: { role: Role; whitelisted: boolean; user: string } = {
  role: "user",
  whitelisted: true,
  user: "0026demo01",
};

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
      "X-OpenOps-Mock-Role": demoIdentity.role,
      "X-OpenOps-Mock-Whitelist": demoIdentity.whitelisted ? "true" : "false",
      "X-OpenOps-Mock-User": demoIdentity.user,
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
