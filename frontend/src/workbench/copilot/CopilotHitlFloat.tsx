// 审批卡（HITL/ASK）· CopilotChat 路径浮层——锚在 composer 正上方（操作点附近）。
//
// 此前审批卡与 RCA 同在面板**顶部**横条：审批是"等你按按钮"的强交互动作项，出现在
// 对话区顶部远离输入焦点，还把消息流往下压（用户实测反馈）。改为 SkillSlash 同款
// DOM 锚定：rAF 测 `.copilot-chat-panel textarea` rect，CopilotChat 重挂/输入框长高
// 时自动跟随；不碰 CopilotChat 内部（input slot 方案在外部状态注入时组件引用不稳，
// 会整段重建输入框丢焦点）。RCA 决策面板保持顶部横条（分析上下文，非动作项）。
// 锚定/portal 机制提取到 ComposerAnchoredFloat（与假设 checkpoint 卡共用）。
import { HitlCard } from "../HitlCard";
import { ComposerAnchoredFloat } from "./ComposerFloat";
import type { HitlCardData } from "../../lib/api/types";

export function CopilotHitlFloat({ hitl, onDecide }: {
  hitl: HitlCardData | undefined;
  onDecide: (d: "approved" | "rejected") => void;
}) {
  return (
    <ComposerAnchoredFloat active={Boolean(hitl)} className="oa-hitl-float" zIndex={1000}>
      {hitl ? <HitlCard key={hitl.approval_request_id + hitl.status} hitl={hitl} onDecide={onDecide} /> : null}
    </ComposerAnchoredFloat>
  );
}
