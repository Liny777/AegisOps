/** 后端 shape → 前端视图模型投影（/state、audit_event、approval_request、openops.* 事件）。 */
import type {
  ActivityGroup,
  ActivityNode,
  AgentInstance,
  HitlCardData,
  HitlFact,
  OpenOpsEvent,
  RcaCardData,
} from "./types";
import type { Tone } from "../../theme/tokens";
import { normalizeActivityEvent } from "../activity";

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
  // 发现到、但未在模板 default_tools 白名单故未装配的动态 MCP 工具（非阻断，中性提示，区别于上方红色 blocked）
  "openops.tool.skipped": { icon: "info-circle", tone: "neutral", title: "工具未装配" },
  // 子 Agent 画像里配了 Skill、运行时却没装配上（key 已下架/改名——编辑器仍显示为已勾选）。
  // 同为非阻断中性提示：此前静默切空、零信号，管理员只能从「子 Agent 不调 Skill」的体感反推
  "openops.skill.skipped": { icon: "info-circle", tone: "neutral", title: "Skill 未装配" },
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
  // 假设 checkpoint（step=3 后弹卡等用户补充假设/继续）；双写裸名：审计行无 openops. 前缀
  "openops.diagnosis.checkpoint.opened": { icon: "bulb", tone: "warning", title: "等待补充假设" },
  "diagnosis.checkpoint.opened": { icon: "bulb", tone: "warning", title: "等待补充假设" },
  "openops.diagnosis.checkpoint.extended": { icon: "clock", tone: "neutral", title: "假设输入中" },
  "diagnosis.checkpoint.extended": { icon: "clock", tone: "neutral", title: "假设输入中" },
  "openops.diagnosis.checkpoint.closed": { icon: "circle-check", tone: "good", title: "假设确认完成" },
  "diagnosis.checkpoint.closed": { icon: "circle-check", tone: "good", title: "假设确认完成" },
  "openops.task.completed": { icon: "flag-check", tone: "good", title: "任务完成" },
  "openops.task.cancelled": { icon: "player-stop", tone: "warning", title: "任务取消" },
  "openops.task.failed": { icon: "alert-triangle", tone: "danger", title: "任务失败" },
  "openops.run.closed": { icon: "lock", tone: "neutral", title: "会话关闭" },
  "openops.model.selected": { icon: "cpu", tone: "neutral", title: "模型切换" },
  "model.selected": { icon: "cpu", tone: "neutral", title: "模型切换" },
  // 38 号：模型模板槽位降级（授权撤销/模板停用 → 该槽回退平台默认）。双写裸名：审计行无 openops. 前缀
  "openops.model.template_degraded": { icon: "alert-triangle", tone: "warning", title: "模型模板降级" },
  "model.template_degraded": { icon: "alert-triangle", tone: "warning", title: "模型模板降级" },
  // 自带模型降级（配置被删/禁用 → 回退平台默认）。与模板降级不同：这条还会在工作台弹横幅
  // 直接告知用户去重新选择（见 Workbench 的 openops.model.user_llm_degraded 分支）
  "openops.model.user_llm_degraded": { icon: "alert-triangle", tone: "warning", title: "自带模型降级" },
  "model.user_llm_degraded": { icon: "alert-triangle", tone: "warning", title: "自带模型降级" },
  "run.closed": { icon: "lock", tone: "neutral", title: "会话关闭" },
};

function meta(t: string) {
  return EVENT_META[t] ?? { icon: "info-circle", tone: "neutral" as Tone, title: t };
}

// 后端 occurred_at 是 tz-aware UTC ISO（…+00:00）——必须转本地时区显示。
// 曾用 iso.slice(11,16) 裸切 UTC 时分，CST 浏览器全部差 8 小时（17:13 显示 09:13）。
const hhmm = (iso?: string): string => {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime())
    ? iso.slice(11, 16)  // 非法串（如已是 "10:02" 字面量）回退原样
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
};

/** audit_event 行 → 活动节点。 */
export function auditToNode(e: Record<string, unknown>): ActivityNode {
  const t = String(e.event_type ?? "");
  const m = meta(t);
  const p = (e.payload_redacted_json ?? {}) as Record<string, unknown>;
  const normalized = normalizeActivityEvent(e, "audit");
  return {
    id: normalized?.eventId ?? String(e.audit_event_id ?? e.event_id ?? `${t}:${e.occurred_at ?? ""}`),
    title: m.title,
    tool: t,
    detail: normalized?.message
      ?? (String(p.summary ?? p.reason ?? e.reason_code ?? "") || String(e.action ?? "")),
    time: hhmm(String(e.occurred_at ?? "")),
    icon: m.icon,
    tone: m.tone,
    agentKey: normalized?.agentKey !== "main" ? normalized?.agentKey
      : (p as Record<string, unknown>).agent_key ? String((p as Record<string, unknown>).agent_key) : undefined,
  };
}

/** openops.* SSE 事件 → 活动节点。 */
export function eventToNode(e: OpenOpsEvent): ActivityNode {
  const m = meta(e.event_type);
  const ak = ((e.payload_redacted_json ?? {}) as Record<string, unknown>).agent_key;
  const normalized = normalizeActivityEvent(e, "live");
  return {
    id: normalized?.eventId ?? e.event_id,
    title: m.title,
    tool: e.event_type,
    detail: normalized?.message ?? e.message,
    time: hhmm(e.occurred_at),
    icon: m.icon,
    tone: m.tone,
    agentKey: normalized?.agentKey !== "main" ? normalized?.agentKey : ak ? String(ak) : undefined,
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

/** 审批入参键 → 中文标签（已知键友好化，未知键用原 key）。 */
const APPROVAL_ARG_LABELS: Record<string, string> = {
  command: "命令",
  target: "目标",
  appid: "目标 APPID",
  app_id: "目标 APPID",
  project_id: "目标 APPID",
  action: "动作",
  impact: "影响说明",
};

/** 审批卡事实行统一构建（SSE 实时与 /state 恢复同口径）：首行工具名 + 逐项具体入参。
 *  args 空时回退到"工具"单行——至少让用户看清在批哪个工具。 */
export function buildApprovalFacts(tool: string, args: Record<string, unknown>): HitlFact[] {
  const facts: HitlFact[] = [{ label: "工具", value: tool }];
  for (const [k, v] of Object.entries(args)) {
    if (k === "agent_key" || v === null || v === undefined || v === "") continue; // agent_key 由 emit 注入，非入参
    facts.push({ label: APPROVAL_ARG_LABELS[k] ?? k, value: String(v) });
  }
  return facts;
}

/** approval_request 行 → HITL 卡数据。 */
export function approvalToHitl(a: Record<string, unknown>): HitlCardData {
  const args = (a.arguments_redacted_json ?? {}) as Record<string, unknown>;
  const expire = a.expire_at ? new Date(String(a.expire_at)) : null;
  const remainMs = expire ? expire.getTime() - Date.now() : 0;
  const mm = Math.max(0, Math.floor(remainMs / 60000));
  const ss = Math.max(0, Math.floor((remainMs % 60000) / 1000));
  const facts = buildApprovalFacts(String(a.tool_call_name ?? "tool"), args);
  facts.push({ label: "任务", value: String(a.task_id ?? "—") });
  return {
    approval_request_id: String(a.approval_request_id),
    title: "需要人工批准",
    tool: String(a.tool_call_name ?? "tool"),
    // 按工具类型给摘要：Bash 命令≠恢复动作；非 bash 工具用中性话术（此前把一切写工具都叫「恢复动作」）
    summary: args.command
      ? "Agent 请求在你的容器内执行非只读命令，批准后才会真正执行。"
      : `Agent 请求调用需人工批准的工具「${String(a.tool_call_name ?? "tool")}」，批准后才会真正执行。`,
    facts,
    countdown: `${mm}:${String(ss).padStart(2, "0")}`,
    status: "pending",
    tone: "warning",
  };
}

/** 后端 rca payload（编排器产）→ RcaCardData（形状即为前端模型，补 time）。
 *  occurredAt 用**事件时刻**（rca.updated 的 occurred_at），缺省（/state 恢复）保留
 *  payload 自带 time，两者皆无才退回客户端时钟。 */
export function projectRca(
  p: Record<string, unknown> | null | undefined,
  occurredAt?: string,
): RcaCardData | undefined {
  const steps = (p as { steps?: unknown } | null | undefined)?.steps;
  if (!p || !Array.isArray(steps) || !steps.length) return undefined;
  const data = p as unknown as RcaCardData;
  return {
    ...data,
    time: (occurredAt ? hhmm(occurredAt) : data.time ?? hhmm(new Date().toISOString())) || undefined,
  };
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
    // 列表行无 overlay，投影层给不出真实模型——留空由 SettingsPage 补拉（resolveModelLabel）
    // 后兜底「平台提供」；此处硬编码假信息会盖住补拉结果。
    model: undefined,
  };
}
