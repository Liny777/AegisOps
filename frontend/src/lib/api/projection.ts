/** 后端 shape → 前端视图模型投影（/state、audit_event、approval_request、openops.* 事件）。 */
import type {
  ActivityGroup,
  ActivityNode,
  AgentInstance,
  HitlCardData,
  OpenOpsEvent,
  RcaCardData,
} from "./types";
import type { Tone } from "../../theme/tokens";

/* 事件类型 → 活动线节点外观 */
const EVENT_META: Record<string, { icon: string; tone: Tone; title: string }> = {
  // D 块：子 Agent 编排事件
  "openops.subagent.dispatched": { icon: "send", tone: "neutral", title: "派发子 Agent" },
  "openops.subagent.started": { icon: "robot", tone: "neutral", title: "子 Agent 启动" },
  "openops.subagent.reported": { icon: "clipboard-check", tone: "good", title: "子 Agent 汇报" },
  "openops.subagent.timeout": { icon: "clock-exclamation", tone: "warning", title: "子 Agent 超时" },
  "openops.subagent.failed": { icon: "alert-triangle", tone: "warning", title: "子 Agent 异常" },
  "agent_run.created": { icon: "player-play", tone: "neutral", title: "会话创建" },
  "task.started": { icon: "bolt", tone: "neutral", title: "任务启动" },
  "openops.task.started": { icon: "bolt", tone: "neutral", title: "任务启动" },
  "scope.resolved": { icon: "target", tone: "good", title: "范围解析" },
  "openops.scope.resolved": { icon: "target", tone: "good", title: "范围解析" },
  // B3：范围版本变更 / 被阻断（scope.updated 可能早于 task.started —— resolve 先于 task 启动，时间线按序渲染即可）
  "scope.updated": { icon: "refresh", tone: "neutral", title: "范围更新" },
  "openops.scope.updated": { icon: "refresh", tone: "neutral", title: "范围更新" },
  "scope.blocked": { icon: "ban", tone: "danger", title: "范围被阻断" },
  "openops.scope.blocked": { icon: "ban", tone: "danger", title: "范围被阻断" },
  "openops.tool.call.started": { icon: "tool", tone: "neutral", title: "工具调用" },
  "openops.tool.call.succeeded": { icon: "circle-check", tone: "good", title: "工具完成" },
  "openops.tool.call.failed": { icon: "alert-triangle", tone: "danger", title: "工具失败" },
  "openops.tool.blocked": { icon: "ban", tone: "danger", title: "工具被阻断" },
  // B2：模型推理事件
  "openops.model.call.started": { icon: "cpu", tone: "neutral", title: "模型推理" },
  "openops.model.call.succeeded": { icon: "cpu", tone: "good", title: "模型完成" },
  "openops.model.call.failed": { icon: "alert-triangle", tone: "danger", title: "模型失败" },
  "openops.rca.updated": { icon: "report-search", tone: "neutral", title: "RCA 更新" },
  "openops.approval.required": { icon: "shield-check", tone: "warning", title: "ASK · 等待批准" },
  "approval.approved": { icon: "shield-check", tone: "good", title: "已批准" },
  "openops.approval.approved": { icon: "shield-check", tone: "good", title: "已批准" },
  "approval.rejected": { icon: "shield-x", tone: "danger", title: "已拒绝" },
  "openops.approval.rejected": { icon: "shield-x", tone: "danger", title: "已拒绝" },
  "openops.approval.timeout": { icon: "clock-x", tone: "warning", title: "批准超时" },
  "openops.task.completed": { icon: "flag-check", tone: "good", title: "任务完成" },
  "openops.task.cancelled": { icon: "player-stop", tone: "warning", title: "任务取消" },
  "openops.task.failed": { icon: "alert-triangle", tone: "danger", title: "任务失败" },
  "openops.run.closed": { icon: "lock", tone: "neutral", title: "会话关闭" },
  "openops.model.selected": { icon: "cpu", tone: "neutral", title: "模型切换" },
  "model.selected": { icon: "cpu", tone: "neutral", title: "模型切换" },
  "run.closed": { icon: "lock", tone: "neutral", title: "会话关闭" },
};

function meta(t: string) {
  return EVENT_META[t] ?? { icon: "info-circle", tone: "neutral" as Tone, title: t };
}

const hhmm = (iso?: string) => (iso ? iso.slice(11, 16) : "");

/** audit_event 行 → 活动节点。 */
export function auditToNode(e: Record<string, unknown>): ActivityNode {
  const t = String(e.event_type ?? "");
  const m = meta(t);
  const p = (e.payload_redacted_json ?? {}) as Record<string, unknown>;
  return {
    id: String(e.audit_event_id ?? e.event_id ?? Math.random()),
    title: m.title,
    tool: t,
    detail: String(p.summary ?? p.reason ?? e.reason_code ?? "") || String(e.action ?? ""),
    time: hhmm(String(e.occurred_at ?? "")),
    icon: m.icon,
    tone: m.tone,
    agentKey: (p as Record<string, unknown>).agent_key ? String((p as Record<string, unknown>).agent_key) : undefined,
  };
}

/** openops.* SSE 事件 → 活动节点。 */
export function eventToNode(e: OpenOpsEvent): ActivityNode {
  const m = meta(e.event_type);
  const ak = ((e.payload_redacted_json ?? {}) as Record<string, unknown>).agent_key;
  return {
    id: e.event_id,
    title: m.title,
    tool: e.event_type,
    detail: e.message,
    time: hhmm(e.occurred_at),
    icon: m.icon,
    tone: m.tone,
    agentKey: ak ? String(ak) : undefined,
  };
}

export function groupNodes(nodes: ActivityNode[], running: boolean): ActivityGroup[] {
  const items = nodes.map((n, i) => ({
    ...n,
    running: running && i === nodes.length - 1,
  }));
  // D 块：子 Agent 事件（payload.agent_key ≠ main）各自成组（SubagentActivityRail 口径，30.3 §八）
  const main = items.filter((n) => !n.agentKey || n.agentKey === "main");
  const groups: ActivityGroup[] = [{ label: "时间线", items: main }];
  const byKey = new Map<string, typeof items>();
  for (const n of items) {
    if (n.agentKey && n.agentKey !== "main") {
      if (!byKey.has(n.agentKey)) byKey.set(n.agentKey, []);
      byKey.get(n.agentKey)!.push(n);
    }
  }
  for (const [k, v] of byKey) groups.push({ label: `子 Agent · ${k}`, items: v, roleKey: k, status: subGroupStatus(v, running) });
  return groups;
}

/** E3：子 Agent 组状态推导（事件即真相）：终态事件优先，任务仍在跑则视为运行中。 */
function subGroupStatus(items: ActivityNode[], running: boolean): ActivityGroup["status"] {
  const types = items.map((n) => n.tool ?? "");
  if (types.some((t) => t.includes("subagent.timeout") || t.includes("subagent.failed"))) return "failed";
  if (types.some((t) => t.includes("subagent.reported"))) return "done";
  return running ? "running" : undefined;
}

/** approval_request 行 → HITL 卡数据。 */
export function approvalToHitl(a: Record<string, unknown>): HitlCardData {
  const args = (a.arguments_redacted_json ?? {}) as Record<string, unknown>;
  const expire = a.expire_at ? new Date(String(a.expire_at)) : null;
  const remainMs = expire ? expire.getTime() - Date.now() : 0;
  const mm = Math.max(0, Math.floor(remainMs / 60000));
  const ss = Math.max(0, Math.floor((remainMs % 60000) / 1000));
  // bash（run_container_command）审批的 args 是 {command}，恢复类是 {target/appid/action}
  // ——按付形状取事实，避免 bash 审批「目标/动作」两栏全"—"没法判断（与 SSE 路径同口径）
  const facts = args.command
    ? [
        { label: "命令", value: String(args.command) },
        { label: "任务", value: String(a.task_id ?? "—") },
      ]
    : [
        { label: "工具", value: String(a.tool_call_name ?? "") },
        { label: "目标", value: String(args.target ?? args.appid ?? "—") },
        { label: "动作", value: String(args.action ?? "—") },
        { label: "任务", value: String(a.task_id ?? "—") },
      ];
  return {
    approval_request_id: String(a.approval_request_id),
    title: "需要人工批准",
    tool: String(a.tool_call_name ?? "tool"),
    summary: args.command
      ? "Agent 请求在你的容器内执行非只读命令，批准后才会真正执行。"
      : "Agent 建议执行受控恢复动作，确认后才会解密凭证并调用工具。",
    facts,
    countdown: `${mm}:${String(ss).padStart(2, "0")}`,
    status: "pending",
    tone: "warning",
  };
}

/** 后端 rca payload（编排器产）→ RcaCardData（形状即为前端模型，补 time）。 */
export function projectRca(p: Record<string, unknown> | null | undefined): RcaCardData | undefined {
  if (!p || !p.title) return undefined;
  return { ...(p as unknown as RcaCardData), time: hhmm(new Date().toISOString().replace("T", " ").slice(0, 16) + ":00") || undefined };
}

/** 后端实例行 → 前端 AgentInstance。wsNames = oModel workspace id→名称映射
 * （DB 实例行只存 workspace_id；卡片「系统范围」要显示工作空间名称，拿不到名字回退 id）。 */
export function projectInstance(r: Record<string, unknown>, wsNames?: Map<string, string>): AgentInstance {
  const wsId = String(r.workspace_id ?? "");
  return {
    instance_id: String(r.instance_id ?? r.agent_team_instance_id),
    name: String(r.instance_name ?? r.name ?? ""),
    template: "感知快恢 Agent",
    workspace_id: wsId,
    workspace_label: wsNames?.get(wsId) ?? wsId,
    scope_revision: String(r.scope_revision ?? ""),
    status: (r.status as AgentInstance["status"]) ?? "active",
    active_config_version: String(r.active_config_version_id ?? "").slice(0, 8),
    counts: "",
    desc: "自动接管告警，执行诊断与恢复",
    model: "千问 (平台提供)",
  };
}
