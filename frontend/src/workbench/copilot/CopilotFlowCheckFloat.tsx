// 四号校验卡 · CopilotChat 路径浮层——锚在 composer 正上方（同 CopilotHitlFloat 机制）。
// 卡是强交互动作项（等用户完成风控校验），与审批卡同样贴近输入焦点；SDK 弹窗本体由风控 SDK 管理。
import { FlowCheckCard } from "../FlowCheckCard";
import { ComposerAnchoredFloat } from "./ComposerFloat";
import type { FlowCheckCardData } from "../../lib/api/types";

export function CopilotFlowCheckFloat({ flowCheck, onDecided }: {
  flowCheck: FlowCheckCardData | undefined;
  onDecided: (d: "approved" | "rejected" | "timeout") => void;
}) {
  return (
    <ComposerAnchoredFloat active={Boolean(flowCheck)} className="oa-hitl-float" zIndex={1000}>
      {flowCheck ? (
        <FlowCheckCard
          key={flowCheck.flow_check_request_id + flowCheck.status}
          data={flowCheck}
          onDecided={onDecided}
        />
      ) : null}
    </ComposerAnchoredFloat>
  );
}
