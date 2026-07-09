/**
 * OpenOpsRuntimeAdapter —— 对话运行态适配器边界（对齐 30.4「CopilotKit 与 AG-UI 事件映射」）。
 *
 * 本 pass（mock）：对话工作台直接消费 `lib/api` 的 getWorkbenchState()（后端批量 /ag-ui + /state + demo）。
 * 后续（真实联调）：接入 CopilotKit v2 + AG-UI —— 参考 frontend-v2 的 Node CopilotKit-runtime sidecar
 *   （server/copilot-runtime.ts：HttpAgent → AgentScope /agui/run SSE、interrupt→tool-call 桥、消息持久化），
 *   把标准事件（RUN_STARTED / TEXT_MESSAGE_* / TOOL_CALL_*）交给 <CopilotChat>，
 *   openops.* 自定义事件（scope/runtime_plan/tool.blocked/approval/model…）写入下方状态投影，
 *   断线/刷新以 GET /agent-runs/{id}/state 恢复。此文件即该 live adapter 的落点。
 */
import type { WorkbenchState } from "../api/types";

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

/** live adapter 占位：真实联调阶段实现（CopilotKit runtime + AG-UI）。 */
export function createLiveAdapter(): OpenOpsRuntimeAdapter {
  throw new Error("live AG-UI adapter 尚未接入——待真实后端联调阶段实现（见本文件顶部说明）。");
}
