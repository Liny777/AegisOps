// 旁观直播（2026-08-17 拍板）：告警诊断由 dispatcher 无头驱动，旁观者对话区靠 SSE 上的
// openops.assistant.delta 实时渲染。本文件是纯函数核（reducer + 守卫），零 React 零 IO。
//
// phase 语义：
//   idle       无直播（初始/已清）
//   live       任务运行中，delta 持续累积
//   finalizing 收到终态——泡保留，等 copilot 侧 connectAgent 刷入真实历史后 clear
//   frozen     历史刷新失败——泡冻结保留（内容不丢），不再接收增量；下次进页由挂载链恢复
//
// 已接受局限：中途才打开页面者只见打开之后的文本（终局刷新补全）；断线窗口正文缺段
// （resyncGap 提示条 + 终局刷新兜底）——瞬态事件不占 SSE 游标、不可补发（后端 events.py 旁路）。

/** 单泡字符上限：旁观直播是临时视图，超长正文截断展示，全文以终局落库的 transcript 为准。 */
export const OBSERVER_BUBBLE_CHAR_LIMIT = 50_000;
/** 泡数上限：防异常长会话把旁观态内存撑爆；超限的新泡直接丢弃（正文以终局历史为准）。 */
export const OBSERVER_BUBBLE_MAX_COUNT = 40;

export interface ObserverBubble {
  messageId: string;
  text: string;
  truncated: boolean;
}

export type ObserverPhase = "idle" | "live" | "finalizing" | "frozen";

export interface ObserverStreamState {
  phase: ObserverPhase;
  taskId: string | null;
  /** message_id 到达序（渲染顺序）。 */
  order: string[];
  bubbles: Record<string, ObserverBubble>;
  /** SSE 曾 resync：瞬态增量不可补发，正文可能缺段（渲染提示条）。 */
  resyncGap: boolean;
}

export type ObserverStreamAction =
  | { type: "task_started"; taskId: string | null }
  | { type: "delta"; messageId: string; delta: string }
  | { type: "resync" }
  | { type: "task_terminal" }
  | { type: "clear" }
  /** 历史刷新失败：冻结保留（对比 clear 丢内容——刷新失败时丢直播泡等于两头空）。 */
  | { type: "freeze" };

export const initialObserverStream = (): ObserverStreamState =>
  ({ phase: "idle", taskId: null, order: [], bubbles: {}, resyncGap: false });

const INITIAL = initialObserverStream();

/** 非法/无效转移一律返回同引用（useSyncExternalStore 依赖引用稳定判"没变"）。 */
export function observerStreamReducer(
  s: ObserverStreamState, a: ObserverStreamAction,
): ObserverStreamState {
  switch (a.type) {
    case "task_started":
      // 同任务幂等重入（/state 快照恢复与 SSE task.started 双入口、refresh 重放）：泡保留。
      // 中途打开页面的旁观者靠快照恢复入 live——task.started 事件早已发过不会补发。
      if (s.phase === "live" && s.taskId !== null && s.taskId === a.taskId) return s;
      // 新任务边界：清空重开（同 run 追问/重试都算新一轮直播）
      return { phase: "live", taskId: a.taskId, order: [], bubbles: {}, resyncGap: false };
    case "delta": {
      if (s.phase !== "live" || !a.delta) return s;
      const prev = s.bubbles[a.messageId];
      if (prev) {
        if (prev.truncated) return s; // 截断后同泡增量直接丢
        const next = prev.text + a.delta;
        const truncated = next.length > OBSERVER_BUBBLE_CHAR_LIMIT;
        return {
          ...s,
          bubbles: {
            ...s.bubbles,
            [a.messageId]: {
              ...prev,
              text: truncated ? next.slice(0, OBSERVER_BUBBLE_CHAR_LIMIT) : next,
              truncated,
            },
          },
        };
      }
      if (s.order.length >= OBSERVER_BUBBLE_MAX_COUNT) return s;
      return {
        ...s,
        order: [...s.order, a.messageId],
        bubbles: { ...s.bubbles, [a.messageId]: { messageId: a.messageId, text: a.delta, truncated: false } },
      };
    }
    case "resync":
      return s.phase === "live" && !s.resyncGap ? { ...s, resyncGap: true } : s;
    case "task_terminal":
      return s.phase === "live" ? { ...s, phase: "finalizing" } : s;
    case "clear":
      return s.phase === "idle" && s.order.length === 0 ? s : { ...INITIAL };
    case "freeze":
      return s.phase === "finalizing" || s.phase === "live" ? { ...s, phase: "frozen" } : s;
    default:
      return s;
  }
}

export interface ObserverDeltaSignals {
  apiMode: "real" | "mock";
  /** 当前工作台展示的 runId（copilot 档即 canonical runId）。 */
  runId: string | null;
  /** SSE 事件里的 agent_run_id。 */
  eventRunId: string;
  taskStatus: string | null;
  runStatus: "active" | "closed";
  /** 本端 agui 直连流的 runId（aguiActive ref）：非空且等于事件 run = 本端在播，非旁观。
   *  注意：copilot 路径本端发送不写 aguiActive——那条路靠 copilotLocalRun 挡。 */
  aguiStreamRunId: string | null;
  /** 本端 CopilotKit 回合进行中（面板桥上报 agent.isRunning）：CopilotChat 自己在播 TEXT_MESSAGE，
   *  SSE delta 若混入 store 会在终局闪现重复正文。fallback 档恒 false。 */
  copilotLocalRun: boolean;
  /** 本端程序化发送在途（pendingSend / autoQuestion）= 本端回合，非旁观。 */
  pendingSend: boolean;
  autoQuestion: boolean;
}

/** 守卫诊断：返回第一条不满足的守卫名（Workbench 的 [observer] 信标打点用），全过返回 null。
 *  顺序即 isObserverDelta 的 AND 链；task_not_running 附实际任务态——「时间线动但对话空白」
 *  的内网排查里，interrupted（后端重启影子快照）与 running 的区分是头号分叉。 */
export function explainObserverSignals(input: ObserverDeltaSignals): string | null {
  if (input.apiMode !== "real") return "api_mode";
  if (input.runId === null) return "no_run";
  if (input.runId !== input.eventRunId) return "run_mismatch";
  if (input.taskStatus !== "running") return `task_not_running(${input.taskStatus ?? "null"})`;
  if (input.runStatus !== "active") return "run_not_active";
  if (input.aguiStreamRunId === input.eventRunId) return "agui_streaming";
  if (input.copilotLocalRun) return "copilot_local_run";
  if (input.pendingSend) return "pending_send";
  if (input.autoQuestion) return "auto_question";
  return null;
}

/** 旁观守卫：这条 delta 属于「别人（后台）在跑、我在看」才放行。任一不满足即丢。 */
export function isObserverDelta(input: ObserverDeltaSignals): boolean {
  return explainObserverSignals(input) === null;
}
