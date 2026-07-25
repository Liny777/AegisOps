/** Agent Studio 切片的 API 类型（原在 lib/api/types.ts，随切片外移）。
 *
 * ⚠ 与活动流/审计的「只暴露脱敏 payload」约定不同：Studio 是管理员/本人专用通道，
 * 后端返回 span **原文**（LLM 输入输出 / 工具入参结果 / 交接文本，服务端已按长度截断）。
 * 用户回放（replay*）的 LLM 输入由**服务端置空**，不是前端隐藏——不要在这里加"前端过滤"。
 */

export interface StudioRunStats {
  agents: number;
  llm_calls: number;
  tool_calls: number;
  total_tokens: number;
  latency_ms: number;
}
export interface StudioRunSummary {
  agent_run_id: string;
  run_title: string;
  run_status: string;
  started_at: string | null;
  ended_at: string | null;
  /** 用户软删的会话对管理员复盘仍可见，只打标记。 */
  deleted: boolean;
  framework_session_id: string;
  stats: StudioRunStats;
}
export interface StudioRunsPage {
  items: StudioRunSummary[];
  total: number;
  page: number;
  page_size: number;
}
export interface StudioLlmCall {
  kind: "llm";
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  cache_input_tokens: number;
  latency_ms: number;
  started_at: string | null;
  finish_reason: string;
  /** 本次调用的输入 messages（序列化原文，截断）。 */
  input: string;
  /** 本次调用的模型输出（文本 + 工具决策，序列化原文，截断）。 */
  output: string;
  status: string;
}
export interface StudioToolCall {
  kind: "tool";
  tool_name: string;
  tool_args: string;
  tool_result: string;
  latency_ms: number;
  started_at: string | null;
  status: string;
}
export type StudioCall = StudioLlmCall | StudioToolCall;
/** 主↔子交接（sre_agent_delegation 账本）：task_text=派发指令，report_text=子 Agent 汇报。 */
export interface StudioHandover {
  delegation_id: string;
  agent_key: string;
  task_text: string;
  report_text: string;
  delegation_status: string;
  dispatch_batch_no: number | null;
  had_final_report: boolean;
  creation_date: string | null;
}
export interface StudioAgentTotals {
  llm_calls: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  latency_ms: number;
}
export interface StudioAgentCardData {
  session_id: string;
  role: string;
  agent_name: string;
  is_main: boolean;
  /** agent 层输入输出（invoke_agent span）：主=用户问题/最终答复，子=派发指令/汇报。 */
  agent_io: { input: string; output: string } | null;
  handover: StudioHandover | null;
  calls: StudioCall[];
  totals: StudioAgentTotals;
}
export interface StudioRollup {
  agents: number;
  llm_calls: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  total_latency_ms: number;
  models: string[];
}
/** run 对话记录一条（与用户端 /messages 同投影：仅 user/assistant 文本，全文无截断，无时间戳）。 */
export interface StudioMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}
export interface StudioRunDetail {
  run: {
    agent_run_id: string;
    user_id: string;
    run_title: string;
    run_status: string;
    started_at: string | null;
    ended_at: string | null;
    deleted: boolean;
    framework_session_id: string;
  };
  agents: StudioAgentCardData[];
  rollup: StudioRollup;
  /** 全量派发账本（零 span 的派发也在，如启动即失败的子任务）。 */
  delegations: StudioHandover[];
}
