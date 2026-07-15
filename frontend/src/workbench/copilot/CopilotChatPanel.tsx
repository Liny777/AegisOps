// CopilotKit 官方 UI 接管对话区（Part B，用户拍板：整体换 <CopilotChat>）。
//
// 链路：<CopilotKit runtimeUrl=/api/copilotkit>（vite proxy → sidecar :4002，
// `npm run runtime` 启动）→ CopilotRuntime → HttpAgent → FastAPI per-run /agui。
// threadId=runId（sidecar 按它重写目标 URL）；身份经 headers 透传（mock IAM 两头）。
// 发送按钮两态/停止（原生）：运行中变停止 → abort 流 → 后端断流取消桥停任务。
// RCA/HITL 卡与活动栏不在本组件渲染；本组件把同流 CUSTOM 事件桥接给 Workbench，
// 再与备用 SSE、/state 和审计分页统一去重投影（30.4 三层模型不变）。
import { useCallback, useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { CopilotChat, CopilotKit, useAgent, useCopilotKit } from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";
import "./CopilotChatPanel.css";

import { demoIdentity } from "../../lib/api";
import type { OpenOpsEvent } from "../../lib/api/types";
import { CopilotAutoSend } from "./CopilotAutoSend";
import { CopilotSkillSlash } from "./CopilotSkillSlash";
import { ControlledVisualizationTools } from "./rich-ui";
import { MermaidFullscreenBoundary } from "./MermaidFullscreenBoundary";
import { OpenOpsChatMessageView } from "./OpenOpsChatMessageView";
import {
  bindCopilotThread,
  isCopilotThreadReady,
  shouldMountCopilotAutoSend,
} from "./threadBinding";

const AGENT_ID = "sre-agent";

function identityHeaders(): Record<string, string> {
  return {
    "X-OpenOps-Mock-User": demoIdentity.user,
    "X-OpenOps-Mock-Name": encodeURIComponent(demoIdentity.name),
  };
}

export function CopilotChatPanel({
  runId,
  instanceId,
  readOnly = false,
  blocked = false,
  blockedMessage,
  autoQuestion,
  onAutoSent,
  onOpenOps,
  onRetryConnection,
}: {
  runId: string;
  instanceId: string;
  /** closed run 仍使用同一 CopilotChat，只把官方 input slot 替换为只读提示。 */
  readOnly?: boolean;
  /** URL 已切换但新 Run 尚未完成状态恢复时，旧 composer 必须真正不可输入。 */
  blocked?: boolean;
  /** 切换失败时解释为何旧会话仍不可输入。 */
  blockedMessage?: string;
  /** 外链 ?q= 带入的待发问题（仅外链落地首个面板非空）；发送/放弃后经 onAutoSent 清除。 */
  autoQuestion?: string | null;
  onAutoSent?: () => void;
  /** 官方 CopilotChat 所消费的同一条 AG-UI 流中的 openops.* CUSTOM 事件。 */
  onOpenOps?: (event: OpenOpsEvent) => void;
  /** connect 恢复失败时重挂当前 run 的 Provider。 */
  onRetryConnection?: () => void;
}) {
  const [connectionStatus, setConnectionStatus] = useState<"connecting" | "ready" | "error">(
    "connecting",
  );
  const handleConnected = useCallback(
    () => setConnectionStatus((current) => (current === "error" ? current : "ready")),
    [],
  );

  return (
    <CopilotThreadGate
      runId={runId}
      blocked={blocked}
      blockedMessage={blockedMessage}
      connectionStatus={connectionStatus}
      onRetryConnection={onRetryConnection}
    >
      {onOpenOps ? <OpenOpsCustomEventBridge onOpenOps={onOpenOps} /> : null}
      {/* 先于 CopilotChat 订阅 connect 首帧/终态，恢复开始前 composer 始终不可用。 */}
      <CopilotConnectMonitor onConnected={handleConnected} />
      <MermaidFullscreenBoundary>
        {/* 模型只在初始化向导配置，会话内不再提供切换（去掉原右上角浮层选择器） */}
        <CopilotChat
          key={`${runId}:${readOnly ? "readonly" : "active"}`}
          agentId={AGENT_ID}
          threadId={runId}
          autoScroll="pin-to-bottom"
          className="copilot-chat-panel"
          messageView={OpenOpsChatMessageView}
          welcomeScreen={false}
          input={readOnly ? CLOSED_INPUT_SLOT : OPENOPS_INPUT_SLOT}
          onError={() =>
            setConnectionStatus((current) => (current === "ready" ? current : "error"))
          }
        />
        {!readOnly ? <CopilotSkillSlash instanceId={instanceId} /> : null}
        {!readOnly && autoQuestion && onAutoSent && shouldMountCopilotAutoSend(
          blocked || connectionStatus !== "ready",
          autoQuestion,
          true,
        ) ? (
          <CopilotAutoSend question={autoQuestion} threadId={runId} agentId={AGENT_ID} onSent={onAutoSent} />
        ) : null}
      </MermaidFullscreenBoundary>
    </CopilotThreadGate>
  );
}

function CopilotConnectMonitor({ onConnected }: { onConnected: () => void }) {
  const { agent } = useAgent({ agentId: AGENT_ID });
  useEffect(() => {
    let runningFrame: number | null = null;
    let finalizedFirstFrame: number | null = null;
    let finalizedSecondFrame: number | null = null;
    const scheduleFrame = (callback: () => void) => window.requestAnimationFrame(callback);
    const sub = agent.subscribe({
      onRunStartedEvent() {
        // 活跃任务的 connect 会持续到任务终态；下一帧仍在运行才放行，以便用户回到
        // 审批中的会话。空会话会在同一事件批次收到 RUN_FINISHED，此时不放行。
        runningFrame = scheduleFrame(() => {
          runningFrame = null;
          if (agent.isRunning) onConnected();
        });
      },
      onRunFinalized() {
        // 空 transcript 的 RUN_STARTED → SNAPSHOT → RUN_FINISHED 是同步连续到达的。
        // 再等两个渲染帧，确保 CopilotChat 自身的 connect finally 已写入最新 Agent，
        // 避免刚输入就被 StrictMode/connect 收尾重挂清空。
        finalizedFirstFrame = scheduleFrame(() => {
          finalizedFirstFrame = null;
          finalizedSecondFrame = scheduleFrame(() => {
            finalizedSecondFrame = null;
            onConnected();
          });
        });
      },
    });
    return () => {
      sub.unsubscribe();
      if (runningFrame !== null) window.cancelAnimationFrame(runningFrame);
      if (finalizedFirstFrame !== null) window.cancelAnimationFrame(finalizedFirstFrame);
      if (finalizedSecondFrame !== null) window.cancelAnimationFrame(finalizedSecondFrame);
    };
  }, [agent, onConnected]);

  return null;
}

function ClosedConversationInput() {
  return (
    <div
      data-testid="closed-conversation-readonly"
      style={{
        flex: "0 0 auto",
        padding: "14px 24px 18px",
        textAlign: "center",
        color: "#788192",
        fontSize: 12.5,
        background: "#f7f8fa",
      }}
    >
      会话已关闭：只读查看历史与审计，不能再启动新任务。
    </div>
  );
}

const CLOSED_INPUT_SLOT = {
  children: () => <ClosedConversationInput />,
};

// 官方 input slot 注入稳定类名；不绑定 CopilotKit 内部 utility class。
const OPENOPS_INPUT_SLOT = {
  className: "oa-chat-input",
  textArea: { className: "oa-chat-textarea" },
};

/**
 * 每个 Provider 只服务一个 run，但 CopilotChat 仍要到 passive effect 才写
 * `agent.threadId`。这里在 layout phase 先同步绑定，并在该次 commit 与首次 connect
 * 都确认前把 composer 设为 inert。
 */
function CopilotThreadGate({
  runId,
  blocked,
  blockedMessage,
  connectionStatus,
  onRetryConnection,
  children,
}: {
  runId: string;
  blocked: boolean;
  blockedMessage?: string;
  connectionStatus: "connecting" | "ready" | "error";
  onRetryConnection?: () => void;
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
    connectionStatus === "ready" &&
    isCopilotThreadReady(runId, committedBinding.runId, agent.threadId, blocked);
  const connectFailed = connectionStatus === "error" && !blockedMessage;
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
          role={connectFailed ? "alert" : "status"}
          aria-live="polite"
          data-testid={connectFailed ? "agent-connect-failed" : blockedMessage ? "copilot-thread-blocked" : undefined}
          style={{ position: "absolute", inset: 0, zIndex: 30, display: "grid", placeItems: "center", background: "rgba(247,248,250,.72)", color: "#788192", fontSize: 12.5 }}
        >
          {connectFailed ? (
            <div style={{ textAlign: "center" }}>
              <div>会话历史恢复失败，请重试。</div>
              {onRetryConnection ? (
                <button
                  type="button"
                  onClick={onRetryConnection}
                  style={{ marginTop: 10, border: "1px solid #ccd3df", borderRadius: 7, background: "#fff", padding: "6px 12px", cursor: "pointer" }}
                >
                  重试恢复
                </button>
              ) : null}
            </div>
          ) : blockedMessage ?? (connectionStatus === "connecting" ? "正在恢复会话…" : "正在绑定会话…")}
        </div>
      ) : null}
    </div>
  );
}

/**
 * One provider per resolved run. Workbench keys this component by runId (plus
 * an explicit retry generation), so messages/state can never leak across
 * conversation lifecycles.
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
