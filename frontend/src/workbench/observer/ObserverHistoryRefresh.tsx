// 旁观直播的终局桥（无 UI，挂 CopilotChatPanel 的 CopilotKit Provider 内）：
// ① store 进入 finalizing 且本端无活流 → 一次 copilotkit.connectAgent 静默刷入真实历史
//   （task.completed 前后端已 _persist_state，sidecar MISS 路径拉到完整 transcript，
//    同 threadId isFreshRestore=false 编辑式合并、不清屏不闪）→ 成功 clear / 失败 freeze
//   （冻结泡保留不重试——刷新失败时丢直播泡等于两头空）。
//   ⚠ vendor RunHandler.connectAgent **从不 reject**（内部 catch 全部错误后 resolve
//   {result: undefined, newMessages: []}），成败只能按 agent.messages 是否非空判定——
//   已完成任务的 transcript 至少含用户输入一条，空即失败。
// ② 顺带上报本端 CopilotKit 回合状态（agent.isRunning）给 Workbench 的旁观守卫：
//    copilot 路径本端发送不写 aguiActive，没有这个信号本端回合的 SSE delta 会混入直播 store。
//    ⚠ 本组件自己发起的 connectAgent 也会把 isRunning 置 true——inFlight 期间不上报，
//    否则「刷新连接」被误判成本端回合，Workbench 会把 finalizing 中的 store 提前清掉。
import { useEffect, useRef } from "react";
import { UseAgentUpdate, useAgent, useCopilotKit } from "@copilotkit/react-core/v2";

import type { ObserverStreamStore } from "./observerStore";

export function ObserverHistoryRefresh({
  store,
  agentId,
  onLocalRunChange,
}: {
  store: ObserverStreamStore;
  agentId: string;
  onLocalRunChange?: (running: boolean) => void;
}) {
  const { agent } = useAgent({ agentId, updates: [UseAgentUpdate.OnRunStatusChanged] });
  const { copilotkit } = useCopilotKit();
  const inFlight = useRef(false);

  useEffect(() => {
    if (!inFlight.current) onLocalRunChange?.(agent.isRunning === true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent, agent.isRunning, onLocalRunChange]);

  // deps 含 agent.isRunning：finalizing 时若本端恰有活流（打开页面恰逢任务收尾的竞态），
  // 不放弃刷新——等 isRunning 翻 false 本 effect 重跑再补一次 check。
  useEffect(() => {
    const check = () => {
      const s = store.getState();
      if (s.phase !== "finalizing" || inFlight.current) return;
      if (agent.isRunning) return; // 等活流收尾，isRunning 翻转会重进本 effect
      inFlight.current = true;
      void Promise.resolve(copilotkit.connectAgent({ agent }))
        .then(() => {
          // connectAgent 从不 reject：按合并后的消息面判成败（详见文件头注 ①）
          const gotHistory = (agent.messages?.length ?? 0) > 0;
          store.dispatch({ type: gotHistory ? "clear" : "freeze" });
        })
        .catch(() => store.dispatch({ type: "freeze" })) // 防御未来 vendor 语义变化
        .finally(() => {
          inFlight.current = false;
        });
    };
    check(); // finalizing 可能在本组件挂载前已进入（切会话回来）
    return store.subscribe(check);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store, agent, agent.isRunning, copilotkit]);

  return null;
}
