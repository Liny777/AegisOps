/**
 * 假设 checkpoint 卡片的纯状态逻辑（openops.diagnosis.checkpoint.* 事件 → 卡片状态）。
 *
 * 服务端权威：opened/extended 带 deadline_at（ISO），前端倒计时只做展示、到 0 不发请求
 * （超时由服务端判定并下发 closed）。所有转移都按 checkpoint_id 守卫——双通道重复投递、
 * 迟到的旧卡事件不得污染新卡。
 */

export interface CheckpointCardState {
  checkpointId: string;
  /** 服务端截止时刻（ISO）；hold 延长后由 extended 事件更新。 */
  deadlineAt: string;
  status: "pending" | "continued" | "added" | "timed_out";
}

/** opened 事件/快照 → 挂起态；缺 checkpoint_id 的畸形 payload 返回 undefined（丢弃）。 */
export function checkpointFromOpened(payload: Record<string, unknown>): CheckpointCardState | undefined {
  const id = payload.checkpoint_id ? String(payload.checkpoint_id) : "";
  if (!id) return undefined;
  return { checkpointId: id, deadlineAt: String(payload.deadline_at ?? ""), status: "pending" };
}

/** extended 事件（hold 延长窗口）：仅同卡且仍 pending 时更新 deadline。 */
export function applyCheckpointExtended(
  current: CheckpointCardState | undefined,
  payload: Record<string, unknown>,
): CheckpointCardState | undefined {
  if (!current || current.status !== "pending") return current;
  const id = payload.checkpoint_id ? String(payload.checkpoint_id) : "";
  if (id && id !== current.checkpointId) return current;
  const deadline = payload.deadline_at ? String(payload.deadline_at) : "";
  return deadline ? { ...current, deadlineAt: deadline } : current;
}

/** closed 事件 → 结果态；旧卡迟到的 closed 不动当前卡。 */
export function applyCheckpointClosed(
  current: CheckpointCardState | undefined,
  payload: Record<string, unknown>,
): CheckpointCardState | undefined {
  if (!current) return current;
  const id = payload.checkpoint_id ? String(payload.checkpoint_id) : "";
  if (id && id !== current.checkpointId) return current;
  const timedOut = payload.timed_out === true;
  const added = payload.action === "add_hypothesis";
  return { ...current, status: timedOut ? "timed_out" : added ? "added" : "continued" };
}

/** 剩余整秒（≥0）；deadline 无效返回 0（卡片显示「正在继续…」而非 NaN）。 */
export function remainingSeconds(deadlineAt: string, nowMs: number): number {
  const deadline = new Date(deadlineAt).getTime();
  if (Number.isNaN(deadline)) return 0;
  return Math.max(0, Math.ceil((deadline - nowMs) / 1000));
}
