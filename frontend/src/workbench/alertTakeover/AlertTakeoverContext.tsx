// TakeoverVm 经 Context 下发：copilot 路径的 Slot 位于 vendor messageView 的 render prop 内，
// props 传不进去。范式照 ChatPresetsProvider——禁 memo 组件工厂（会 mint 新组件类型致子树 remount）。
import { createContext, useContext, type ReactNode } from "react";

import type { TakeoverVm } from "./useAlertTakeover";

const AlertTakeoverContext = createContext<TakeoverVm | null>(null);

export function AlertTakeoverProvider({
  vm,
  enabled,
  children,
}: {
  vm: TakeoverVm | null | undefined;
  /** false = 向下传 null（copilot 历史恢复期/只读会话不闪现，与 presets 同口径）。 */
  enabled: boolean;
  children: ReactNode;
}) {
  return (
    <AlertTakeoverContext.Provider value={enabled ? vm ?? null : null}>
      {children}
    </AlertTakeoverContext.Provider>
  );
}

export function useAlertTakeoverVm(): TakeoverVm | null {
  return useContext(AlertTakeoverContext);
}
