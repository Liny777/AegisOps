import type { ModelOption } from "../lib/api/types";

/**
 * 实例默认模型绑定（active 配置 overlay）→ 人类可读模型名。
 * 与 InitWizard 编辑态预填同口径：
 * - user_llm_config_id(UUID) = 自带 LLM；platform_model_id(裸 model_id) = 平台模型；二者皆空 = 平台默认。
 * models 来自 api.getModelConfigs()（平台 `platform:<id>` + 自定义 UUID，含 label/current）。
 */
export function resolveModelLabel(
  overlay: Record<string, unknown> | undefined,
  models: ModelOption[],
): string {
  const o = overlay ?? {};
  const llmId = typeof o.user_llm_config_id === "string" ? o.user_llm_config_id : "";
  const platformId = typeof o.platform_model_id === "string" ? o.platform_model_id : "";
  if (llmId) return models.find((m) => m.llm_config_id === llmId)?.label ?? "自定义模型";
  if (platformId) return models.find((m) => m.llm_config_id === "platform:" + platformId)?.label ?? platformId;
  return models.find((m) => m.current)?.label ?? "";
}
