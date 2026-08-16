/** 7x24 告警接管切片的展示常量与类别派生（AlertRulesPane / AlertsPage / mockData 共用，
 *  单一事实源——此前三处各抄一份，改文案必漂）。 */
import type { Tone } from "../theme/tokens";
import type { AlertRuleTemplatesPayload, AlertSeverity } from "./types";

export const SEVERITY_LABEL: Record<AlertSeverity, string> = { fatal: "致命", critical: "严重", warning: "普通", info: "提示" };
export const SEVERITY_TONE: Record<AlertSeverity, Tone> = { fatal: "danger", critical: "warning", warning: "neutral", info: "neutral" };
export const SEVERITY_ORDER: AlertSeverity[] = ["fatal", "critical", "warning", "info"];
/** payload 未到位时类型下拉的兜底档（清单页筛选亦用作固定档；开放枚举的其余类型走「全部」）。 */
export const FALLBACK_CATEGORIES = ["MySQL", "PostgreSQL", "Docker"];

/** rule-templates payload → 类别数组（templates 首现顺序去重；编辑器 chip 组与筛选下拉共用）。 */
export function deriveTemplateCategories(payload: Pick<AlertRuleTemplatesPayload, "templates">): string[] {
  const seen: string[] = [];
  for (const t of payload.templates) if (!seen.includes(t.category)) seen.push(t.category);
  return seen;
}
