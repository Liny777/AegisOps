/**
 * AG-UI 运行态适配器（B5，30.4「CopilotKit 与 AG-UI 事件映射」）。
 *
 * 用 @ag-ui/client HttpAgent（CopilotKit v2 同款 AG-UI 客户端）打后端
 * POST /agent-runs/{id}/agui：标准事件（TEXT_MESSAGE_*）→ 对话主区流式文本；
 * CUSTOM(openops.*) → 与 SSE 同一套 openops 事件处理器（活动线 / RCA / ASK）；
 * 断线 / 刷新恢复仍以 GET /agent-runs/{id}/state 为准。
 *
 * 注：对话 UI 仍是本设计系统的自定义组件（30.3/32 号拍板），CopilotKit 走 headless——
 * 协议与适配器边界已按 AG-UI 对齐，后续升 CopilotKit v2 <CopilotChat> 时后端无需再动。
 */
import { HttpAgent } from "@ag-ui/client";
import { API_BASE } from "../api/client";
import { demoIdentity } from "../api/client";
import type { OpenOpsEvent } from "../api/types";

/** 运行态传输通道：agui（默认，B5）| sse（回退旧 REST+SSE 路径）。 */
export const TRANSPORT: string = (import.meta.env.VITE_OPENOPS_TRANSPORT as string | undefined) ?? "agui";

export interface AguiHandlers {
  /** openops.* 自定义事件（与 SSE 通道同一处理器，按 event_id 去重）。 */
  onOpenOps: (e: OpenOpsEvent) => void;
  /** 助手文本增量（对话主区流式气泡）。 */
  onAssistantDelta: (messageId: string, delta: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
}

export function runAguiTask(runId: string, text: string, h: AguiHandlers): { cancel: () => void } {
  const agent = new HttpAgent({
    url: `${API_BASE}/openops/v1/agent-runs/${runId}/agui`,
    headers: {
      "X-OpenOps-Mock-User": demoIdentity.user,
      "X-OpenOps-Mock-Name": encodeURIComponent(demoIdentity.name),
    },
    // HttpAgent 把 fetch 存成未绑定引用后以 this.fetch() 调用 → 浏览器 Illegal invocation；显式绑定 window
    fetch: (input: string, init: RequestInit) => window.fetch(input, init),
  });
  agent.messages = [{ id: `u_${Date.now()}`, role: "user", content: text }];
  void agent
    .runAgent(undefined, {
      onTextMessageContentEvent(params: { event: { messageId: string; delta: string } }) {
        h.onAssistantDelta(params.event.messageId, params.event.delta);
      },
      onEvent(params: { event: { type: string; name?: string; value?: unknown } }) {
        const ev = params.event;
        if (ev?.type === "CUSTOM" && typeof ev.name === "string" && ev.name.startsWith("openops.")) {
          h.onOpenOps(ev.value as OpenOpsEvent);
        }
      },
      onRunFinishedEvent() {
        h.onDone();
      },
      onRunErrorEvent(params: { event: { message?: string } }) {
        h.onError(String(params.event?.message ?? "运行失败"));
      },
    })
    .catch((err: unknown) => h.onError((err as Error).message));
  return { cancel: () => agent.abortRun() };
}
