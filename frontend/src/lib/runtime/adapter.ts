/**
 * OpenOpsRuntimeAdapter —— 对话运行态适配器边界（对齐 30.4「CopilotKit 与 AG-UI 事件映射」）。
 *
 * 当前最小闭环：对话工作台通过 OpenOps REST 创建 Run/Task，通过 /state 恢复，通过 /events/stream 消费 SSE。
 * 后续（真实联调）：接入 CopilotKit v2 + AG-UI —— 参考 frontend-v2 的 Node CopilotKit-runtime sidecar
 *   （server/copilot-runtime.ts：HttpAgent → OpenOps AG-UI 代理 SSE、interrupt→tool-call 桥、消息持久化），
 *   把标准事件（RUN_STARTED / TEXT_MESSAGE_* / TOOL_CALL_*）交给 <CopilotChat>，
 *   openops.* 自定义事件（scope/runtime_plan/tool.blocked/approval/model…）写入下方状态投影，
 *   断线/刷新以 GET /agent-runs/{id}/state 恢复。此文件即该 live adapter 的落点。
 */
import type { OpenOpsApi } from "../api";
import type {
  ActivityEventInput,
  DelegationInput,
} from "../activity";
import {
  activityReducer,
  createActivityState,
  mergeActivityEvents,
  mergeDelegations,
  prependActivityPage,
} from "../activity";
import type { ActivityStoreState, WorkbenchState } from "../api/types";

export interface OpenOpsRuntimeAdapter {
  /** 拉取聚合状态（刷新 / 断线恢复入口）。 */
  getState(runId: string): Promise<WorkbenchState>;
  /** 发送新任务；返回订阅取消函数（live 下经 AG-UI 流增量回调）。 */
  sendTask(runId: string, text: string, onEvent: (partial: Partial<WorkbenchState>) => void): () => void;
  /** ASK 审批决策。 */
  decideApproval(approvalId: string, decision: "approved" | "rejected", reason?: string): Promise<void>;
  /** 会话级临时模型切换（不生成 config_version）。 */
  selectModel(runId: string, llmConfigId: string): Promise<void>;
  cancelTask(taskId: string): Promise<void>;
  closeRun(runId: string): Promise<void>;
}

/** live AG-UI 运行态已接入（B5）：见 ./agui.ts —— HttpAgent 打 POST /agent-runs/{id}/agui，
 *  标准事件流式进对话区、CUSTOM(openops.*) 与 SSE 复用同一处理器；Workbench 直接消费。
 *  本聚合接口保留为后续 CopilotKit v2 <CopilotChat> 接入时的边界。 */
export function createLiveAdapter(): OpenOpsRuntimeAdapter {
  throw new Error("请使用 lib/runtime/agui.ts 的 runAguiTask（B5 已接入）；本聚合接口留给 CopilotKit v2。");
}

/**
 * 活动栏的数据通道边界。
 *
 * CopilotKit/AG-UI 的具体订阅方式由工作台负责；适配器只处理已收到的 CUSTOM/SSE
 * 事件与 REST 恢复，因此不依赖未验证的 useAgent.subscribe 行为。
 */
export interface OpenOpsActivityRuntimeAdapter {
  restore(runId: string, current?: ActivityStoreState): Promise<ActivityStoreState>;
  mergeLive(state: ActivityStoreState, event: ActivityEventInput): ActivityStoreState;
  mergeLedger(state: ActivityStoreState, rows: DelegationInput[]): ActivityStoreState;
  loadEarlier(runId: string, state: ActivityStoreState, limit?: number): Promise<ActivityStoreState>;
}

export function createActivityRuntimeAdapter(
  client: Pick<OpenOpsApi, "getRunState" | "getActivityEvents">,
): OpenOpsActivityRuntimeAdapter {
  return {
    async restore(runId, current = createActivityState()) {
      return activityReducer(current, { type: "hydrate", snapshot: await client.getRunState(runId) });
    },
    mergeLive(state, event) {
      return { ...state, events: mergeActivityEvents(state.events, [event], "live") };
    },
    mergeLedger(state, rows) {
      return { ...state, delegations: mergeDelegations(state.delegations, rows) };
    },
    async loadEarlier(runId, state, limit = 100) {
      if (!state.hasMore || !state.nextCursor) return state;
      const page = await client.getActivityEvents(runId, { before: state.nextCursor, limit });
      return prependActivityPage(state, page);
    },
  };
}
