// CopilotKit 官方 UI 接管对话区（Part B，用户拍板：整体换 <CopilotChat>）。
//
// 链路：<CopilotKit runtimeUrl=/api/copilotkit>（vite proxy → sidecar :4002，
// `npm run runtime` 启动）→ CopilotRuntime → HttpAgent → FastAPI per-run /agui。
// threadId=runId（sidecar 按它重写目标 URL）；身份经 headers 透传（mock IAM 两头）。
// 发送按钮两态/停止（原生）：运行中变停止 → abort 流 → 后端断流取消桥停任务。
// RCA/HITL 卡与活动栏不在本组件渲染；本组件把同流 CUSTOM 事件桥接给 Workbench，
// 再与备用 SSE、/state 和审计分页统一去重投影（30.4 三层模型不变）。
import { useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { CopilotChat, CopilotKit, useAgent, useCopilotKit } from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";
import "./CopilotChatPanel.css";

import { api, demoIdentity } from "../../lib/api";
import type { OpenOpsEvent, TranscriptMessage } from "../../lib/api/types";
import { CopilotAutoSend } from "./CopilotAutoSend";
import { CopilotSkillSlash } from "./CopilotSkillSlash";
import { ControlledVisualizationTools } from "./rich-ui";
import {
  bindCopilotThread,
  isCopilotThreadReady,
  shouldMountCopilotAutoSend,
} from "./threadBinding";

const AGENT_ID = "sre-agent";

// CopilotKit v2 官方 slots：项目样式只依赖这些 OpenOps class，不绑定 cpk:* 内部实现。
// 对象放在组件外保持引用稳定，避免流式输出时 slot 组件被不必要地重新解析。
const OPENOPS_MESSAGE_VIEW = {
  className: "oa-chat-message-list",
  assistantMessage: {
    className: "oa-chat-message oa-chat-assistant-message",
    markdownRenderer: { className: "oa-chat-markdown" },
    toolbar: { className: "oa-chat-toolbar oa-chat-assistant-toolbar" },
    copyButton: { className: "oa-chat-copy-button" },
  },
  userMessage: {
    className: "oa-chat-message oa-chat-user-message",
    messageRenderer: { className: "oa-chat-user-content" },
    toolbar: { className: "oa-chat-toolbar oa-chat-user-toolbar" },
    copyButton: { className: "oa-chat-copy-button" },
  },
};

function identityHeaders(): Record<string, string> {
  return {
    "X-OpenOps-Mock-User": demoIdentity.user,
    "X-OpenOps-Mock-Name": encodeURIComponent(demoIdentity.name),
  };
}

export function CopilotChatPanel({
  runId,
  instanceId,
  blocked = false,
  blockedMessage,
  autoQuestion,
  onAutoSent,
  onOpenOps,
}: {
  runId: string;
  instanceId: string;
  /** URL 已切换但新 Run 尚未完成状态恢复时，旧 composer 必须真正不可输入。 */
  blocked?: boolean;
  /** 切换失败时解释为何旧会话仍不可输入。 */
  blockedMessage?: string;
  /** 外链 ?q= 带入的待发问题（仅外链落地首个面板非空）；发送/放弃后经 onAutoSent 清除。 */
  autoQuestion?: string | null;
  onAutoSent?: () => void;
  /** 官方 CopilotChat 所消费的同一条 AG-UI 流中的 openops.* CUSTOM 事件。 */
  onOpenOps?: (event: OpenOpsEvent) => void;
}) {
  return (
    <CopilotThreadGate runId={runId} blocked={blocked} blockedMessage={blockedMessage}>
      {onOpenOps ? <OpenOpsCustomEventBridge onOpenOps={onOpenOps} /> : null}
      <div style={{ position: "relative", flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
        {/* 历史对话：重进会话时 CopilotKit v2 的 connect 走 sidecar 内存回放、拿不到历史，故这里直接
            REST 拉后端 transcript 自渲染在 live 对话上方（有历史才显示）。 */}
        <HistoryDock runId={runId} />
        {/* 模型只在初始化向导配置，会话内不再提供切换（去掉原右上角浮层选择器） */}
        <CopilotChat
          agentId={AGENT_ID}
          threadId={runId}
          autoScroll="pin-to-bottom"
          className="copilot-chat-panel"
          messageView={OPENOPS_MESSAGE_VIEW}
          welcomeScreen={false}
        />
        <CopilotSkillSlash instanceId={instanceId} />
        {autoQuestion && onAutoSent && shouldMountCopilotAutoSend(blocked, autoQuestion, true) ? (
          <CopilotAutoSend question={autoQuestion} threadId={runId} agentId={AGENT_ID} onSent={onAutoSent} />
        ) : null}
      </div>
    </CopilotThreadGate>
  );
}

/**
 * 历史对话 dock（B1）：进入/重开一个 run 时 REST 拉后端 transcript 自渲染，绕开 CopilotKit connect
 * （v2 的 connect 由 sidecar InMemoryAgentRunner 从进程内存回放，内存 miss 即空流、拿不到历史）。
 * 只读回放，不进 agent 消息管线（故不会被 CopilotChat 挂载时的 setMessages([]) 清空）；有历史才显示。
 * 新一轮对话仍由下方 live CopilotChat 承载，下次重开时该轮已并入 state_json → 出现在本 dock。
 */
function HistoryDock({ runId }: { runId: string }) {
  const [history, setHistory] = useState<TranscriptMessage[]>([]);

  useEffect(() => {
    const ac = new AbortController();
    setHistory([]);
    api.getMessages(runId, { signal: ac.signal })
      .then((msgs) => setHistory(Array.isArray(msgs) ? msgs : []))
      .catch(() => undefined); // 拉不到历史不阻断聊天
    return () => ac.abort();
  }, [runId]);

  if (!history.length) return null;
  return (
    <div
      data-testid="chat-history-dock"
      style={{ flex: "0 0 auto", maxHeight: "46%", overflowY: "auto", borderBottom: "1px solid #eef0f3", background: "#fbfcfd" }}
    >
      <div style={{ width: "min(100%, 760px)", margin: "0 auto", padding: "14px 24px 16px", display: "flex", flexDirection: "column", gap: 14 }}>
        <div style={{ fontSize: 11.5, color: "#9aa3b0", fontWeight: 600 }}>历史对话</div>
        {history.map((m, i) => (
          <HistoryBubble key={`h-${runId}-${i}`} role={m.role} content={m.content} />
        ))}
      </div>
    </div>
  );
}

function HistoryBubble({ role, content }: { role: "user" | "assistant"; content: string }) {
  const isUser = role === "user";
  return (
    <div style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start" }}>
      <div
        style={{
          maxWidth: "82%", whiteSpace: "pre-wrap", overflowWrap: "anywhere",
          fontSize: 14, lineHeight: 1.52, color: "#1f2430",
          padding: isUser ? "8px 12px" : "2px 0",
          borderRadius: isUser ? 12 : 0,
          background: isUser ? "#eef2ff" : "transparent",
        }}
      >
        {content}
      </div>
    </div>
  );
}

/**
 * Provider 常驻时所有历史会话共享同一个 AbstractAgent。CopilotChat 自身在 passive effect
 * 才写 `agent.threadId`，所以 Run 刚切换的一帧内输入可能仍发往旧 Run。这里在 layout phase
 * 先同步绑定，并在该次 commit 确认前把整个 composer 设为 inert；不重挂 Provider。
 */
function CopilotThreadGate({
  runId,
  blocked,
  blockedMessage,
  children,
}: {
  runId: string;
  blocked: boolean;
  blockedMessage?: string;
  children: ReactNode;
}) {
  const { agent } = useAgent({ agentId: AGENT_ID });
  const { copilotkit } = useCopilotKit();
  const [committedBinding, setCommittedBinding] = useState<{
    agent: typeof agent;
    runId: string;
  } | null>(null);

  useLayoutEffect(() => {
    // runtime info 尚未返回时，各 useAgent hook 可能各自持有 provisional agent。
    // 只对 core 已注册的真实共享实例放行，确保这里绑定的正是 CopilotChat 发送时使用的实例。
    if (copilotkit.getAgent(AGENT_ID) !== agent) {
      setCommittedBinding(null);
      return;
    }
    bindCopilotThread(agent, runId);
    // agent 也属于 commit 身份：runtime info 到达会把 provisional agent 换成真实 agent，
    // 即使 runId 没变也必须触发一次新的就绪确认。
    setCommittedBinding({ agent, runId });
  }, [agent, copilotkit, runId]);

  const registeredAgent = copilotkit.getAgent(AGENT_ID) === agent;
  const ready = registeredAgent &&
    committedBinding?.agent === agent &&
    isCopilotThreadReady(runId, committedBinding.runId, agent.threadId, blocked);
  return (
    <div
      data-testid="copilot-thread-gate"
      data-thread-ready={ready ? "true" : "false"}
      style={{ position: "relative", flex: 1, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column" }}
    >
      <div
        inert={!ready}
        aria-hidden={!ready}
        style={{ flex: 1, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column", pointerEvents: ready ? "auto" : "none" }}
      >
        {/* Runtime info 到达前不挂 CopilotChat，避免 provisional Agent 先开一条必然被中止的 connect。 */}
        {registeredAgent ? children : null}
      </div>
      {!ready ? (
        <div
          role="status"
          aria-live="polite"
          data-testid={blockedMessage ? "copilot-thread-blocked" : undefined}
          style={{ position: "absolute", inset: 0, zIndex: 30, display: "grid", placeItems: "center", background: "rgba(247,248,250,.72)", color: "#788192", fontSize: 12.5 }}
        >
          {blockedMessage ?? "正在绑定会话…"}
        </div>
      ) : null}
    </div>
  );
}

/**
 * One provider for the entire AppShell lifetime. `threadId` belongs to
 * CopilotChat, so changing history entries no longer repeats runtime-info or
 * recreates the provider.
 */
export function CopilotWorkbenchProvider({ children }: { children: ReactNode }) {
  return (
    <CopilotKit
      runtimeUrl={`${import.meta.env.BASE_URL}api/copilotkit`}
      agent={AGENT_ID}
      headers={identityHeaders}
      useSingleEndpoint
      enableInspector={false}
      onError={(event) => console.error("[CopilotKit]", event)}
    >
      <ControlledVisualizationTools agentId={AGENT_ID} />
      {children}
    </CopilotKit>
  );
}

/**
 * 无 UI 的 AG-UI CUSTOM 事件桥。
 *
 * 必须位于 CopilotKit Provider 内部，订阅的就是 CopilotChat 正在运行的 AbstractAgent；因此
 * 不会为主动任务再开第二条 AG-UI 请求。备用 /events/stream 仍由 Workbench 负责被动更新，
 * 两路最终按后端统一的 event_id 在活动投影器中去重。
 */
function OpenOpsCustomEventBridge({ onOpenOps }: { onOpenOps: (event: OpenOpsEvent) => void }) {
  const { agent } = useAgent({ agentId: AGENT_ID });

  useEffect(() => {
    const subscription = agent.subscribe({
      onCustomEvent({ event }) {
        if (typeof event.name !== "string" || !event.name.startsWith("openops.")) return;
        const value = event.value;
        if (!value || typeof value !== "object") return;
        onOpenOps(value as OpenOpsEvent);
      },
    });
    return () => subscription.unsubscribe();
  }, [agent, onOpenOps]);

  return null;
}
