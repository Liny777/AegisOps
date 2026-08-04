import assert from "node:assert/strict";
import { test } from "node:test";
import { resolveModelLabel } from "./modelLabel";
import type { ModelOption, ModelTemplateOption } from "../lib/api/types";

const MODELS: ModelOption[] = [
  { llm_config_id: "platform:glm-5.1", label: "GLM-5.1", note: "", current: true, available: true },
  { llm_config_id: "platform:qwen3.5-instruct", label: "Qwen3.5", note: "", available: true },
  { llm_config_id: "11111111-1111-1111-1111-111111111111", label: "我的 GPT-4o", note: "", available: true },
];

const TEMPLATES: ModelTemplateOption[] = [
  { model_template_id: "mtpl_1", display_name: "均衡（推荐）", access_scope: "all",
    is_default: true, status: "active",
    main_model: { model_id: "glm-5.1", display_name: "GLM-5.1" },
    sub_model: { model_id: "qwen3.5-instruct", display_name: "Qwen3.5" } },
];

test("custom BYO 优先且反查展示名", () => {
  assert.equal(
    resolveModelLabel({ user_llm_config_id: "11111111-1111-1111-1111-111111111111" }, MODELS, TEMPLATES),
    "我的 GPT-4o");
  assert.equal(resolveModelLabel({ user_llm_config_id: "unknown-id" }, MODELS), "自定义模型");
});

test("model_template_id 命中 → 「模板名（主 x · 子 y）」", () => {
  assert.equal(resolveModelLabel({ model_template_id: "mtpl_1" }, MODELS, TEMPLATES),
    "均衡（推荐）（主 GLM-5.1 · 子 Qwen3.5）");
});

test("模板缺失（被停用/撤销授权/清单拉取失败）→ 兜底文案", () => {
  assert.equal(resolveModelLabel({ model_template_id: "mtpl_gone" }, MODELS, TEMPLATES),
    "模型模板（已停用或不可见）");
  // 第三参缺省 = 向后兼容旧调用方：模板绑定统一走兜底
  assert.equal(resolveModelLabel({ model_template_id: "mtpl_1" }, MODELS),
    "模型模板（已停用或不可见）");
});

test("BYO 与模板同现时 BYO 优先（互斥兜底，与后端解析同序）", () => {
  assert.equal(
    resolveModelLabel({ user_llm_config_id: "11111111-1111-1111-1111-111111111111", model_template_id: "mtpl_1" },
      MODELS, TEMPLATES),
    "我的 GPT-4o");
});

test("legacy platform_model_id 反查平台展示名，缺失回退裸 id", () => {
  assert.equal(resolveModelLabel({ platform_model_id: "qwen3.5-instruct" }, MODELS, TEMPLATES), "Qwen3.5");
  assert.equal(resolveModelLabel({ platform_model_id: "gone-model" }, MODELS), "gone-model");
});

test("全空 → 平台默认（current 行）", () => {
  assert.equal(resolveModelLabel({}, MODELS, TEMPLATES), "GLM-5.1");
  assert.equal(resolveModelLabel(undefined, [], []), "");
});
