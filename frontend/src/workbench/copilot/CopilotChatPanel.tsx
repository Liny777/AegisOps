// CopilotKit 官方 UI 接管对话区（Part B，用户拍板：整体换 <CopilotChat>）。
//
// 链路：<CopilotKit runtimeUrl=/api/copilotkit>（vite proxy → sidecar :4002，
// `npm run runtime` 启动）→ CopilotRuntime → HttpAgent → FastAPI per-run /agui。
// threadId=runId（sidecar 按它重写目标 URL）；身份经 headers 透传（mock IAM 两头）。
// 发送按钮两态/停止（原生）：运行中变停止 → abort 流 → 后端断流取消桥停任务。
// RCA/HITL 卡与活动栏不在本组件渲染；本组件把同流 CUSTOM 事件桥接给 Workbench，
// 再与备用 SSE、/state 和审计分页统一去重投影（30.4 三层模型不变）。
import { useEffect } from "react";
import { CopilotChat, CopilotKit, useAgent } from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";
import "./CopilotChatPanel.css";

import { demoIdentity } from "../../lib/api";
import type { OpenOpsEvent } from "../../lib/api/types";
import { CopilotAutoSend } from "./CopilotAutoSend";
import { CopilotSkillSlash } from "./CopilotSkillSlash";
import { ControlledVisualizationTools } from "./rich-ui";

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

export function CopilotChatPanel({ runId, instanceId, autoQuestion, onAutoSent, onOpenOps }: {
  runId: string;
  instanceId: string;
  /** 外链 ?q= 带入的待发问题（仅外链落地首个面板非空）；发送/放弃后经 onAutoSent 清除。 */
  autoQuestion?: string | null;
  onAutoSent?: () => void;
  /** 官方 CopilotChat 所消费的同一条 AG-UI 流中的 openops.* CUSTOM 事件。 */
  onOpenOps?: (event: OpenOpsEvent) => void;
}) {
  return (
    <CopilotKit
      key={runId}
      runtimeUrl={`${import.meta.env.BASE_URL}api/copilotkit`}
      agent={AGENT_ID}
      headers={identityHeaders}
      onError={(event) => console.error("[CopilotKit]", event)}
    >
      <ControlledVisualizationTools agentId={AGENT_ID} />
      {onOpenOps ? <OpenOpsCustomEventBridge onOpenOps={onOpenOps} /> : null}
      <div style={{ position: "relative", flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
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
        {autoQuestion && onAutoSent ? (
          <CopilotAutoSend question={autoQuestion} threadId={runId} agentId={AGENT_ID} onSent={onAutoSent} />
        ) : null}
      </div>
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
