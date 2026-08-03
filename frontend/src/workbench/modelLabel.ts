import type { ModelOption, ModelTemplateOption } from "../lib/api/types";

/**
 * 实例默认模型绑定（active 配置 overlay）→ 人类可读模型名。
 * 与 InitWizard 编辑态预填同口径（互斥优先级）：
 * - user_llm_config_id(UUID) = 自带 LLM（主=子）；
 * - model_template_id = 模型模板（38 号：主/子 Agent 槽位）→「模板名（主 x · 子 y）」；
 * - platform_model_id(裸 model_id) = 平台模型 legacy；三者皆空 = 平台默认。
 * models 来自 api.getModelConfigs()（平台 `platform:<id>` + 自定义 UUID，含 label/current）；
 * templates 来自 api.getModelTemplates()（可选参：拉取失败传 [] 走「已停用或不可见」兜底）。
 */
export function resolveModelLabel(
  overlay: Record<string, unknown> | undefined,
  models: ModelOption[],
  templates: ModelTemplateOption[] = [],
): string {
  const o = overlay ?? {};
  const llmId = typeof o.user_llm_config_id === "string" ? o.user_llm_config_id : "";
  const tplId = typeof o.model_template_id === "string" ? o.model_template_id : "";
  const platformId = typeof o.platform_model_id === "string" ? o.platform_model_id : "";
  if (llmId) return models.find((m) => m.llm_config_id === llmId)?.label ?? "自定义模型";
  if (tplId) {
    const t = templates.find((x) => x.model_template_id === tplId);
    // real 模式用户列表只回 active+已授权行——被停用/撤销授权的绑定命中不了，统一走兜底文案
    if (!t) return "模型模板（已停用或不可见）";
    return `${t.display_name}（主 ${t.main_model.display_name} · 子 ${t.sub_model.display_name}）`;
  }
  if (platformId) return models.find((m) => m.llm_config_id === "platform:" + platformId)?.label ?? platformId;
  return models.find((m) => m.current)?.label ?? "";
}
