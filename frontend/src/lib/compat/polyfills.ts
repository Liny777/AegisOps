// 老 WebView 兜底（WeLink 内置浏览器内核版本不明，2026-08-20 白屏排查产物）。
//
// vite build.target 只让 esbuild 转译**语法**（??= 等），不 polyfill 实例方法级 API——
// 依赖产物里的新 API 会在老内核直接 TypeError 整树卸载。只补 dist 逐 chunk grep 确认在用的六项：
// - @copilotkit/react-core：[...].toReversed()（Chrome 110+，对话消息路径）、crypto.randomUUID()（92+）
// - Array/String.prototype.at（92+）——自有 .at(-1) 已改写，此处仅作依赖保险
// - mermaid 系懒加载 chunk（c4/diagram/sankey/block/code-block/dagre/cytoscape/pie）：
//   String.prototype.replaceAll（85+）、Object.hasOwn（93+）、无守卫 structuredClone（98+）
// @ag-ui/client 的 structuredClone 自带 typeof 守卫降级（mermaid 系没有）。不引 core-js。
// 本模块必须是 main.tsx 第一行 import（先于所有依赖模块求值）。

/* eslint-disable @typescript-eslint/no-explicit-any */

function atImpl(this: { length: number; [i: number]: unknown }, n: number) {
  n = Math.trunc(n) || 0;
  if (n < 0) n += this.length;
  return n < 0 || n >= this.length ? undefined : this[n];
}

for (const proto of [Array.prototype, String.prototype] as any[]) {
  if (!("at" in proto)) {
    Object.defineProperty(proto, "at", { value: atImpl, writable: true, configurable: true });
  }
}

if (!("toReversed" in Array.prototype)) {
  Object.defineProperty(Array.prototype, "toReversed", {
    value: function toReversed(this: unknown[]) {
      return Array.prototype.slice.call(this).reverse();
    },
    writable: true,
    configurable: true,
  });
}

/** v4 UUID 纯函数（导出便于 node 单测；bytes 会被就地改写 version/variant 位）。 */
export function uuidV4From(bytes: Uint8Array): string {
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const h = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

if (typeof crypto !== "undefined" && !("randomUUID" in crypto)) {
  // DOM lib 里 randomUUID 返回模板字面量类型，宽接口断言绕开赋值不兼容
  (crypto as unknown as { randomUUID: () => string }).randomUUID = () =>
    uuidV4From(crypto.getRandomValues(new Uint8Array(16)));
}

/** replaceAll 纯实现（导出便于单测）：字符串 search 转义成 /g 正则走 replace，
 *  语义与原生一致（含函数 replacer 与 $ 特殊模式）；RegExp search 由调用方自带 /g（mermaid 产物如此）。 */
export function replaceAllImpl(input: string, search: string | RegExp, replacement: unknown): string {
  if (search instanceof RegExp) {
    return input.replace(search, replacement as string);
  }
  const escaped = String(search).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return input.replace(new RegExp(escaped, "g"), replacement as string);
}

if (!("replaceAll" in String.prototype)) {
  Object.defineProperty(String.prototype, "replaceAll", {
    value: function replaceAll(this: string, search: string | RegExp, replacement: unknown) {
      return replaceAllImpl(String(this), search, replacement);
    },
    writable: true,
    configurable: true,
  });
}

if (typeof (Object as { hasOwn?: unknown }).hasOwn !== "function") {
  Object.defineProperty(Object, "hasOwn", {
    value: (o: object, k: PropertyKey) => Object.prototype.hasOwnProperty.call(o, k),
    writable: true,
    configurable: true,
  });
}

/** structuredClone 纯实现（导出便于单测）：覆盖 mermaid/dagre/cytoscape 实际克隆的数据形态
 *  （纯对象/数组/Map/Set/Date/RegExp/TypedArray + 循环引用）；函数/DOM 节点等不可克隆项原样引用
 *  （原生会 throw，这里宽松处理——兜底场景宁可图渲染出来）。 */
export function structuredCloneImpl<T>(value: T, seen = new WeakMap<object, unknown>()): T {
  if (value === null || typeof value !== "object") return value;
  const obj = value as unknown as object;
  if (seen.has(obj)) return seen.get(obj) as T;
  if (value instanceof Date) return new Date(value.getTime()) as unknown as T;
  if (value instanceof RegExp) return new RegExp(value.source, value.flags) as unknown as T;
  if (ArrayBuffer.isView(value)) {
    // TypedArray / DataView：拷底层 buffer
    const view = value as unknown as { buffer: ArrayBuffer; byteOffset: number; byteLength: number };
    const buf = view.buffer.slice(view.byteOffset, view.byteOffset + view.byteLength);
    return new (value.constructor as new (b: ArrayBuffer) => T)(buf);
  }
  if (value instanceof ArrayBuffer) return value.slice(0) as unknown as T;
  if (value instanceof Map) {
    const out = new Map();
    seen.set(obj, out);
    for (const [k, v] of value) out.set(structuredCloneImpl(k, seen), structuredCloneImpl(v, seen));
    return out as unknown as T;
  }
  if (value instanceof Set) {
    const out = new Set();
    seen.set(obj, out);
    for (const v of value) out.add(structuredCloneImpl(v, seen));
    return out as unknown as T;
  }
  if (Array.isArray(value)) {
    const out: unknown[] = [];
    seen.set(obj, out);
    for (let i = 0; i < value.length; i++) out[i] = structuredCloneImpl(value[i], seen);
    return out as unknown as T;
  }
  if (typeof (value as { then?: unknown }).then === "function") return value; // Promise 等原样
  const out: Record<string, unknown> = {};
  seen.set(obj, out);
  for (const k of Object.keys(value)) out[k] = structuredCloneImpl((value as Record<string, unknown>)[k], seen);
  return out as unknown as T;
}

if (typeof globalThis !== "undefined" && typeof globalThis.structuredClone !== "function") {
  (globalThis as { structuredClone: <T>(v: T) => T }).structuredClone = <T,>(v: T) => structuredCloneImpl(v);
}
