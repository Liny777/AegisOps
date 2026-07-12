// 身份透传（Part B sidecar）：浏览器 → sidecar → FastAPI 的 header 桥。
//
// 本项目 V1 用 mock IAM（`X-OpenOps-Mock-User/Name`）；真 IAM（B9）接入后同一通道透传
// cookie / X-Forwarded-For。AsyncLocalStorage 让 CopilotKit runner 深处的 fetch 也能拿到
// 当前请求的身份头（参考 openOps-Dev strategy-a 的 identity.ts，裁掉 IAM hop 逻辑）。
import { AsyncLocalStorage } from "node:async_hooks";
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
