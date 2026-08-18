// ObserverLiveVm 经 Context 下发：copilot 路径的 Slot 位于 vendor messageView 的 render prop 内，
// props 传不进去。范式照 AlertTakeoverContext——禁 memo 组件工厂（会 mint 新组件类型致子树 remount）。
import { createContext, useContext, type ReactNode } from "react";

import type { ObserverStreamStore } from "./observerStore";

export interface ObserverLiveVm {
  store: ObserverStreamStore;
  /** 旁观者用户气泡文案（/state.active_task.input_text）；task.started payload 无原文，靠 /state 补拉。 */
  inputText: string | null;
  /** CopilotKit agent id（CopilotChatPanel 的 AGENT_ID）——经 context 下发防 Slot 反向 import 成环。 */
  agentId: string;
}

const ObserverLiveContext = createContext<ObserverLiveVm | null>(null);

export function ObserverLiveProvider({
  vm,
  enabled,
  children,
}: {
  vm: ObserverLiveVm | null | undefined;
  /** false = 向下传 null（copilot 历史恢复期/只读会话不闪现，与 presets/takeover 同口径）。 */
  enabled: boolean;
  children: ReactNode;
}) {
  return (
    <ObserverLiveContext.Provider value={enabled ? vm ?? null : null}>
      {children}
    </ObserverLiveContext.Provider>
  );
}

export function useObserverLiveVm(): ObserverLiveVm | null {
  return useContext(ObserverLiveContext);
}
