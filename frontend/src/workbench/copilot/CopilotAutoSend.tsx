// 外链自动发问 · CopilotChat 路径 bridge（老项目 AutoSendBridge 的 v2 对位）。
//
// 挂在 <CopilotKit> 内，经 useAgent 拿共享 AbstractAgent，程式化 addMessage + runAgent
// ——与用户在输入框敲回车等效，消息即时进面板且流式回复正常渲染。
//
// 关键守卫（82e40e6 教训"发错会话"的 v2 版）：只在 agent.threadId === 本面板 runId 时才发。
// CopilotChat 挂载后才把 threadId 绑到 agent，bridge effect 可能先跑——轮询短等而非直发。
// threadId 做幂等键：同线程只发一次，StrictMode 双跑/重挂不重发。
import { useEffect, useRef } from "react";
import { useAgent } from "@copilotkit/react-core/v2";

export function CopilotAutoSend({
  question,
  threadId,
  agentId,
  onSent,
}: {
  question: string | null;
  threadId: string;
  agentId: string;
  onSent: () => void;
}) {
  const { agent } = useAgent({ agentId });
  const sentForRef = useRef<string | null>(null);

  useEffect(() => {
    if (!question || !agent) return;
    if (sentForRef.current === threadId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const started = Date.now();
    const trySend = () => {
      if (cancelled || sentForRef.current === threadId) return;
      const bound = (agent as { threadId?: string }).threadId;
      if (bound !== threadId) {
        // CopilotChat 尚未把 threadId 绑上 agent：短轮询等就绪；超时放弃并清 pending（宁丢勿发错会话）
        if (Date.now() - started > 10_000) {
          console.error("[autosend] agent.threadId 未就绪（bound=%s expect=%s），放弃自动发送", bound, threadId);
          onSent();
          return;
        }
        timer = setTimeout(trySend, 100);
        return;
      }
      sentForRef.current = threadId;
      console.info("[autosend] appending question to copilot thread", threadId);
      void (async () => {
        try {
          agent.addMessage({ id: `autosend-${threadId}`, role: "user", content: question });
          await agent.runAgent();
          console.info("[autosend] copilot runAgent completed");
        } catch (e) {
          console.error("[autosend] copilot send failed:", e);
        } finally {
          onSent(); // 失败也清 pending，防卡死重放（b5c5c79 同取舍）
        }
      })();
    };
    trySend();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [question, threadId, agent, onSent]);

  return null;
}
