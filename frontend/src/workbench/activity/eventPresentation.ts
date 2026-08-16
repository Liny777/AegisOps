import type { ActivityEvent } from "../../lib/api/types";
import type { Tone } from "../../theme/tokens";

// 装配缺口信号：tool.skipped（MCP 未进白名单）/ skill.skipped（画像 Skill 未装配）
// 都是给管理员看的运维诊断事件，不该糊进面向用户的工作台活动栏。
const TECHNICAL_ONLY = /^openops\.(tool|skill)\.skipped$/i;

/** 装配缺口类运维诊断事件：工作台活动栏一律不展示（业务时间线与「全部动态」已下线，
 * 此判定仍是历史事件的过滤口径），只留审计回放页（getAuditNodes → run 审计页）与
 * 审计表 payload 供管理员排障。 */
export function isOpsDiagnosticEvent(event: ActivityEvent): boolean {
  return TECHNICAL_ONLY.test(event.eventType);
}

export function eventTitle(event: ActivityEvent): string {
  if (event.displayLabel) return event.displayLabel;
  const type = event.eventType.replace(/^openops\./, "");
  const labels: Array<[RegExp, string]> = [
    [/subagent\.dispatched/, "已派发调查任务"],
    [/subagent\.started/, "开始执行"],
    [/subagent\.reported/, "已向主控汇报"],
    [/subagent\.failed/, "执行异常"],
    [/subagent\.timeout/, "执行超时"],
    [/subagent\.cancelled/, "任务已取消"],
    [/approval\.required/, "等待人工审批"],
    [/approval\.(approved|resolved)/, "审批已通过"],
    [/approval\.rejected/, "审批被拒绝"],
    // 假设 checkpoint（诊断 step=3 后弹卡）：不登记会回退成 action/原始串
    [/diagnosis\.checkpoint\.opened/, "等待补充假设"],
    [/diagnosis\.checkpoint\.extended/, "假设输入中"],
    [/diagnosis\.checkpoint\.closed/, "假设确认完成"],
    // 两条装配缺口信号放在 tool.*/skill.* 之前：否则匹配不到会回退成 action，
    // 技术轨里显示为 mcp_not_whitelisted / skill_not_assembled 这类原始串
    [/tool\.skipped/, "工具未装配"],
    [/skill\.skipped/, "Skill 未装配"],
    [/tool(?:\.call)?\.(started|called)|tool\.call$/, "调用工具"],
    [/tool(?:\.call)?\.(completed|result)/, "工具返回结果"],
    [/tool(?:\.call)?\.failed/, "工具调用失败"],
    [/skill\.(started|called)/, "执行 Skill"],
    [/skill\.completed/, "Skill 执行完成"],
    [/sandbox/, "沙箱执行"],
    // 模板降级须在 /model/ 通配之前：否则命中「模型处理」，警示语义全丢（38 号）
    [/model\.template_degraded/, "模型模板降级"],
    [/model/, "模型处理"],
  ];
  return labels.find(([pattern]) => pattern.test(type))?.[1] ?? event.action ?? type.replace(/[._]/g, " ");
}

export function eventIcon(event: ActivityEvent): string {
  const type = event.eventType.toLowerCase();
  if (type.includes("template_degraded")) return "alert-triangle";
  if (type.includes("failed")) return "alert-triangle";
  if (type.includes("timeout")) return "clock-exclamation";
  if (type.includes("cancelled")) return "ban";
  if (type.includes("checkpoint")) return "bulb";
  if (type.includes("approval")) return "hourglass";
  if (type.includes("reported")) return "message-check";
  if (type.includes("dispatched")) return "send";
  if (type.includes("subagent.started")) return "player-play";
  if (type.includes("tool")) return "tool";
  if (type.includes("skill")) return "wand";
  if (type.includes("sandbox")) return "terminal-2";
  if (type.includes("model")) return "sparkles";
  return "activity";
}

export function eventTone(event: ActivityEvent): Tone {
  const value = `${event.eventType} ${event.severity}`.toLowerCase();
  if (/(failed|error|timeout|critical)/.test(value)) return "danger";
  if (/(running|started|required|warning|degraded)/.test(value)) return "warning";
  if (/(completed|reported|approved|success)/.test(value)) return "good";
  return "neutral";
}

function safeScalar(payload: Readonly<Record<string, unknown>>, keys: string[]): string | undefined {
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" || typeof value === "boolean") return String(value);
  }
  return undefined;
}

export function eventTool(event: ActivityEvent): string | undefined {
  return safeScalar(event.redactedPayload, ["tool_label", "tool", "tool_name", "skill_label", "skill"]);
}

export function eventSummary(event: ActivityEvent): string {
  return (
    safeScalar(event.redactedPayload, [
      "report_summary",
      "summary",
      "result_summary",
      "argument_summary",
      "error_summary",
      "reason_summary",
      "command_summary",
      "stdout_summary",
      "stderr_summary",
      "output_summary",
      "task_summary",
    ]) ?? event.message
  );
}

export function technicalMeta(event: ActivityEvent): Array<{ label: string; value: string }> {
  const candidates: Array<[string, string[]]> = [
    ["原因", ["reason_code"]],
    ["请求", ["request_id", "tool_call_id"]],
    ["执行", ["execution_id"]],
    // 38 号模板降级键（redact 白名单已放行）：不加提取行则 payload 过了脱敏也不渲染
    ["模板槽位", ["slot"]],
    ["模型模板", ["model_template_id"]],
    ["Trace", ["audit_trace_id", "trace_id"]],
  ];
  const rows = candidates.flatMap(([label, keys]) => {
    const value = safeScalar(event.redactedPayload, keys);
    return value ? [{ label, value }] : [];
  });
  if (event.reasonCode && !rows.some((row) => row.label === "原因")) {
    rows.unshift({ label: "原因", value: event.reasonCode });
  }
  if (event.auditTraceId && !rows.some((row) => row.label === "Trace")) {
    rows.push({ label: "Trace", value: event.auditTraceId });
  }
  if (event.externalRequestId && !rows.some((row) => row.label === "请求")) {
    rows.splice(rows.some((row) => row.label === "原因") ? 1 : 0, 0, {
      label: "请求",
      value: event.externalRequestId,
    });
  }
  return rows;
}

export function compareEvents(a: ActivityEvent, b: ActivityEvent): number {
  const ta = Date.parse(a.occurredAt);
  const tb = Date.parse(b.occurredAt);
  if (!Number.isNaN(ta) && !Number.isNaN(tb) && ta !== tb) return ta - tb;
  return (a.sequence ?? 0) - (b.sequence ?? 0);
}
