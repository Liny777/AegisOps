// 身份透传（Part B sidecar）：浏览器 → sidecar → FastAPI 的 header 桥。
//
// 本项目 V1 用 mock IAM（`X-OpenOps-Mock-User/Name`）；真 IAM（B9）接入后同一通道透传
// cookie / X-Forwarded-For。AsyncLocalStorage 让 CopilotKit runner 深处的 fetch 也能拿到
// 当前请求的身份头（参考 openOps-Dev strategy-a 的 identity.ts，裁掉 IAM hop 逻辑）。
import { AsyncLocalStorage } from "node:async_hooks";
import { createHash } from "node:crypto";
import type { IncomingHttpHeaders } from "node:http";

export type Identity = Record<string, string>;

const als = new AsyncLocalStorage<Identity>();

// 白名单转发：身份两头 + cookie（真 IAM 预留）+ 浏览器 IP（内网 MCP 双透传口径）
const FORWARD = ["x-openops-mock-user", "x-openops-mock-name", "cookie", "x-forwarded-for"] as const;

export function identityFromIncoming(headers: IncomingHttpHeaders): Identity {
  const out: Identity = {};
  for (const k of FORWARD) {
    const v = headers[k];
    if (typeof v === "string" && v) out[k] = v;
    else if (Array.isArray(v) && v.length) out[k] = v.join(", ");
  }
  return out;
}

export function runWithIdentity<T>(identity: Identity, fn: () => T): T {
  return als.run(identity, fn);
}

export function identityHeaders(): Identity {
  return als.getStore() ?? {};
}

/**
 * Return a stable, non-reversible owner scope for in-memory runtime state.
 *
 * A real IAM session cookie takes precedence whenever present. The mock user id
 * is only a fallback for local/mock deployments, where no authenticated cookie
 * exists. The raw cookie never leaves this function: only its digest is used as
 * the ownership key. Display names and forwarded addresses are deliberately not
 * ownership signals: names can change and client IPs can be shared or spoofed.
 */
export function identityOwnerScope(identity: Identity = identityHeaders()): string {
  const mockUser = identity["x-openops-mock-user"];
  const cookie = identity.cookie;
  const principal = cookie
    ? `cookie\0${cookie}`
    : mockUser
      ? `mock-user\0${mockUser}`
      : "anonymous";
  return createHash("sha256").update(principal).digest("base64url");
}
