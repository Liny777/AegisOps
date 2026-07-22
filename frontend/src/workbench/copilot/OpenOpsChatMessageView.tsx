import {  type JSX, useEffect, useMemo, useRef, useState } from "react";
import {
  CopilotChatMessageView,
  CopilotChatToolCallsView,
  type CopilotChatMessageViewProps,
  type CopilotChatToolCallsViewProps,
} from "@copilotkit/react-core/v2";
import { Streamdown } from "streamdown";
import { CopilotPresetQuestions } from "./CopilotPresetQuestions";
import { groupToolCallsByUserTurn } from "./toolGrouping";

const TABLE_STYLE: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: "12px",
  border: "1px solid #dfe5ee",
};
 
const TH_TD_STYLE: React.CSSProperties = {
  padding: "8px 12px",
  border: "1px solid #e6e8ec",
};
 
const TH_STYLE: React.CSSProperties = {
  ...TH_TD_STYLE,
  fontWeight: 600,
  background: "#f8fafc",
};
 
const CENTERED_TABLE_COMPONENTS = {
  table: ({ children, ...props }: JSX.IntrinsicElements["table"] & { node?: unknown }) => (
    <table {...props} style={TABLE_STYLE}>{children}</table>
  ),
  th: ({ children, ...props }: JSX.IntrinsicElements["th"] & { node?: unknown }) => (
    <th {...props} style={TH_STYLE}>{children}</th>
  ),
  td: ({ children, ...props }: JSX.IntrinsicElements["td"] & { node?: unknown }) => (
    <td {...props} style={TH_TD_STYLE}>{children}</td>
  ),
};
 
function OaMarkdownRenderer({ content, className, ...props }: { content?: string; className?: string } & Record<string, unknown>) {
  return (
    <Streamdown className={`oa-chat-markdown${className ? ` ${className}` : ""}`} components={CENTERED_TABLE_COMPONENTS} {...props}>
      {content ?? ""}
    </Streamdown>
  );
}

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
  markdownRenderer: OaMarkdownRenderer,
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

// 官方 CopilotChatMessageView 在消息 > 50 条时启用 @tanstack/react-virtual 虚拟化。该虚拟化在长
// 会话里不稳：滚动容器（use-stick-to-bottom 强制 overflow:auto 的那层）clientHeight 在挂载/重测量
// 时会瞬时为 0（控制台可见 "clientHeight=0 — virtualization disabled"），叠加 estimateSize=100 与真实
// 富文本行高的错配，getTotalSize()/scrollHeight 抖动、与粘底逻辑抢 scrollTop，导致原生滚动条滑块只能
// 停顶/底、拖不到中间（内网教训：长对话滚动回弹）。
// 修法：给官方组件传 children（render prop）。其内部判定 `shouldVirtualize = !!scrollElement &&
// !children && messages.length > 50`，只要传了 children 就恒不虚拟化，而消息仍由官方 renderMessageBlock
// 全量渲染（我们的 slot / 工具聚合 / markdown 全部照旧）。SRE 对话量级下全量渲染成本可接受。
// —— 该 children 结构复刻官方非虚拟化分支（container + interruptElement + 运行中游标）；CopilotKit 固定
// 1.61.2，随升级需回看该分支。
function OpenOpsChatMessageViewImpl({ messages = [], className, ...props }: CopilotChatMessageViewProps) {
  const groupedMessages = useMemo(() => groupToolCallsByUserTurn(messages), [messages]);
  const listClassName = ["copilotKitMessages", "cpk:flex", "cpk:flex-col", "oa-chat-message-list", className]
    .filter(Boolean)
    .join(" ");
  return (
    <CopilotChatMessageView
      {...props}
      messages={groupedMessages}
      assistantMessage={ASSISTANT_MESSAGE_SLOT}
      userMessage={USER_MESSAGE_SLOT}
    >
      {({ messageElements, messages: renderedMessages, isRunning, interruptElement }) => {
        const lastMessage = renderedMessages[renderedMessages.length - 1];
        const showCursor = isRunning && lastMessage?.role !== "reasoning";
        return (
          <div data-testid="copilot-message-list" className={listClassName}>
            {messageElements}
            {/* 空会话冷启动引导；首条消息落地后此分支自然不再命中。 */}
            {renderedMessages.length === 0 ? <CopilotPresetQuestions /> : null}
            {interruptElement}
            {showCursor ? (
              <div className="cpk:mt-2">
                <CopilotChatMessageView.Cursor />
              </div>
            ) : null}
          </div>
        );
      }}
    </CopilotChatMessageView>
  );
}

// Slot 类型包含官方静态 Cursor；原样透传，避免替换消息视图时丢掉运行中游标能力。
export const OpenOpsChatMessageView = Object.assign(OpenOpsChatMessageViewImpl, {
  Cursor: CopilotChatMessageView.Cursor,
});
