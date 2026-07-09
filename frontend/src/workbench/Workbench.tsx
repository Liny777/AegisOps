import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { color, radius } from "../theme/tokens";
import { toneColor } from "../theme/tokens";
import { Icon, IconButton, Button } from "../ui";
import { api, API_MODE } from "../lib/api";
import { subscribeSse } from "../lib/runtime/sse";
import { approvalToHitl, eventToNode, groupNodes } from "../lib/api/projection";
import type {
  ActivityNode,
  ChatMessage,
  HitlCardData,
  OpenOpsEvent,
  RcaCardData,
  WorkbenchState,
  ModelOption,
} from "../lib/api/types";
import { RcaCard } from "./RcaCard";
import { HitlCard } from "./HitlCard";
import { Composer } from "./Composer";
import { ActivityRail } from "./ActivityRail";

type ConnState = "connecting" | "open" | "reconnecting";

/** 对话工作台（isChat）：real 模式 = ensureRun + /state 恢复 + SSE 实时推进（30.3/30.4/30.7）。 */
export function Workbench() {
  const { instanceId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const explicitRunId = searchParams.get("run_id");
  const [demo] = useState<WorkbenchState>(() => api.demoState()); // 静态外观（chips/skills/models/摘要）
  const [runId, setRunId] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<"active" | "closed">("active");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [taskStatus, setTaskStatus] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [rca, setRca] = useState<RcaCardData | undefined>(undefined);
  const [hitl, setHitl] = useState<HitlCardData | undefined>(undefined);
  const [models, setModels] = useState<ModelOption[]>(demo.models);
  const [currentModel, setCurrentModel] = useState(demo.currentModel);
  const [nodes, setNodes] = useState<ActivityNode[]>([]);
  const [conn, setConn] = useState<ConnState>("connecting");
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [statusOpen, setStatusOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(true);
  const seen = useRef(new Set<string>());

  const pushNode = useCallback((n: ActivityNode) => {
    setNodes((prev) => (seen.current.has(n.id) ? prev : (seen.current.add(n.id), [...prev, n])));
  }, []);

  const refresh = useCallback(async (rid: string) => {
    const d = (await api.getRunState(rid)) as Record<string, any>;
    setRunStatus(d.run?.run_status === "closed" ? "closed" : "active");
    if (d.active_task) {
      setTaskId(d.active_task.task_id);
      setTaskStatus(d.active_task.status);
      setMessages((m) => (m.length ? m : [{ id: "u0", role: "user", text: d.active_task.input_text }]));
    }
    if (d.rca) setRca(d.rca as RcaCardData);
    const pend = (d.pending_approvals ?? []) as Record<string, unknown>[];
    if (pend.length) setHitl(approvalToHitl(pend[0]));
    else setHitl((h) => (h && h.status !== "pending" ? h : undefined));
    const audit = await api.getAuditNodes(rid);
    seen.current = new Set(audit.map((n) => n.id));
    setNodes(audit);
  }, []);

  // 挂载：显式 run_id 优先恢复；否则 ensureRun 复用当前 Agent 的 active run。
  useEffect(() => {
    if (API_MODE !== "real" || !instanceId) {
      // mock 演示：直接用 demoState
      setMessages(demo.messages);
      setRca(demo.rca);
      setHitl(demo.hitl);
      setNodes(demo.activity.flatMap((g) => g.items));
      setConn("open");
      return;
    }
    let closed = false;
    let handle: { close: () => void } | null = null;
    setRunId(null);
    setRunStatus("active");
    setTaskId(null);
    setTaskStatus(null);
    setMessages([]);
    setRca(undefined);
    setHitl(undefined);
    setNodes([]);
    seen.current = new Set();
    setConn("connecting");
    (async () => {
      const rid = explicitRunId ?? (await api.ensureRun(instanceId));
      if (closed) return;
      setRunId(rid);
      api.getModelConfigs().then((ms) => {
        if (closed || !ms.length) return;
        setModels(ms);
        setCurrentModel(ms.find((m) => m.current)?.label ?? ms[0].label);
      }).catch(() => undefined);
      await refresh(rid);
      handle = subscribeSse(`/api/openops/v1/agent-runs/${rid}/events/stream`, {
        onStateChange: setConn,
        onResync: () => void refresh(rid),
        onEvent: (raw) => {
          const e = raw as OpenOpsEvent;
          pushNode(eventToNode(e));
          const p = (e.payload_redacted_json ?? {}) as Record<string, any>;
          switch (e.event_type) {
            case "openops.task.started":
              setTaskStatus("running");
              break;
            case "openops.rca.updated":
              setRca(p as unknown as RcaCardData);
              break;
            case "openops.approval.required":
              setHitl({
                approval_request_id: String(p.approval_request_id ?? ""),
                title: "需要人工批准",
                tool: String(p.tool ?? "recover_execute"),
                summary: e.message,
                facts: [
                  { label: "目标", value: String(p.target ?? "—") },
                  { label: "影响说明", value: String(p.impact ?? "—") },
                ],
                countdown: "5:00",
                status: "pending",
                tone: "warning",
              });
              break;
            case "openops.approval.approved":
              setHitl((h) => (h ? { ...h, status: "approved" } : h));
              break;
            case "openops.approval.rejected":
              setHitl((h) => (h ? { ...h, status: "rejected" } : h));
              break;
            case "openops.task.completed":
              setTaskStatus("completed");
              setMessages((m) => [...m, { id: e.event_id, role: "bot", text: e.message, showCopy: true }]);
              break;
            case "openops.task.cancelled":
              setTaskStatus("cancelled");
              setMessages((m) => [...m, { id: e.event_id, role: "bot", text: e.message }]);
              break;
            case "openops.run.closed":
              setRunStatus("closed");
              break;
            default:
              break;
          }
        },
      });
    })();
    return () => {
      closed = true;
      handle?.close();
    };
  }, [instanceId, explicitRunId, refresh, pushNode, demo]);

  const send = async (text: string) => {
    setMessages((m) => [...m, { id: `u${m.length}`, role: "user", text }]);
    if (API_MODE !== "real" || !runId) {
      setTimeout(() => setMessages((m) => [...m, { id: `b${m.length}`, role: "bot", text: "（mock 演示）任务已受理。", showCopy: true }]), 400);
      return;
    }
    try {
      const r = await api.startTask(runId, text);
      setTaskId(r.task_id);
      setTaskStatus("running");
      setRca(undefined);
      setHitl(undefined);
    } catch (err) {
      setMessages((m) => [...m, { id: `e${m.length}`, role: "bot", text: `无法启动任务：${(err as Error).message}` }]);
    }
  };

  const running = taskStatus === "running";
  return (
    <>
      {/* header */}
      <header style={{ flex: "0 0 auto", height: 56, borderBottom: `1px solid ${color.border}`, background: "#fff", display: "flex", alignItems: "center", padding: "0 20px", gap: 12 }}>
        <div style={{ fontSize: 15, fontWeight: 700, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{demo.chatTitle}</div>
        <span title="创建时绑定，不可更改" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color: color.textNav, background: color.neutralBg, border: `1px solid ${color.border}`, padding: "4px 10px", borderRadius: radius.pill, whiteSpace: "nowrap", flex: "0 0 auto" }}>
          <Icon name="robot" size={14} color={color.brand} />当前使用：{demo.agentName}<Icon name="lock" size={12} color={color.textFaint} />
        </span>
        <ConnBadge conn={conn} closed={runStatus === "closed"} />
        <div style={{ flex: 1 }} />
        {running && taskId ? (
          <Button variant="ghost" icon="player-stop" onClick={() => api.cancelTask(taskId)}>取消任务</Button>
        ) : null}
        {runStatus === "active" && runId ? (
          <Button variant="ghost" icon="lock" onClick={() => api.closeRun(runId).then(() => setRunStatus("closed"))}>关闭会话</Button>
        ) : null}
        <IconButton icon="refresh" title="会话摘要" active={summaryOpen} onClick={() => setSummaryOpen((v) => !v)} />
        <IconButton icon="timeline-event" title="活动栏" active={activityOpen} onClick={() => setActivityOpen((v) => !v)} />
        <IconButton icon="list-check" title="服务状态" active={statusOpen} onClick={() => setStatusOpen((v) => !v)} />
      </header>

      {statusOpen ? (
        <div style={{ flex: "0 0 auto", borderBottom: `1px solid ${color.border}`, background: color.surfaceAlt, padding: "12px 20px", display: "flex", flexWrap: "wrap", gap: 10 }}>
          {demo.statusChips.map((s) => (
            <span key={s.key} style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 12, fontWeight: 600, color: color.textBody, background: "#fff", border: `1px solid ${color.border}`, padding: "6px 11px", borderRadius: radius.md }}>
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: toneColor[s.tone].dot }} />{s.label}<span style={{ color: color.textSubtle, fontWeight: 500 }}>{s.value}</span>
            </span>
          ))}
        </div>
      ) : null}

      {summaryOpen ? (
        <div style={{ flex: "0 0 auto", borderBottom: `1px solid ${color.border}`, background: color.brandTintBg, padding: "12px 20px" }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: color.brandStrong, marginBottom: 3 }}>会话摘要</div>
          <div style={{ fontSize: 13, color: color.textBody, lineHeight: 1.6 }}>{demo.summaryText}</div>
        </div>
      ) : null}

      <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "24px 0" }}>
            <div style={{ maxWidth: 760, margin: "0 auto", padding: "0 24px", display: "flex", flexDirection: "column", gap: 18 }}>
              {messages.length === 0 && !running ? (
                <div style={{ textAlign: "center", color: color.textSubtle, fontSize: 13, padding: "40px 0" }}>
                  <Icon name="message-2" size={22} color={color.brand} />
                  <div style={{ marginTop: 10 }}>描述你的排障任务，Agent 会按「巡检 → 定界 → 恢复」推进，恢复动作需你确认。</div>
                </div>
              ) : null}
              {messages.map((m) => (m.role === "user" ? <UserBubble key={m.id} text={m.text} /> : <BotBubble key={m.id} text={m.text} showCopy={m.showCopy} />))}
              {rca ? <RcaCard rca={rca} /> : null}
              {hitl ? (
                <HitlCard
                  key={hitl.approval_request_id + hitl.status}
                  hitl={hitl}
                  onDecide={(d) => api.decideApproval(hitl.approval_request_id, d)}
                />
              ) : null}
              {running ? (
                <div style={{ display: "flex", alignItems: "center", gap: 8, color: color.textSubtle, fontSize: 12.5 }}>
                  <Icon name="loader-2" size={14} color={color.brand} spin />Agent 正在调查…（工具细节见右侧活动栏）
                </div>
              ) : null}
            </div>
          </div>
          {runStatus === "closed" ? (
            <div style={{ flex: "0 0 auto", padding: "14px 24px 18px", textAlign: "center", color: color.textSubtle, fontSize: 12.5, background: color.pageBg }}>
              <Icon name="lock" size={14} /> 会话已关闭：只读查看历史与审计，不能再启动新任务。
            </div>
          ) : (
            <Composer
              skills={demo.skills}
              models={models}
              currentModel={currentModel}
              onSend={send}
              onSelectModel={(mo) => {
                setCurrentModel(mo.label);
                if (runId) void api.selectModel(runId, mo.llm_config_id);
              }}
            />
          )}
        </div>
        {activityOpen ? <ActivityRail groups={groupNodes(nodes, running)} /> : null}
      </div>
    </>
  );
}

function ConnBadge({ conn, closed }: { conn: ConnState; closed: boolean }) {
  if (closed) {
    return <span style={{ fontSize: 11.5, fontWeight: 600, color: color.textSubtle }}>已关闭</span>;
  }
  const map: Record<ConnState, { label: string; tone: "good" | "warning" | "neutral" }> = {
    open: { label: "实时", tone: "good" },
    connecting: { label: "连接中", tone: "neutral" },
    reconnecting: { label: "重连中", tone: "warning" },
  };
  const m = map[conn];
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, fontWeight: 600, color: toneColor[m.tone].text }}>
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: toneColor[m.tone].dot, animation: conn !== "open" ? "omPulse 1.2s ease-in-out infinite" : undefined }} />
      {m.label}
    </span>
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
