import assert from "node:assert/strict";
import test from "node:test";

import { replaceAllImpl, structuredCloneImpl, uuidV4From } from "./polyfills";

// 只测导出的纯函数：全局挂载在 Node 22（自带 at/toReversed/randomUUID）下 guard 天然不触发，
// 不构造对全局原型的断言（避免污染其他用例）。

test("uuidV4From：固定字节 → RFC4122 v4 格式（version/variant 位正确）", () => {
  const bytes = new Uint8Array(16); // 全零
  const uuid = uuidV4From(bytes);
  assert.match(uuid, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal(uuid, "00000000-0000-4000-8000-000000000000");
});

test("uuidV4From：全 0xff 字节仍钉住 version=4 / variant=10xx", () => {
  const bytes = new Uint8Array(16).fill(0xff);
  const uuid = uuidV4From(bytes);
  assert.equal(uuid[14], "4"); // version 半字节
  assert.ok(["8", "9", "a", "b"].includes(uuid[19]), uuid); // variant 半字节
});

test("replaceAllImpl：与原生 replaceAll 行为一致（正则元字符 search / 多次出现 / $& 模式 / 函数 replacer）", () => {
  const cases: [string, string, string][] = [
    ["a{host}b{host}c", "{host}", "X"],
    ["a.b.c", ".", "-"], // search 含正则元字符必须按字面量处理
    ["$1$1", "$1", "y"],
    ["无匹配", "zzz", "n"],
    ["aaa", "a", "$&$&"], // $ 特殊模式
  ];
  for (const [input, search, repl] of cases) {
    assert.equal(replaceAllImpl(input, search, repl), input.replaceAll(search, repl), `${input}|${search}`);
  }
  const fn = (m: string) => m.toUpperCase();
  assert.equal(replaceAllImpl("ab-ab", "ab", fn), "ab-ab".replaceAll("ab", fn));
  assert.equal(replaceAllImpl("a1b2", /\d/g, "#"), "a#b#"); // RegExp search 自带 /g
});

test("structuredCloneImpl：嵌套对象/数组/Map/Set/Date 深拷贝，循环引用不爆栈", () => {
  const date = new Date(1_700_000_000_000);
  const src: Record<string, unknown> = {
    arr: [1, { deep: "v" }],
    map: new Map([["k", { n: 1 }]]),
    set: new Set([1, 2]),
    date,
    re: /ab+c/gi,
    bytes: new Uint8Array([1, 2, 3]),
  };
  src.self = src; // 循环引用
  const out = structuredCloneImpl(src) as typeof src;
  assert.notEqual(out, src);
  assert.deepEqual(out.arr, src.arr);
  assert.notEqual((out.arr as unknown[])[1], (src.arr as unknown[])[1]); // 深拷贝而非引用
  assert.equal((out.map as Map<string, { n: number }>).get("k")!.n, 1);
  assert.notEqual((out.map as Map<string, unknown>).get("k"), (src.map as Map<string, unknown>).get("k"));
  assert.deepEqual([...(out.set as Set<number>)], [1, 2]);
  assert.equal((out.date as Date).getTime(), date.getTime());
  assert.notEqual(out.date, date);
  assert.equal((out.re as RegExp).source, "ab+c");
  assert.equal((out.re as RegExp).flags, "gi");
  assert.deepEqual([...(out.bytes as Uint8Array)], [1, 2, 3]);
  assert.equal(out.self, out); // 循环引用指向新克隆自身
});
