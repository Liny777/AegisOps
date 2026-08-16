// 对话流内的接管交互挂点：armed 按钮 / 确认卡 / 结果卡。模块级稳定引用，
// copilot（OpenOpsChatMessageView children）与 fallback（Workbench 自建列表）两条路径共用。
import { useAlertTakeoverVm } from "./AlertTakeoverContext";
import { TakeoverConfirmCard } from "./TakeoverConfirmCard";
import { TakeoverResultCard } from "./TakeoverResultCard";
import "./AlertTakeover.css";

export function AlertTakeoverSlot() {
  const vm = useAlertTakeoverVm();
  if (!vm) return null;
  switch (vm.phase) {
    case "armed":
      return (
        <div className="oa-takeover-arm-row">
          <button
            type="button"
            className="oa-takeover-arm"
            data-testid="alert-takeover-button"
            onClick={vm.open}
          >
            设置告警接管
          </button>
        </div>
      );
    case "card_open":
    case "submitting":
    case "error":
      return <TakeoverConfirmCard vm={vm} />;
    case "done_created":
    case "done_merged":
    case "done_already":
    case "done_not_granted":
      return <TakeoverResultCard vm={vm} />;
    default:
      return null;
  }
}
