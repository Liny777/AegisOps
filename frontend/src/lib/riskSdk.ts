/** 风控 SDK 接入（29.14 四号校验）。
 *
 * SDK 由 index.html 的 `<script src="/sdk/risk-control.js">` 全局加载（外部依赖，仓库不 vendor）；
 * 弹窗 UI 由 SDK 内部管理——本模块只做类型化访问与「未加载/初始化失败」的显式失败语义，
 * FlowCheckCard 据此走 rejected 决策，避免运行时干等到超时。
 *
 * initialization / flow-number-check 接口只配 URI 路径（标注里不含域名），完整 URL 由调用方用
 * window.location.origin 拼接后传入（29.15 的接口挂在与前端同域的网关下）。
 */

export interface RiskSdkInitParams {
  /** origin + init_path 拼好的完整 initialization URL。 */
  initUrl: string;
  serviceId: string;
  invokingMethod: string;
  /** IAM 用户 UUID（29.16 user-info 的 id），风控 initialization 的 operator。 */
  operator: string;
  enterpriseId: string;
  /** 操作对象信息（标注 object_arg_path 从工具入参提取；供 SDK 展示/对象校验）。 */
  targetObject?: unknown;
  targetObjectPath?: string;
}

export interface RiskSdkInstance {
  /** 拉起四号输入弹窗（SDK 内部 UI）。 */
  show(): void;
  /** 用户提交四号后校验（verifyUrl = origin + verify_path）；resolve 即校验通过。 */
  verify(verifyUrl: string): Promise<{ token: string; flowCode: string }>;
  destroy(): void;
}

declare global {
  interface Window {
    RiskControlSDK?: {
      initialize(params: RiskSdkInitParams): Promise<RiskSdkInstance>;
    };
  }
}

/** SDK 是否已随页面加载（脚本 404/被拦时为 false → 卡片显式报错并走拒绝）。 */
export function riskSdkAvailable(): boolean {
  return typeof window !== "undefined" && !!window.RiskControlSDK?.initialize;
}

export async function initRiskSdk(params: RiskSdkInitParams): Promise<RiskSdkInstance> {
  if (!riskSdkAvailable()) {
    throw new Error("风控 SDK 未加载（/sdk/risk-control.js）——请联系管理员确认部署");
  }
  return window.RiskControlSDK!.initialize(params);
}
