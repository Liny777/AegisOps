// 假设 checkpoint 卡 · CopilotChat 路径浮层——与审批卡同款 composer 锚定（决策动作项
// 出现在输入焦点附近）。zIndex 低于审批卡：两卡理论上不同相（step 3 vs 恢复期），
// 万一并存时审批（安全动作）压过 checkpoint（分析补充）。
import { HypothesisCheckpointCard } from "../HypothesisCheckpointCard";
import { ComposerAnchoredFloat } from "./ComposerFloat";
import type { RcaCardData } from "../../lib/api/types";
import type { CheckpointCardState } from "../../lib/checkpoint/model";

export function CopilotCheckpointFloat({ checkpoint, rca, onDecide }: {
  checkpoint: CheckpointCardState | undefined;
  rca?: RcaCardData;
  onDecide: (action: "continue" | "add_hypothesis" | "hold", text?: string) => void;
}) {
  return (
    <ComposerAnchoredFloat active={Boolean(checkpoint)} className="oa-checkpoint-float" zIndex={999}>
      {checkpoint ? (
        // key 只含 id 不含 status：结果态翻转就地重渲染（composing 输入框状态无需保留——
        // 已定格结果态本就该收起输入框）；换卡（新 id）才重挂重置本地态
        <HypothesisCheckpointCard key={checkpoint.checkpointId} checkpoint={checkpoint} rca={rca} onDecide={onDecide} />
      ) : null}
    </ComposerAnchoredFloat>
  );
}
