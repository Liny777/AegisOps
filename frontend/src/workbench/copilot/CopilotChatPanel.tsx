// CopilotKit 官方 UI 接管对话区（Part B，用户拍板：整体换 <CopilotChat>）。
//
// 链路：<CopilotKit runtimeUrl=/api/copilotkit>（vite proxy → sidecar :4002，
// `npm run runtime` 启动）→ CopilotRuntime → HttpAgent → FastAPI per-run /agui。
// threadId=runId（sidecar 按它重写目标 URL）；身份经 headers 透传（mock IAM 两头）。
// 发送按钮两态/停止（原生）：运行中变停止 → abort 流 → 后端断流取消桥停任务。
// RCA/HITL 卡与活动栏不进本组件——仍由 Workbench 的 SSE 通道驱动（30.4 三层模型不变）。
import { CopilotChat, CopilotKit } from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";

import { demoIdentity } from "../../lib/api";
import { CopilotModelFloat } from "./CopilotModelFloat";
import { CopilotSkillSlash } from "./CopilotSkillSlash";

const AGENT_ID = "sre-agent";

function identityHeaders(): Record<string, string> {
  return {
    "X-OpenOps-Mock-User": demoIdentity.user,
    "X-OpenOps-Mock-Name": encodeURIComponent(demoIdentity.name),
  };
}

export function CopilotChatPanel({ runId, instanceId }: { runId: string; instanceId: string }) {
  return (
    <CopilotKit
      key={runId}
      runtimeUrl="/api/copilotkit"
      agent={AGENT_ID}
      headers={identityHeaders}
      onError={(event) => console.error("[CopilotKit]", event)}
    >
      <div style={{ position: "relative", flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
        <div style={{ position: "absolute", top: 8, right: 16, zIndex: 5 }}>
          <CopilotModelFloat runId={runId} />
        </div>
        <CopilotChat
          agentId={AGENT_ID}
          threadId={runId}
          autoScroll="pin-to-bottom"
          className="copilot-chat-panel"
          welcomeScreen={false}
        />
        <CopilotSkillSlash instanceId={instanceId} />
      </div>
    </CopilotKit>
  );
}
