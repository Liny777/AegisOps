import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  resetKey: string;
}

interface State {
  error: Error | null;
}

/** Keep a CopilotKit render failure inside the conversation pane. */
export class CopilotErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[OpenOps][CopilotKit] 对话区渲染失败", error, info.componentStack);
  }

  componentDidUpdate(previous: Props): void {
    if (this.state.error && previous.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <div
        role="alert"
        data-testid="copilot-error-fallback"
        style={{
          flex: 1,
          minHeight: 0,
          display: "grid",
          placeItems: "center",
          padding: 24,
          background: "#f7f8fa",
        }}
      >
        <div style={{ maxWidth: 520, padding: 20, border: "1px solid #e3e6eb", borderRadius: 12, background: "#fff" }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: "#222936" }}>对话组件暂时无法显示</div>
          <div style={{ marginTop: 8, fontSize: 12.5, lineHeight: 1.6, color: "#596273" }}>
            当前页面仍可继续使用。请重试对话区；若问题持续，可切换到其他历史会话再返回。
          </div>
          <button
            type="button"
            onClick={() => this.setState({ error: null })}
            style={{ marginTop: 14, border: "1px solid #ccd3df", borderRadius: 7, background: "#fff", padding: "6px 12px", cursor: "pointer" }}
          >
            重试对话区
          </button>
        </div>
      </div>
    );
  }
}
