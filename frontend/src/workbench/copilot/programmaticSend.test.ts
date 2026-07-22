import assert from "node:assert/strict";
import test from "node:test";

import { newSendNonce, programmaticMessageId, programmaticSendKey } from "./programmaticSend";

test("无 nonce 缺省行为不变：幂等键=threadId，消息 id=autosend-{threadId}（外链单次语义）", () => {
  assert.equal(programmaticSendKey("run-1"), "run-1");
  assert.equal(programmaticMessageId("run-1"), "autosend-run-1");
});

test("带 nonce：同 nonce 幂等（StrictMode 双跑同键），不同 nonce 键与消息 id 都唯一", () => {
  assert.equal(programmaticSendKey("run-1", "n1"), programmaticSendKey("run-1", "n1"));
  assert.equal(programmaticMessageId("run-1", "n1"), programmaticMessageId("run-1", "n1"));
  assert.notEqual(programmaticSendKey("run-1", "n1"), programmaticSendKey("run-1", "n2"));
  assert.notEqual(programmaticMessageId("run-1", "n1"), programmaticMessageId("run-1", "n2"));
  // 带 nonce 的键不会与无 nonce 的外链键互撞（否则卡片点击会吞掉外链发送）。
  assert.notEqual(programmaticSendKey("run-1", "n1"), programmaticSendKey("run-1"));
});

test("跨线程不互撞：同 nonce 不同 threadId 的键与消息 id 均不同", () => {
  assert.notEqual(programmaticSendKey("run-1", "n1"), programmaticSendKey("run-2", "n1"));
  assert.notEqual(programmaticMessageId("run-1", "n1"), programmaticMessageId("run-2", "n1"));
});

test("newSendNonce 连续生成不重复", () => {
  const nonces = new Set(Array.from({ length: 200 }, () => newSendNonce()));
  assert.equal(nonces.size, 200);
});
