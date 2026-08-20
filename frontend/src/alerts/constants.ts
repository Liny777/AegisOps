/** 7x24 告警接管切片的展示常量与类别派生（AlertRulesPane / AlertsPage / mockData 共用，
 *  单一事实源——此前三处各抄一份，改文案必漂）。 */
import type { Tone } from "../theme/tokens";
import type { AlertRuleTemplatesPayload, AlertSeverity } from "./types";

export const SEVERITY_LABEL: Record<AlertSeverity, string> = { fatal: "致命", critical: "严重", warning: "普通", info: "提示" };
export const SEVERITY_TONE: Record<AlertSeverity, Tone> = { fatal: "danger", critical: "warning", warning: "neutral", info: "neutral" };
export const SEVERITY_ORDER: AlertSeverity[] = ["fatal", "critical", "warning", "info"];
/** payload 未到位时类型下拉的兜底档（清单页筛选亦用作固定档；开放枚举的其余类型走「全部」）。 */
export const FALLBACK_CATEGORIES = ["MySQL", "PostgreSQL", "Docker"];

/** 失败/放弃原因 → 用户话术（与后端 service._SKIP_REASON_TEXT 同源口径；未知码由调用方回落原始 code）。 */
export const STATE_REASON_TEXT: Record<string, string> = {
  timeout: "诊断超时被取消",
  task_failed: "诊断执行失败（模型或工具调用异常，详见会话活动栏）",
  task_cancelled: "诊断任务被取消",
  queue_expired: "排队超时未接管（超过配置时限自动放弃，可手动重试）",
  rule_revoked: "命中的告警策略已被停用或删除，本单不再自动诊断（留痕）",
  no_scope: "无可用范围快照（实例从未成功解析过 scope）",
  stale: "重启后发现告警已陈旧，不再补诊",
  interrupted_by_restart: "服务重启中断且超出重试预算",
  SANDBOX_CAPACITY_FULL: "沙箱容量已满，起诊断失败（超出重试预算）",
  SANDBOX_CONTAINER_FAILED: "沙箱容器启动失败，起诊断失败（超出重试预算）",
};

/** rule-templates payload → 类别数组（templates 首现顺序去重；编辑器 chip 组与筛选下拉共用）。 */
export function deriveTemplateCategories(payload: Pick<AlertRuleTemplatesPayload, "templates">): string[] {
  const seen: string[] = [];
  for (const t of payload.templates) if (!seen.includes(t.category)) seen.push(t.category);
  return seen;
}
