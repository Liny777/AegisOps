/**
 * 定界面板的 revision 守卫（纯函数）。
 *
 * 双通道（AG-UI CUSTOM + 备用 SSE）会重复投递同一事件，SSE 重连 resync 也可能带回
 * 旧快照；渲染层靠 revision 幂等去重、永不闪回。最关键的一条：**revision 按面板身份
 * 分段单调**——同一 run 里第二次定界任务的 revision 从 1 重计，分段键变更即接受并
 * 重置基线，否则新任务的面板会被旧任务的高 revision 永久压制。
 *
 * 分段键由调用方提供：优先 payload.board_task_id（owner 主任务身份——事件 envelope
 * 的 task_id 是「调用方」，子 Agent 更新与主任务兜底会交替出现，不能当分段键），缺失
 * 回退 envelope task_id（demo/旧后端）。跨段迟到副本由 Workbench 的 firstDelivery
 * 门挡住（双通道各自有序，首达序即全序），本守卫不必防。
 */

export interface RcaGuard {
  /** 当前面板分段键（board_task_id ?? task_id）；null = 尚无面板或快照未带任务归属。 */
  taskId: string | null;
  /** 该任务内已应用的最大 revision；null = 尚未见过带 revision 的 payload。 */
  revision: number | null;
}

export const initialRcaGuard = (): RcaGuard => ({ taskId: null, revision: null });

/** 是否应用该 payload：跨 task 即接受；同 task 要求 revision 严格递增；缺 revision 的旧 payload last-write-wins。 */
export function shouldApplyRca(
  guard: RcaGuard,
  taskId: string | null | undefined,
  revision: number | null | undefined,
): boolean {
  const nextTask = taskId ?? null;
  if (nextTask !== guard.taskId) return true; // task 变更：新任务面板重计，无条件接受
  if (revision == null || guard.revision == null) return true; // 旧后端无 revision：后到覆盖先到
  return revision > guard.revision;
}

/** 应用成功后推进守卫；同 task 缺 revision 时保留已知基线（不倒退）。 */
export function nextRcaGuard(
  guard: RcaGuard,
  taskId: string | null | undefined,
  revision: number | null | undefined,
): RcaGuard {
  const nextTask = taskId ?? null;
  const sameTask = nextTask === guard.taskId;
  return {
    taskId: nextTask,
    revision: revision ?? (sameTask ? guard.revision : null),
  };
}
