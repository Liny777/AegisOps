// 旁观直播 store：渲染性能收口。delta 是 token 级高频动作——通知经 rAF 合帧（一帧多条只
// notify 一次），且只有 ObserverLiveSlot 经 useSyncExternalStore 订阅，delta 不再触发
// Workbench/CopilotChat 整树重渲染（对比修复前：thinking.delta 每 token 一次 setNodes 全树渲染）。
import {
  initialObserverStream,
  observerStreamReducer,
  type ObserverStreamAction,
  type ObserverStreamState,
} from "./observerStream";

export interface ObserverStreamStore {
  getState(): ObserverStreamState;
  dispatch(a: ObserverStreamAction): void;
  subscribe(listener: () => void): () => void;
}

type Schedule = (cb: () => void) => void;

const defaultSchedule: Schedule = (cb) => {
  // 非浏览器环境（node:test）回退宏任务；两者语义等价：延后到当前批量 dispatch 之后
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => cb());
  else setTimeout(cb, 16);
};

export function createObserverStreamStore(schedule: Schedule = defaultSchedule): ObserverStreamStore {
  let state = initialObserverStream();
  let framePending = false;
  const listeners = new Set<() => void>();

  const notify = () => {
    for (const l of [...listeners]) l();
  };

  return {
    getState: () => state,
    dispatch(a) {
      const next = observerStreamReducer(state, a);
      if (next === state) return;
      state = next;
      if (a.type === "delta") {
        if (framePending) return;
        framePending = true;
        schedule(() => {
          framePending = false;
          notify();
        });
      } else {
        // 状态类转移（started/terminal/clear/freeze/resync）立即通知：UI 切换不等下一帧
        notify();
      }
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
  };
}
