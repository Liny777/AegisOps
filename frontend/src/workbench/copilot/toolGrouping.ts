import type { AssistantMessage, Message, ToolCall } from "@ag-ui/client";

function hasVisibleContent(message: AssistantMessage): boolean {
  return Boolean(message.content?.trim());
}

/**
 * 将同一用户轮次内散落的 assistant toolCalls 合并到第一个工具消息。
 *
 * tool 结果消息仍原位保留，供 CopilotChatToolCallsView 按 toolCallId 查找；后续有文本的
 * assistant 消息也保持原顺序，只清掉已迁移的 toolCalls。该函数不修改 Agent 原始消息数组。
 */
export function groupToolCallsByUserTurn(messages: Message[]): Message[] {
  const grouped: Message[] = [];
  let anchorIndex: number | null = null;

  for (const message of messages) {
    if (message.role === "user") {
      anchorIndex = null;
      grouped.push(message);
      continue;
    }

    if (message.role !== "assistant" || !message.toolCalls?.length) {
      grouped.push(message);
      continue;
    }

    if (anchorIndex === null) {
      anchorIndex = grouped.length;
      grouped.push({ ...message, toolCalls: [...message.toolCalls] });
      continue;
    }

    const anchor = grouped[anchorIndex] as AssistantMessage;
    const knownIds = new Set((anchor.toolCalls ?? []).map((toolCall) => toolCall.id));
    const additions = message.toolCalls.filter((toolCall) => !knownIds.has(toolCall.id));
    grouped[anchorIndex] = {
      ...anchor,
      toolCalls: [...(anchor.toolCalls ?? []), ...additions] as ToolCall[],
    };

    // 迁移后的纯工具锚点没有用户可见内容；有文本时仅移除 toolCalls，保持文本所在位置和消息 ID。
    if (hasVisibleContent(message)) {
      grouped.push({ ...message, toolCalls: undefined });
    }
  }

  return grouped;
}

