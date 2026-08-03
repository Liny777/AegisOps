import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { CapabilityCard, LlmCard, ModelTemplateCard, WorkspaceCard } from "../../src/init/InitWizard";
import type { ModelTemplateOption, Workspace } from "../../src/lib/api/types";

// 极长且大部分不可断的自由文本 name（无空格、CJK 内嵌超长 Latin run）——模拟管理员/用户填入的超长
// 能力 label / 模型名 / workspace 名，这是「撑破卡片框、页面横向溢出」的最坏输入。加固后应：标题走
// 省略号截断（overflow:hidden + textOverflow:ellipsis + minWidth:0），任何元素都不越出视口。
const longRun = "X".repeat(200);
const longCap = `支付核心可观测性-感知快恢能力-${longRun}`;
const longModel = `deepseek-ai/DeepSeek-V2.5-preview-${longRun}`;
const longCustomModel = `custom-org/Very-Long-Custom-Model-${longRun}`;
const longBaseUrl = `https://llm.internal.example.invalid/v1/${"seg".repeat(200)}`;
const longWsName = `payment-core-observability-quick-recovery-workspace-${longRun}`;

const ws: Workspace = {
  workspace_id: `ws-${"0".repeat(120)}`,
  name: longWsName,
  scope_revision: "r1",
  sync_status: "ready",
  updated: "2026-07-16T00:00:00Z",
};

// 模型模板卡（38 号）：超长模板名 + 超长主/子槽位模型名——标题带 data-testid="llm-label"，
// 自动纳入既有宽度断言；槽位行同样省略号截断
const longTpl: ModelTemplateOption = {
  model_template_id: "mtpl-overflow",
  display_name: `均衡推荐-效果优先组合-${longRun}`,
  main_model: { model_id: "m1", display_name: `deepseek-ai/DeepSeek-V2.5-preview-${longRun}` },
  sub_model: { model_id: "m2", display_name: `qwen/Qwen3.5-Instruct-Long-${longRun}` },
  is_default: true,
  status: "active",
};

function Fixture() {
  return (
    <main style={{ boxSizing: "border-box", width: "100%", minHeight: "100vh", padding: 24, background: "#f7f8fa" }}>
      {/* 能力识别卡：复刻 StepCapabilities 的 grid 容器（minmax(200px,1fr)） */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
        <CapabilityCard name={longCap} />
        <CapabilityCard name="定界" />
      </div>
      {/* 模型卡：复刻 StepConfigure 的 column 列表。第二张为自定义卡——带 extra(超长 baseUrl)+trailing，
          复刻 InitWizard 自定义模型分支，回归「名字容器不被 extra 挤成 0 宽」。 */}
      <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 8 }}>
        <ModelTemplateCard tpl={longTpl} selected onClick={() => undefined} />
        <LlmCard selected={false} onClick={() => undefined} icon="sparkles" iconBg="#eef2ff" iconColor="#3661ff" label={longModel} badge="平台提供" />
        <LlmCard selected onClick={() => undefined} icon="cpu" iconBg="#eef2ff" iconColor="#333" label={longCustomModel} badge="可用" badgeTone="good"
          extra={<div style={{ fontSize: 11, color: "#8a8f98", marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", fontFamily: "ui-monospace, monospace" }}>{longBaseUrl}</div>}
          trailing={<span style={{ flexShrink: 0 }}>🗑</span>} />
      </div>
      {/* workspace 卡：复刻 StepConfigure 的 grid 容器（minmax(220px,1fr)） */}
      <div style={{ marginTop: 16, display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 12 }}>
        <WorkspaceCard ws={ws} on onPick={() => undefined} />
      </div>
    </main>
  );
}

document.body.style.margin = "0";
document.body.style.background = "#f7f8fa";
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Fixture />
  </StrictMode>,
);
