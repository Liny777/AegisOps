/**
 * 程序化发送的幂等键/消息 id（纯函数，供 CopilotAutoSend 与单测共用）。
 *
 * 外链 ?q= 场景（无 nonce）：同线程只发一次——保持 82e40e6 以来的缺省行为不变。
 * 卡片按钮场景（带 nonce）：同一会话可多次触发（每次点击一个新 nonce），
 * 但同一 nonce 在 StrictMode 双跑/重挂时仍只发一次。
 */

export interface PendingSend {
  text: string;
  nonce: string;
}

/** 幂等键：无 nonce 退化为 threadId（外链单次语义）。 */
export function programmaticSendKey(threadId: string, nonce?: string): string {
  return nonce ? `${threadId}#${nonce}` : threadId;
}

/** 程序化消息 id：进入 CopilotKit 消息列表，必须全局唯一否则去重吞消息。 */
export function programmaticMessageId(threadId: string, nonce?: string): string {
  return nonce ? `autosend-${threadId}-${nonce}` : `autosend-${threadId}`;
}

/** 每次卡片点击生成新 nonce：时间戳 + 随机段，同毫秒双击也不撞。 */
export function newSendNonce(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}
