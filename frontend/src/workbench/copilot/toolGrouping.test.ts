import assert from "node:assert/strict";
import test from "node:test";

import type { Message } from "@ag-ui/client";
import { groupToolCallsByUserTurn } from "./toolGrouping";

function toolCall(id: string, name = id) {
  return { id, type: "function" as const, function: { name, arguments: `{\"id\":\"${id}\"}` } };
}

test("merges all assistant tool calls in one user turn and keeps results addressable", () => {
  const messages: Message[] = [
    { id: "u1", role: "user", content: "inspect" },
    { id: "a-tool-1", role: "assistant", toolCalls: [toolCall("call-1")] },
    { id: "r1", role: "tool", toolCallId: "call-1", content: "result-1" },
    { id: "a-text", role: "assistant", content: "still checking" },
    { id: "a-tool-2", role: "assistant", toolCalls: [toolCall("call-2"), toolCall("call-3")] },
    { id: "r2", role: "tool", toolCallId: "call-2", content: "result-2" },
  ];

  const grouped = groupToolCallsByUserTurn(messages);
  assert.deepEqual(grouped.map((message) => message.id), ["u1", "a-tool-1", "r1", "a-text", "r2"]);
  assert.deepEqual(
    (grouped[1].role === "assistant" ? grouped[1].toolCalls : [])?.map((call) => call.id) ?? [],
    ["call-1", "call-2", "call-3"],
  );
  assert.equal(grouped.find((message) => message.id === "r2")?.role, "tool");
  assert.deepEqual(messages[1].role === "assistant" ? messages[1].toolCalls?.map((call) => call.id) : [], ["call-1"]);
});

test("starts a new aggregate at every user turn and preserves text-bearing tool messages", () => {
  const messages: Message[] = [
    { id: "u1", role: "user", content: "first" },
    { id: "a1", role: "assistant", toolCalls: [toolCall("call-1")] },
    { id: "a2", role: "assistant", content: "tool and prose", toolCalls: [toolCall("call-2")] },
    { id: "u2", role: "user", content: "second" },
    { id: "a3", role: "assistant", toolCalls: [toolCall("call-3")] },
  ];

  const grouped = groupToolCallsByUserTurn(messages);
  const assistants = grouped.filter((message) => message.role === "assistant");
  assert.deepEqual(assistants.map((message) => message.id), ["a1", "a2", "a3"]);
  assert.deepEqual(assistants.map((message) => message.toolCalls?.map((call) => call.id) ?? []), [
    ["call-1", "call-2"], [], ["call-3"],
  ]);
  assert.equal(assistants[1].content, "tool and prose");
});

test("does not duplicate a repeated tool call snapshot", () => {
  const repeated = toolCall("same-call");
  const grouped = groupToolCallsByUserTurn([
    { id: "u", role: "user", content: "question" },
    { id: "a1", role: "assistant", toolCalls: [repeated] },
    { id: "a2", role: "assistant", toolCalls: [repeated] },
  ]);
  assert.equal(grouped.length, 2);
  assert.equal(grouped[1].role === "assistant" ? grouped[1].toolCalls?.length : 0, 1);
});
