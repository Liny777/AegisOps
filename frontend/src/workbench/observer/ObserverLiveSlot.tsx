// 旁观直播渲染（对话流内）：告警诊断由 dispatcher 无头驱动时，旁观者的 CopilotChat 消息列表
// 是空的（sidecar MISS 快照即终止）——本 Slot 把 SSE delta 累积的直播泡渲染在 messageElements
// 之后。终局由 ObserverHistoryRefresh 刷入真实历史后 clear，本 Slot 自然撤下。
// 模块级稳定引用（禁工厂）；只有本组件订阅 store——delta 高频更新不波及整树。
import { useSyncExternalStore } from "react";
import {
  CopilotChatAssistantMessage,
  CopilotChatMessageView,
  CopilotChatUserMessage,
} from "@copilotkit/react-core/v2";
import type { AssistantMessage, UserMessage } from "@ag-ui/core";

import { useObserverLiveVm, type ObserverLiveVm } from "./ObserverLiveContext";
import "./ObserverLive.css";

const noToolbar = () => null;

function ObserverLiveInner({ vm, localRunActive }: { vm: ObserverLiveVm; localRunActive: boolean }) {
  const s = useSyncExternalStore(vm.store.subscribe, vm.store.getState);
  // 第二道门（防御 copilotLocalRun 信号的时序竞态）：本端 CopilotKit 活流期间
  // CopilotChat 自己在播 TEXT_MESSAGE，直播泡绝不与之并排。isRunning 来自唯一挂点
  // OpenOpsChatMessageView 的 render prop——不在此重复开 useAgent 订阅。
  if (localRunActive) return null;
  if (s.phase === "idle") return null;
  if (s.order.length === 0 && !vm.inputText) return null;
  return (
    <div data-testid="observer-live" className="oa-observer-live">
      {vm.inputText ? (
        <CopilotChatUserMessage
          message={{ id: "observer-user", role: "user", content: vm.inputText } as UserMessage}
        />
      ) : null}
      {s.resyncGap ? (
        <div className="oa-observer-gap">连接曾中断，以下直播内容可能缺段——诊断结束后将自动补全全文。</div>
      ) : null}
      {s.order.map((id) => {
        const bubble = s.bubbles[id];
        if (!bubble) return null;
        return (
          <CopilotChatAssistantMessage
            key={id}
            message={{
              id: `observer-${id}`,
              role: "assistant",
              content: bubble.truncated ? `${bubble.text}\n\n…（直播内容过长已截断，结束后可见全文）` : bubble.text,
            } as AssistantMessage}
            toolbar={noToolbar}
          />
        );
      })}
      {s.phase === "live" ? <CopilotChatMessageView.Cursor /> : null}
    </div>
  );
}

export function ObserverLiveSlot({ localRunActive = false }: { localRunActive?: boolean }) {
  const vm = useObserverLiveVm();
  if (!vm) return null;
  return <ObserverLiveInner vm={vm} localRunActive={localRunActive} />;
}
