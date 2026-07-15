import { useEffect, useMemo, useRef, useState } from "react";
import {
  CopilotChatMessageView,
  CopilotChatToolCallsView,
  type CopilotChatMessageViewProps,
  type CopilotChatToolCallsViewProps,
} from "@copilotkit/react-core/v2";

import { groupToolCallsByUserTurn } from "./toolGrouping";

function GroupedToolCallsView({ message, messages = [] }: CopilotChatToolCallsViewProps) {
  const toolCalls = message.toolCalls ?? [];
  const completedIds = new Set(
    messages.filter((candidate) => candidate.role === "tool").map((candidate) => candidate.toolCallId),
  );
  const completed = toolCalls.filter((toolCall) => completedIds.has(toolCall.id)).length;
  const pending = completed < toolCalls.length;
  const [manualExpanded, setManualExpanded] = useState<boolean | null>(null);
  const previousPending = useRef(pending);

  useEffect(() => {
    if (previousPending.current && !pending) {
      setManualExpanded(false); // 本轮全部完成：无论运行中是否手动展开过，都自动收起一次。
    } else if (!previousPending.current && pending) {
      setManualExpanded(null); // 同轮又出现新调用：恢复“运行中默认展开”。
    }
    previousPending.current = pending;
  }, [pending]);

  const expanded = manualExpanded ?? pending;

  if (!toolCalls.length) return null;
  return (
    <section className="oa-tool-call-group" data-testid="tool-call-group">
      <button
        type="button"
        className="oa-tool-call-group-toggle"
        aria-expanded={expanded}
        onClick={() => setManualExpanded(!expanded)}
      >
        <span className={`oa-tool-call-status${pending ? " is-running" : ""}`} aria-hidden="true" />
        <span className="oa-tool-call-group-label">工具调用</span>
        <span className="oa-tool-call-group-count">
          {toolCalls.length} 个 · 已完成 {completed}/{toolCalls.length}
        </span>
        <span className="oa-tool-call-group-chevron" aria-hidden="true">{expanded ? "−" : "+"}</span>
      </button>
      <div className="oa-tool-call-group-body" hidden={!expanded}>
        <CopilotChatToolCallsView message={message} messages={messages} />
      </div>
    </section>
  );
}

const ASSISTANT_MESSAGE_SLOT = {
  className: "oa-chat-message oa-chat-assistant-message",
  markdownRenderer: { className: "oa-chat-markdown" },
  toolbar: { className: "oa-chat-toolbar oa-chat-assistant-toolbar" },
  copyButton: { className: "oa-chat-copy-button" },
  toolCallsView: GroupedToolCallsView,
};

const USER_MESSAGE_SLOT = {
  className: "oa-chat-message oa-chat-user-message",
  messageRenderer: { className: "oa-chat-user-content" },
  toolbar: { className: "oa-chat-toolbar oa-chat-user-toolbar" },
  copyButton: { className: "oa-chat-copy-button" },
};

/** 官方消息视图的薄包装：仅改消息投影和 tool slot，仍由官方组件负责虚拟化、游标和 rich UI。 */
function OpenOpsChatMessageViewImpl({ messages = [], className, ...props }: CopilotChatMessageViewProps) {
  const groupedMessages = useMemo(() => groupToolCallsByUserTurn(messages), [messages]);
  return (
    <CopilotChatMessageView
      {...props}
      messages={groupedMessages}
      className={["oa-chat-message-list", className].filter(Boolean).join(" ")}
      assistantMessage={ASSISTANT_MESSAGE_SLOT}
      userMessage={USER_MESSAGE_SLOT}
    />
  );
}

// Slot 类型包含官方静态 Cursor；原样透传，避免替换消息视图时丢掉运行中游标能力。
export const OpenOpsChatMessageView = Object.assign(OpenOpsChatMessageViewImpl, {
  Cursor: CopilotChatMessageView.Cursor,
});
