import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Streamdown } from "streamdown";

import { MermaidFullscreenBoundary } from "../../src/workbench/copilot/MermaidFullscreenBoundary";
import "../../src/workbench/copilot/CopilotChatPanel.css";

const chart = `\`\`\`mermaid
flowchart LR
  A[收到告警] --> B[并行查询指标]
  B --> C{根因确认}
  C -->|是| D[执行恢复]
  C -->|否| E[继续定界]
\`\`\``;

function Fixture() {
  return (
    <main style={{ boxSizing: "border-box", width: "100%", minHeight: "100vh", padding: 24 }}>
      <MermaidFullscreenBoundary>
        <div className="oa-chat-markdown" style={{ width: "min(760px, 100%)", margin: "0 auto", transform: "translateY(0)", overflow: "hidden" }}>
          {/* 与真实对话一致：关掉 Mermaid 左下缩放控件，只留右上全屏（见 OpenOpsChatMessageView） */}
          <Streamdown controls={{ mermaid: { panZoom: false } }}>{chart}</Streamdown>
        </div>
      </MermaidFullscreenBoundary>
    </main>
  );
}

document.body.style.margin = "0";
document.body.style.background = "#f7f8fa";
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Fixture />
  </StrictMode>,
);
