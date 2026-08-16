// alerts/api 的动态 import() 单飞门面：alerts/api 顶层引 mockData（~800 行样本数据），
// 静态 import 会把它拖进 Workbench 所在主 chunk；这里按需加载且只对告警直达会话触发
//（ctx 为空的普通会话零请求零字节）。模块级 promise 缓存单飞，失败清缓存以便重试。
import type { AlertsApi } from "../../alerts/api";

export type TakeoverApi = Pick<AlertsApi, "ensureRule" | "getRuleTemplates" | "getAccess">;

let cached: Promise<TakeoverApi> | null = null;

export function loadTakeoverApi(): Promise<TakeoverApi> {
  cached ??= import("../../alerts/api").then(
    (m) => m.alertsApi,
    (err) => {
      cached = null;
      throw err;
    },
  );
  return cached;
}
