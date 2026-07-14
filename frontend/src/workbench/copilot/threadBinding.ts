/** CopilotKit 共享 Agent 的最小 thread 绑定契约。 */
export interface ThreadBindableAgent {
  threadId?: string;
}

/**
 * 在 layout phase 同步绑定目标 Run。CopilotChat 自身的 passive effect 仍负责 connect，
 * 这里只消除“新 UI 已可输入、共享 Agent 仍指向旧 thread”的一帧竞态。
 */
export function bindCopilotThread(agent: ThreadBindableAgent, runId: string): void {
  agent.threadId = runId;
}

/** 输入仅在本次 layout commit 和共享 Agent 都确认同一 Run 后开放。 */
export function isCopilotThreadReady(
  runId: string,
  committedRunId: string | null,
  agentThreadId: string | undefined,
  blocked = false,
): boolean {
  return !blocked && committedRunId === runId && agentThreadId === runId;
}

/** DOM inert 不会阻止 React effect；程序化自动发送必须额外遵守会话阻断状态。 */
export function shouldMountCopilotAutoSend(
  blocked: boolean,
  question: string | null | undefined,
  hasSentHandler: boolean,
): boolean {
  return !blocked && Boolean(question) && hasSentHandler;
}
