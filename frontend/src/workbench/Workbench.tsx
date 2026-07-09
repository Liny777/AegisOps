import { useEffect, useState } from "react";
import { color, radius } from "../theme/tokens";
import { toneColor } from "../theme/tokens";
import { Icon, IconButton } from "../ui";
import { api } from "../lib/api";
import type { ChatMessage, WorkbenchState } from "../lib/api/types";
import { RcaCard } from "./RcaCard";
import { HitlCard } from "./HitlCard";
import { Composer } from "./Composer";
import { ActivityRail } from "./ActivityRail";

/** 对话工作台（isChat）：三区 = 对话主区（消息 + RCA + HITL） + 右侧活动线。 */
export function Workbench() {
  const [state, setState] = useState<WorkbenchState | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [statusOpen, setStatusOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(true);

  useEffect(() => {
    let alive = true;
    api.getWorkbenchState("agt_pay_fast_recovery").then((s) => {
      if (!alive) return;
      setState(s);
      setMessages(s.messages);
    });
    return () => { alive = false; };
  }, []);

  if (!state) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <Icon name="loader-2" size={24} color={color.brand} spin />
      </div>
    );
  }

  const send = (text: string) => {
    setMessages((m) => [...m, { id: `u${m.length}`, role: "user", text }]);
    setTimeout(() => setMessages((m) => [...m, { id: `b${m.length}`, role: "bot", text: "（原型演示）已收到，正在按当前 RuntimePlan 编排巡检 / 定界 / 恢复子 Agent…", showCopy: true }]), 400);
  };

  return (
    <>
      {/* header */}
      <header style={{ flex: "0 0 auto", height: 56, borderBottom: `1px solid ${color.border}`, background: "#fff", display: "flex", alignItems: "center", padding: "0 20px", gap: 12 }}>
        <div style={{ fontSize: 15, fontWeight: 700, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{state.chatTitle}</div>
        <span title="创建时绑定，不可更改" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color: color.textNav, background: color.neutralBg, border: `1px solid ${color.border}`, padding: "4px 10px", borderRadius: radius.pill, whiteSpace: "nowrap", flex: "0 0 auto" }}>
          <Icon name="robot" size={14} color={color.brand} />当前使用：{state.agentName}<Icon name="lock" size={12} color={color.textFaint} />
        </span>
        <div style={{ flex: 1 }} />
        <IconButton icon="refresh" title="会话摘要" active={summaryOpen} onClick={() => setSummaryOpen((v) => !v)} />
        <IconButton icon="timeline-event" title="活动栏" active={activityOpen} onClick={() => setActivityOpen((v) => !v)} />
        <IconButton icon="list-check" title="服务状态" active={statusOpen} onClick={() => setStatusOpen((v) => !v)} />
      </header>

      {/* status chips */}
      {statusOpen ? (
        <div style={{ flex: "0 0 auto", borderBottom: `1px solid ${color.border}`, background: color.surfaceAlt, padding: "12px 20px", display: "flex", flexWrap: "wrap", gap: 10 }}>
          {state.statusChips.map((s) => (
            <span key={s.key} style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 12, fontWeight: 600, color: color.textBody, background: "#fff", border: `1px solid ${color.border}`, padding: "6px 11px", borderRadius: radius.md }}>
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: toneColor[s.tone].dot }} />{s.label}<span style={{ color: color.textSubtle, fontWeight: 500 }}>{s.value}</span>
            </span>
          ))}
        </div>
      ) : null}

      {/* summary banner */}
      {summaryOpen ? (
        <div style={{ flex: "0 0 auto", borderBottom: `1px solid ${color.border}`, background: color.brandTintBg, padding: "12px 20px" }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: color.brandStrong, marginBottom: 3 }}>会话摘要</div>
          <div style={{ fontSize: 13, color: color.textBody, lineHeight: 1.6 }}>{state.summaryText}</div>
        </div>
      ) : null}

      {/* body: conversation + activity */}
      <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "24px 0" }}>
            <div style={{ maxWidth: 760, margin: "0 auto", padding: "0 24px", display: "flex", flexDirection: "column", gap: 18 }}>
              {messages.map((m) => (m.role === "user" ? <UserBubble key={m.id} text={m.text} /> : <BotBubble key={m.id} text={m.text} showCopy={m.showCopy} />))}
              {state.rca ? <RcaCard rca={state.rca} onContinue={() => send("继续验证 H1")} /> : null}
              {state.hitl ? <HitlCard hitl={state.hitl} onDecide={(d) => api.decideApproval(state.hitl!.approval_request_id, d)} /> : null}
            </div>
          </div>
          <Composer
            skills={state.skills}
            models={state.models}
            currentModel={state.currentModel}
            onSend={send}
            onSelectModel={(mo) => api.selectModel("run_demo", mo.llm_config_id)}
          />
        </div>
        {activityOpen ? <ActivityRail groups={state.activity} /> : null}
      </div>
    </>
  );
}

function UserBubble({ text }: { text: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end", animation: "omMsg .25s ease" }}>
      <div style={{ maxWidth: "80%", background: color.brand, color: "#fff", fontSize: 14, lineHeight: 1.6, padding: "11px 15px", borderRadius: "14px 14px 4px 14px", whiteSpace: "pre-wrap" }}>{text}</div>
    </div>
  );
}

function BotBubble({ text, showCopy }: { text: string; showCopy?: boolean }) {
  return (
    <div style={{ display: "flex", gap: 11, animation: "omMsg .25s ease" }}>
      <div style={{ width: 30, height: 30, borderRadius: radius.md, background: color.brandTintBg, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 30px" }}>
        <Icon name="robot" size={17} color={color.brand} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ background: "#fff", border: `1px solid ${color.border}`, color: color.textStrong, fontSize: 14, lineHeight: 1.7, padding: "13px 15px", borderRadius: "4px 14px 14px 14px", whiteSpace: "pre-wrap" }}>{text}</div>
        {showCopy ? (
          <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, color: color.textSubtle, cursor: "pointer" }}><Icon name="copy" size={13} />复制</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
