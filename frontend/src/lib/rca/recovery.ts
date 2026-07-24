/**
 * 恢复执行节点的状态推导（纯函数）。
 *
 * 从 activityState.events 投影恢复动作的审批/执行进度，而非另建 reducer——事件
 * 去重、时序、hydrate 与切会话清理全部复用活动流既有语义，调用方用 useMemo 包住即可。
 *
 * 推导规则（与后端事实对齐，file 见 backend/src/runtime/agentscope_runtime.py 等）：
 * - 段过滤按**时间锚定**而非任务相等：真实主流程里恢复动作发生在新任务（点「采纳并
 *   生成恢复动作」= 新消息 = start_task 新建 task_id），其审批事件的 taskId 永远不等于
 *   诊断板的 boardTaskId——按任务相等过滤会把主场景整段滤掉（审查确认项）。锚 = 当前板
 *   首条 rca.updated（payload.board_task_id 匹配）的时刻，锚后的审批/工具事件都算本轮
 *   恢复候选；锚前（上一轮的恢复痕迹）排除。taskId/leaderTaskId 相等仍作为补充纳入。
 *   boardTaskId=null 或找不到锚（事件窗口滚出）时不过滤——宁可多显示不可失踪。
 * - **所有 `approval.required` 都是恢复候选**：ASK 机制只为写类动作存在（标注
 *   is_approval_required），按 tool 名白名单过滤必漏（真实 MCP 写工具名不可枚举）。
 * - 事件类型后缀匹配：审计行无前缀（`approval.approved`）与 SSE（`openops.approval.approved`）
 *   同判，剥掉 `openops.` 前缀后精确比较（不用 includes，避免误吞近名事件）。
 * - 迁移单调不回退：pending→approved→executing→executed（rejected/timeout/failed 同为
 *   终态档），乱序/重复投递靠 rank 守卫幂等——先到的终态赢，迟到的 required 不闪回。
 * - `approval.approved/rejected` payload 无 tool 字段（decide_approval 只发
 *   approval_request_id），必须按 id 关联；`approval.timeout` 带 id 精确收口（expire
 *   审计路径），不带 id（runtime 循环超时补发，payload 为空）只收口 pending 条目。
 * - `tool.call.started/succeeded/failed` 按 **tool 名** FIFO 配对 approved/executing
 *   条目：脱敏白名单不放行 tool_call_id（infra/redact.py），同名工具并发审批可能
 *   误配相邻条目（含终态种类互换）——已声明局限，影响面为并发同名审批的展示归属。
 * - 容器 Bash 审批（tool=run_container_command）**不做执行收口**：其执行完成只发
 *   `sandbox.command.executed`，而该事件无审批关联字段、allow 只读路径发完全同形事件
 *   （backend sandbox_bash.py），按名收口必然把只读命令误记成恢复执行（甚至误标失败）。
 *   诚实地停在「已批准」；真实 MCP 恢复工具仍经 tool.call.* 正常收口（审查确认项）。
 * - `pendingApprovals`（/state.pending_approvals 行）作种子且**在事件折叠之后**注入：
 *   行是「当前仍待批」的实时状态,不得被历史里无 id 的 timeout 补发误收口；防
 *   recent_events 100 条窗口把 required 挤出后条目凭空消失；按 approval_request_id 去重。
 * - 后端从不发 approval.cancelled 事件（取消只翻库行），此处不建 cancelled 相位；
 *   任务取消后的孤儿 pending 条目随下一次 /state 快照（种子消失）自然收敛。
 */
import type { ActivityEvent } from "../api/types";
import { compareActivityEvents } from "../activity/model";

/** 单条恢复动作的相位；pending/approved/executing 为进行档，其余为终态档。 */
export type RecoveryActionPhase =
  | "pending"
  | "approved"
  | "executing"
  | "executed"
  | "rejected"
  | "timeout"
  | "failed";

/** 聚合相位：idle = 无恢复候选（第六节点灰显）。 */
export type RecoveryPhase = "idle" | RecoveryActionPhase;

export interface RecoveryAction {
  approvalRequestId: string;
  /** 审批行/required 事件的工具名；两条路径都缺失时兜底 "unknown_tool"。 */
  tool: string;
  /** required 事件的人话摘要（如「恢复动作待批准：重启 svc-a」）；种子行无摘要则缺省。 */
  label?: string;
  phase: RecoveryActionPhase;
  /** 进入待审批的时刻（required 事件 occurred_at / 审批行 creation_date）。 */
  requiredAt?: string;
  /** 最近一次相位迁移的时刻。 */
  updatedAt?: string;
}

export interface RecoveryCounts {
  total: number;
  pending: number;
  approved: number;
  executing: number;
  executed: number;
  rejected: number;
  timeout: number;
  failed: number;
}

export interface RecoveryState {
  actions: RecoveryAction[];
  counts: RecoveryCounts;
  phase: RecoveryPhase;
}

export interface DeriveRecoveryOptions {
  /** 诊断面板分段键（board_task_id ?? task_id）；null = 不过滤。 */
  boardTaskId: string | null;
  /** /state.pending_approvals 原始行（sre_approval_request 投影）。 */
  pendingApprovals?: readonly Record<string, unknown>[];
}

const UNKNOWN_TOOL = "unknown_tool";

/** 相位档位：迁移只允许升档（rank 严格递增），终态同档先到先赢。 */
const PHASE_RANK: Record<RecoveryActionPhase, number> = {
  pending: 0,
  approved: 1,
  executing: 2,
  executed: 3,
  rejected: 3,
  timeout: 3,
  failed: 3,
};

const str = (value: unknown): string | undefined => {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return undefined;
};

/** 剥 `openops.` 前缀后的小写事件类型：审计裸类型与 SSE 前缀类型同判。 */
const bareType = (eventType: string): string => {
  const type = eventType.toLowerCase();
  return type.startsWith("openops.") ? type.slice("openops.".length) : type;
};

const actionTime = (action: RecoveryAction): number => {
  const parsed = Date.parse(action.updatedAt ?? action.requiredAt ?? "");
  return Number.isFinite(parsed) ? parsed : 0;
};

/** rank 守卫下的相位迁移；升档才生效，重复/乱序投递天然幂等。 */
function transition(action: RecoveryAction, next: RecoveryActionPhase, at: string): void {
  if (PHASE_RANK[next] <= PHASE_RANK[action.phase]) return;
  action.phase = next;
  if (at) action.updatedAt = at;
}

export function deriveRecoveryState(
  events: readonly ActivityEvent[],
  options: DeriveRecoveryOptions,
): RecoveryState {
  const { boardTaskId, pendingApprovals = [] } = options;
  /** 插入序 ≈ 时间序（事件按 compareActivityEvents 折叠，种子后置为 pending），FIFO 配对依赖它。 */
  const byId = new Map<string, RecoveryAction>();

  // ── 稳定时序（活动流已排序，此处防御性再排一遍保证纯函数自洽）
  const ordered = [...events].sort(compareActivityEvents);

  // ── 时间锚定：当前板首条 rca.updated 的时刻。恢复动作发生在新任务（采纳按钮=新消息
  //    =新 task_id），按任务相等过滤会把主场景整段滤掉——锚后的事件都算本轮候选。
  let anchorTime: number | undefined;
  if (boardTaskId !== null) {
    for (const event of ordered) {
      if (bareType(event.eventType) !== "rca.updated") continue;
      const eventBoardId = str(event.redactedPayload.board_task_id) ?? event.taskId;
      if (eventBoardId !== boardTaskId) continue;
      const parsed = Date.parse(event.occurredAt);
      if (Number.isFinite(parsed)) anchorTime = parsed;
      break;
    }
  }
  const inSegment = (event: ActivityEvent): boolean => {
    if (boardTaskId === null) return true;
    if (event.taskId === boardTaskId || event.leaderTaskId === boardTaskId) return true;
    if (anchorTime === undefined) return true; // 找不到锚（窗口滚出）：宁可多显示不可失踪
    const at = Date.parse(event.occurredAt);
    return !Number.isFinite(at) || at >= anchorTime;
  };
  const segment = ordered.filter(inSegment);

  /** 按 tool 名 FIFO 配对最早的候选条目（脱敏白名单无 tool_call_id，见文件头声明的局限）。 */
  const pairByTool = (
    tool: string | undefined,
    fromPhases: readonly RecoveryActionPhase[],
    next: RecoveryActionPhase,
    at: string,
  ): void => {
    if (!tool) return;
    for (const action of byId.values()) {
      if (action.tool !== tool || !fromPhases.includes(action.phase)) continue;
      transition(action, next, at);
      return;
    }
  };

  for (const event of segment) {
    const type = bareType(event.eventType);
    const payload = event.redactedPayload;
    const id = str(payload.approval_request_id);

    if (type === "approval.required") {
      // 后端两条 ASK 路径（工具门/容器 Bash 桥）都带 approval_request_id；缺 id 无法追踪迁移，跳过。
      if (!id) continue;
      const existing = byId.get(id);
      if (existing) {
        // 种子/重复投递：不回退相位，只补齐展示字段（种子行无 message）。
        if (existing.tool === UNKNOWN_TOOL) existing.tool = str(payload.tool) ?? existing.tool;
        if (!existing.label) existing.label = event.message || str(payload.summary);
        if (!existing.requiredAt) existing.requiredAt = event.occurredAt;
      } else {
        byId.set(id, {
          approvalRequestId: id,
          tool: str(payload.tool) ?? UNKNOWN_TOOL,
          label: event.message || str(payload.summary),
          phase: "pending",
          requiredAt: event.occurredAt,
          updatedAt: event.occurredAt,
        });
      }
    } else if (type === "approval.approved" || type === "approval.rejected") {
      // decide_approval 的 payload 无 tool 字段，只能按 id 关联；required 被窗口挤出且
      // 不在种子（已决即非 pending）时补建条目，保住终态可见性。
      if (!id) continue;
      const next: RecoveryActionPhase = type === "approval.approved" ? "approved" : "rejected";
      const existing = byId.get(id);
      if (existing) transition(existing, next, event.occurredAt);
      else {
        byId.set(id, {
          approvalRequestId: id,
          tool: str(payload.tool) ?? UNKNOWN_TOOL,
          phase: next,
          updatedAt: event.occurredAt,
        });
      }
    } else if (type === "approval.timeout") {
      if (id) {
        // expire 审计路径带 id（payload 还带 tool）：精确收口，条目缺失则补建。
        const existing = byId.get(id);
        if (existing) transition(existing, "timeout", event.occurredAt);
        else {
          byId.set(id, {
            approvalRequestId: id,
            tool: str(payload.tool) ?? UNKNOWN_TOOL,
            phase: "timeout",
            updatedAt: event.occurredAt,
          });
        }
      } else {
        // runtime 循环超时补发（payload 为空）：只收口「该时刻之前已在等」的 pending——
        // 不动 approved/executing，也不动晚于补发时刻才创建的条目（历史 timeout 不得
        // 误杀后来的真 pending）。已批准动作是否执行由 tool.call 事件定夺。
        const cutoff = Date.parse(event.occurredAt);
        for (const action of byId.values()) {
          if (action.phase !== "pending") continue;
          const requiredAt = Date.parse(action.requiredAt ?? "");
          if (!Number.isFinite(cutoff) || (Number.isFinite(requiredAt) && requiredAt <= cutoff)) {
            transition(action, "timeout", event.occurredAt);
          }
        }
      }
    } else if (type === "tool.call.started") {
      pairByTool(str(payload.tool), ["approved"], "executing", event.occurredAt);
    } else if (type === "tool.call.succeeded") {
      // started 可能被窗口挤出：approved 直达 executed 也接受。
      pairByTool(str(payload.tool), ["approved", "executing"], "executed", event.occurredAt);
    } else if (type === "tool.call.failed") {
      pairByTool(str(payload.tool), ["approved", "executing"], "failed", event.occurredAt);
    }
    // 注意：sandbox.command.executed 刻意不收口（无审批关联字段、allow 只读路径同形,
    // 按名收口必误配——容器 Bash 审批诚实地停在「已批准」,见文件头声明）。
  }

  // ── 种子后置：/state.pending_approvals 是「当前仍待批」的实时状态，折叠后补建缺失
  //    条目即可——先于事件注入会被历史里无 id 的 timeout 补发误收口。行是实时真值,
  //    不做段过滤(上一轮残留的 pending 仍然可操作、值得显示)。
  for (const rowValue of pendingApprovals) {
    const rowData = rowValue ?? {};
    const id = str(rowData.approval_request_id);
    if (!id) continue;
    const createdAt = str(rowData.creation_date) ?? str(rowData.created_at);
    const existing = byId.get(id);
    if (existing) {
      // 事件已建的条目保持其推导相位，只回填缺失的展示字段（required 被窗口挤出、
      // 仅剩 approved 事件时 tool 靠审批行补齐）。
      if (existing.tool === UNKNOWN_TOOL) existing.tool = str(rowData.tool_call_name) ?? existing.tool;
      if (!existing.requiredAt) existing.requiredAt = createdAt;
      continue;
    }
    byId.set(id, {
      approvalRequestId: id,
      tool: str(rowData.tool_call_name) ?? UNKNOWN_TOOL,
      phase: "pending",
      requiredAt: createdAt,
      updatedAt: createdAt,
    });
  }

  // ── 聚合
  const actions = [...byId.values()];
  const counts: RecoveryCounts = {
    total: actions.length,
    pending: 0, approved: 0, executing: 0, executed: 0, rejected: 0, timeout: 0, failed: 0,
  };
  for (const action of actions) counts[action.phase] += 1;

  let phase: RecoveryPhase = "idle";
  if (counts.pending) phase = "pending";
  else if (counts.executing) phase = "executing";
  else if (counts.approved) phase = "approved";
  else if (actions.length) {
    // 全部终态：取最近一次迁移的终态（时间平手取靠后条目，与插入序一致）。
    let latest = actions[0];
    for (const action of actions) {
      if (actionTime(action) >= actionTime(latest)) latest = action;
    }
    phase = latest.phase;
  }

  return { actions, counts, phase };
}
