import { Component, type ErrorInfo, type ReactNode } from "react";
import { color, font, radius } from "../../theme/tokens";
import { isChunkLoadError, shouldAutoReload } from "./chunkError";

interface State {
  error: Error | null;
  reloading: boolean;
}

const btnStyle = {
  padding: "8px 18px",
  borderRadius: radius.md,
  border: `1px solid ${color.borderInput}`,
  background: "#fff",
  cursor: "pointer",
  fontSize: 13,
  color: color.textBody,
} as const;

/** 全局白屏加固：任何未被内层边界（CopilotErrorBoundary）捕获的渲染错误 → 可读中文错误页。
 *  错误页不依赖 Router/AppProvider（崩溃时它们都在死掉的子树里），只用 tokens 常量 + 原生元素。
 *  chunk 加载失败（重新部署后旧 hash）自动整页 reload 一次（chunkError.ts 60s 节流防死循环）。 */
export class GlobalErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null, reloading: false };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[OpenOps][boot-guard] 全局渲染失败", error, info.componentStack);
    if (isChunkLoadError(error) && shouldAutoReload(window.sessionStorage, Date.now())) {
      this.setState({ reloading: true });
      window.location.reload();
    }
  }

  render(): ReactNode {
    const { error, reloading } = this.state;
    if (!error) return this.props.children;
    if (reloading) return null; // reload 已发起：空渲染占位，避免错误页闪现
    const chunk = isChunkLoadError(error);
    return (
      <div
        role="alert"
        data-testid="global-error-fallback"
        style={{
          height: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: color.pageBg,
          fontFamily: font.sans,
        }}
      >
        <div style={{ maxWidth: 480, padding: 24, textAlign: "center" }}>
          <div style={{ fontSize: 17, fontWeight: 700, color: color.textStrong }}>
            {chunk ? "页面资源加载失败" : "页面出错了"}
          </div>
          <div style={{ marginTop: 10, fontSize: 13, color: color.textMuted, lineHeight: 1.7 }}>
            {chunk
              ? "可能是系统刚发布了新版本，旧页面缓存已失效。请点击刷新重试。"
              : "页面遇到意外错误已停止渲染。请刷新重试；若反复出现，请截图下方信息联系平台管理员。"}
          </div>
          <div style={{ marginTop: 10, fontSize: 12, color: color.textSubtle, wordBreak: "break-all" }}>
            {`${error.name}: ${error.message}`.slice(0, 300)}
          </div>
          {!chunk && error.stack && (
            <details style={{ marginTop: 8, fontSize: 11, color: color.textFaint, textAlign: "left" }}>
              <summary style={{ cursor: "pointer" }}>技术详情</summary>
              <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", margin: "6px 0 0" }}>
                {error.stack.slice(0, 1500)}
              </pre>
            </details>
          )}
          <div style={{ marginTop: 16, display: "flex", gap: 10, justifyContent: "center" }}>
            <button type="button" onClick={() => window.location.reload()} style={btnStyle}>
              刷新页面
            </button>
            <button
              type="button"
              onClick={() => {
                window.location.href = import.meta.env.BASE_URL || "/";
              }}
              style={btnStyle}
            >
              返回首页
            </button>
          </div>
        </div>
      </div>
    );
  }
}
