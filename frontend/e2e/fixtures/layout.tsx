import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { CopilotHitlFloat } from "../../src/workbench/copilot/CopilotHitlFloat";
import "../../src/workbench/copilot/CopilotChatPanel.css";

const longToken = "X".repeat(2000);
const longUrl = `https://internal.example.invalid/${"segment".repeat(260)}`;

const hitl = {
  approval_request_id: "layout-approval",
  title: `需要人工批准-${longToken}`,
  tool: `run_container_command_${longToken}`,
  summary: `即将执行长命令 ${longUrl}`,
  facts: [
    { label: "命令", value: `curl-${longUrl}-${longToken}` },
    { label: "目标", value: longToken },
  ],
  countdown: longToken,
  status: "pending" as const,
  tone: "warning" as const,
};

function Fixture() {
  return (
    <main className="copilot-chat-panel" style={{ boxSizing: "border-box", width: "100vw", height: "100vh", overflow: "hidden" }}>
      <div style={{ width: "min(760px, calc(100vw - 24px))", margin: "16px auto" }}>
        <section className="oa-tool-call-group">
          <button type="button" className="oa-tool-call-group-toggle">
            <span className="oa-tool-call-status" />
            <span className="oa-tool-call-group-label">工具调用</span>
            <span className="oa-tool-call-group-count">2 个 · 已完成 2/2</span>
            <span className="oa-tool-call-group-chevron">−</span>
          </button>
          <div className="oa-tool-call-group-body">
            <div>{longUrl}</div>
            <pre>{longToken}</pre>
          </div>
        </section>
      </div>

      <div className="oa-chat-input" style={{ position: "fixed", left: 12, right: 12, bottom: 12, width: "auto", padding: 8, background: "#fff", border: "1px solid #dfe2e8", borderRadius: 12 }}>
        <textarea
          className="oa-chat-textarea"
          aria-label="layout composer"
          defaultValue={longToken}
          rows={3}
          style={{ display: "block", padding: 8, border: 0, resize: "none" }}
        />
      </div>
      <CopilotHitlFloat hitl={hitl} onDecide={() => undefined} />
    </main>
  );
}

document.body.style.margin = "0";
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Fixture />
  </StrictMode>,
);
