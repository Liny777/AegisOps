import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { color, radius } from "../theme/tokens";
import { toneColor } from "../theme/tokens";
import { Icon, IconButton, Button } from "../ui";
import { api, API_MODE } from "../lib/api";
import { subscribeSse } from "../lib/runtime/sse";
import { runAguiTask, TRANSPORT } from "../lib/runtime/agui";
import { approvalToHitl, buildApprovalFacts, eventToNode, groupNodes } from "../lib/api/projection";
import { activityReducer, createActivityState, projectRailModel } from "../lib/activity";
import { API_BASE } from "../lib/api/client";
import type {
  ActivityNode,
  ChatMessage,
  HitlCardData,
  OpenOpsEvent,
  RcaCardData,
  WorkbenchState,
  Skill,
} from "../lib/api/types";
import { RcaCard } from "./RcaCard";
import { HitlCard } from "./HitlCard";
import { Composer } from "./Composer";
import { ActivityRail } from "./ActivityRail";
import { CopilotChatPanel } from "./copilot/CopilotChatPanel";
import { CopilotHitlFloat } from "./copilot/CopilotHitlFloat";
import { useApp, useSyncCurrentAgent } from "../lib/appState";
import { consumeAutoQuestion } from "../lib/autosend";

// Part B：CopilotChat 接管对话区（real+agui）。回退开关：VITE_OPENOPS_COPILOT_CHAT=0 回自建渲染。
const USE_COPILOT_CHAT =
  API_MODE === "real" && TRANSPORT === "agui" &&
  (import.meta.env.VITE_OPENOPS_COPILOT_CHAT as string | undefined) !== "0";

type ConnState = "connecting" | "open" | "reconnecting" | "error";

// 审批卡结果驻留时长：批准/拒绝后原地显「已批准/已拒绝」这么久再自动淡出（用户拍板"显示结果后自动消失"）
const HITL_RESULT_LINGER_MS = 2200;

/** 会话自动起名（与后端 run_state_service._auto_title 同规则）：单行化取前 30 字。 */
const autoTitle = (t: string) => {
  const s = t.split(/\s+/).filter(Boolean).join(" ");
  return s.length > 30 ? s.slice(0, 30) + "…" : s;
};

/** 优先使用 Clipboard API；非安全上下文/权限被拒时回退到浏览器原生 copy。 */
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 继续走兼容回退，避免“按钮可点但没有复制”。
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    textarea.remove();
  }
}

/** 对话工作台（isChat）：real 模式 = 按 run_id 恢复 或 ensureRun + /state + SSE（30.3/30.4/30.7）。
 *  两个入口：/agent-teams/:instanceId/chat（ensureRun 复用 active run）与 /agent-runs/:runId（按 run 恢复）。 */
export function Workbench() {
  const { instanceId = "", runId: runIdParam } = useParams();
  const [searchParams] = useSearchParams();
  const explicitRunId = runIdParam ?? searchParams.get("run_id");
  const { agents, currentAgentId, setCurrentAgentId } = useApp();
  useSyncCurrentAgent(instanceId);  // 新建实例 SPA 导航进来：侧栏列表缺它则重拉（实测 777 bug）
  const [demo] = useState<WorkbenchState>(() => api.demoState()); // 静态外观（chips/skills/models/摘要）
  const [runId, setRunId] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<"active" | "closed">("active");
  const [chatTitle, setChatTitle] = useState<string | null>(null);   // run_title（real）；null=未起名
  const [agentName, setAgentName] = useState<string | null>(null);   // /state 的 instance.instance_name
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [taskStatus, setTaskStatus] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [rca, setRca] = useState<RcaCardData | undefined>(undefined);
  const [hitl, setHitl] = useState<HitlCardData | undefined>(undefined);
  const [skills, setSkills] = useState<Skill[]>(demo.skills); // real：拉与执行门禁同源的装配集，失败回退 demo
  const [nodes, setNodes] = useState<ActivityNode[]>([]);
  const [activityState, dispatchActivity] = useReducer(activityReducer, undefined, createActivityState);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [conn, setConn] = useState<ConnState>("connecting");
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [statusOpen, setStatusOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(true);
  const seen = useRef(new Set<string>());
  const activeRunRef = useRef<string | null>(null);
  const runGenerationRef = useRef(0);
  // agui 流活跃期间对话区文本归 agui 独占（TEXT_MESSAGE_*）；SSE 的终态文本仅在无 agui 流（刷新接续）时追加，
  // 否则 SSE task.completed 先到会以同 event_id 建泡，agui 合成文本再追加 → 文本双写（双通道竞态）
  const aguiActive = useRef(false);
  // 外链 ?q= 待发问题：挂载即消费（sessionStorage 一次性，刷新不重发）；
  // copilot 路径经 CopilotAutoSend 发送，自建/mock 路径由下方 effect 调 send()
  const [autoQuestion, setAutoQuestion] = useState<string | null>(() => consumeAutoQuestion());
  const autoSentRef = useRef(false);

  const pushNode = useCallback((n: ActivityNode) => {
    setNodes((prev) => (seen.current.has(n.id) ? prev : (seen.current.add(n.id), [...prev, n])));
  }, []);

  /** 按 id 去重追加消息（AG-UI 合成结论与 SSE task.completed 共用 event_id，天然去重）。 */
  const appendMessage = useCallback((msg: ChatMessage) => {
    setMessages((m) => (m.some((x) => x.id === msg.id) ? m : [...m, msg]));
  }, []);

  /** 助手流式增量：同 messageId 累加，首个增量建气泡。 */
  const streamDelta = useCallback((messageId: string, delta: string) => {
    setMessages((m) => {
      const i = m.findIndex((x) => x.id === messageId);
      if (i < 0) return [...m, { id: messageId, role: "bot", text: delta, showCopy: true }];
      const next = m.slice();
      next[i] = { ...next[i], text: next[i].text + delta };
      return next;
    });
  }, []);

  /** openops.* 事件统一处理器：AG-UI CUSTOM 与 SSE 双通道复用（活动线按 event_id 去重）。 */
  const handleOpenOpsEvent = useCallback((e: OpenOpsEvent) => {
    // 快速切换会话时，旧 SSE/AG-UI 队列里可能仍有一条已出队事件；不得污染新 Run。
    if (!activeRunRef.current || e.agent_run_id !== activeRunRef.current) return;
    if (e.event_type === "openops.assistant.delta") return; // 文本增量走 TEXT_MESSAGE_*，不进活动线
    dispatchActivity({ type: "merge_events", events: [e], source: "live" });
    const node = eventToNode(e);
    // 双通道竞态：AG-UI CUSTOM 先到（aguiActive=true 不冒泡），SSE 同 event 迟到时 aguiActive 已复位
    // → 曾把 task.completed 的 message 冒成聊天气泡。首达才允许冒泡（活动线本身仍按 id 去重）。
    const firstDelivery = !seen.current.has(node.id);
    pushNode(node);
    const p = (e.payload_redacted_json ?? {}) as Record<string, any>;
    switch (e.event_type) {
      case "openops.task.started":
        setTaskStatus("running");
        if (e.task_id) setTaskId(e.task_id);
        break;
      case "openops.rca.updated":
        setRca(p as unknown as RcaCardData);
        break;
      case "openops.approval.required": {
        const tool = String(p.tool ?? "recover_execute");
        // 首选后端透传的通用入参字典（buildApprovalFacts 逐项展示）；缺失回退旧派生字段（兼容旧后端）
        const facts = p.args && typeof p.args === "object"
          ? buildApprovalFacts(tool, p.args as Record<string, unknown>)
          : p.command
            ? buildApprovalFacts(tool, { command: p.command })
            : buildApprovalFacts(tool, { target: p.target, impact: p.impact });
        setHitl({
          approval_request_id: String(p.approval_request_id ?? ""),
          title: "需要人工批准",
          tool,
          summary: e.message,
          facts,
          countdown: "5:00",
          status: "pending",
          tone: "warning",
        });
        break;
      }
      case "openops.approval.approved":
      case "openops.approval.rejected": {
        // 远端/超时决策路径：翻结果态并按 id 守卫计时器自动淡出（与本端 resolveHitl 幂等）
        const decided = e.event_type === "openops.approval.approved" ? "approved" : "rejected";
        setHitl((h) => (h ? { ...h, status: decided } : h));
        const aid = String(p.approval_request_id ?? "");
        window.setTimeout(
          () => setHitl((cur) => (cur && cur.status !== "pending" && (!aid || cur.approval_request_id === aid) ? undefined : cur)),
          HITL_RESULT_LINGER_MS,
        );
        break;
      }
      case "openops.task.completed":
        setTaskStatus("completed");
        if (!aguiActive.current && firstDelivery) appendMessage({ id: e.event_id, role: "bot", text: e.message, showCopy: true });
        break;
      case "openops.task.failed":
        setTaskStatus("failed");
        if (!aguiActive.current && firstDelivery) appendMessage({ id: e.event_id, role: "bot", text: e.message });
        break;
      case "openops.task.cancelled":
        setTaskStatus("cancelled");
        if (!aguiActive.current && firstDelivery) appendMessage({ id: e.event_id, role: "bot", text: e.message });
        break;
      case "openops.run.closed":
        setRunStatus("closed");
        break;
      default:
        break;
    }
  }, [pushNode, appendMessage]);

  const refresh = useCallback(async (rid: string, generation = runGenerationRef.current) => {
    const d = (await api.getRunState(rid)) as Record<string, any>;
    // /state 可能在路由已经切换后才返回；按 Run + generation 双重隔离异步回写。
    if (activeRunRef.current !== rid || runGenerationRef.current !== generation) return;
    // 按 run 恢复入口：state 返回 instance，回填侧栏当前 Agent 使其与该 run 对齐
    const instId = d.instance?.agent_team_instance_id;
    if (instId) setCurrentAgentId(String(instId));
    setChatTitle((d.run?.run_title as string | undefined) || null);
    if (d.instance?.instance_name) setAgentName(String(d.instance.instance_name));
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
    const recent = Array.isArray(d.recent_events) ? d.recent_events as Record<string, unknown>[] : [];
    for (const event of recent) {
      const id = event.event_id ?? event.audit_event_id;
      if (id) seen.current.add(String(id));
    }
    dispatchActivity({ type: "hydrate", snapshot: d });
  }, [setCurrentAgentId]);

  // 挂载：显式 run_id 优先恢复；否则 ensureRun 复用当前 Agent 的 active run。
  useEffect(() => {
    if (API_MODE !== "real" || (!instanceId && !explicitRunId)) {
      // mock 演示：直接用 demoState
      setMessages(demo.messages);
      setRca(demo.rca);
      setHitl(demo.hitl);
      setNodes(demo.activity.flatMap((g) => g.items));
      if (demo.activitySnapshot) dispatchActivity({ type: "hydrate", snapshot: demo.activitySnapshot });
      setConn("open");
      return;
    }
    let closed = false;
    let handle: { close: () => void } | null = null;
    const generation = ++runGenerationRef.current;
    activeRunRef.current = null;
    setRunId(null);
    setRunStatus("active");
    setChatTitle(null);
    setAgentName(null);
    setEditingTitle(false);
    setTaskId(null);
    setTaskStatus(null);
    setMessages([]);
    setRca(undefined);
    setHitl(undefined);
    setNodes([]);
    dispatchActivity({ type: "reset" });
    seen.current = new Set();
    setConn("connecting");
    (async () => {
      // ensureRun/refresh 失败必须透出（实测坑：静默吞掉 → runId 恒 null → send 走 mock 文案误导）
      let rid: string;
      try {
        rid = explicitRunId ?? (await api.ensureRun(instanceId));
      } catch (err) {
        if (closed) return;
        setConn("error");
        setMessages([{ id: "init_err", role: "bot", text: `会话创建失败：${(err as Error).message}` }]);
        return;
      }
      if (closed) return;
      if (runGenerationRef.current !== generation) return;
      activeRunRef.current = rid;
      setRunId(rid);
      // 模型在初始化向导已定，会话内不切换——不再拉 getModelConfigs / 渲染会话级选择器
      if (instanceId) {
        api.getAvailableSkills(instanceId).then((sk) => {
          if (!closed && sk.length) setSkills(sk); // 空装配集回退 demo（mock 演示不受影响）
        }).catch(() => undefined);
      }
      // 先挂被动 SSE，再拉 /state；连接建立时再补拉一次，覆盖「state 快照后、SSE 注册前」的极小窗口。
      // CopilotChat 主动流同时经 useAgent.subscribe 进入同一 reducer，四路最终按 event_id 去重。
      handle = subscribeSse(`${API_BASE}/openops/v1/agent-runs/${rid}/events/stream`, {
        onStateChange: (state) => {
          if (closed || runGenerationRef.current !== generation || activeRunRef.current !== rid) return;
          setConn(state);
          if (state === "open") void refresh(rid, generation);
        },
        onResync: () => {
          if (!closed && runGenerationRef.current === generation) void refresh(rid, generation);
        },
        onEvent: (raw) => {
          if (!closed && runGenerationRef.current === generation && activeRunRef.current === rid) {
            handleOpenOpsEvent(raw as OpenOpsEvent);
          }
        },
      });
      await refresh(rid, generation);
    })();
    return () => {
      closed = true;
      handle?.close();
      if (runGenerationRef.current === generation) {
        runGenerationRef.current += 1;
        activeRunRef.current = null;
      }
    };
  }, [instanceId, explicitRunId, refresh, handleOpenOpsEvent, demo]);

  const railModel = useMemo(() => projectRailModel(activityState), [activityState]);
  const hasUnifiedActivity = railModel.events.length > 0 || railModel.rounds.length > 0;
  const loadEarlier = useCallback(async () => {
    const activityRunId = runId ?? (API_MODE === "mock" ? "run_demo" : null);
    if (!activityRunId || loadingEarlier || !activityState.hasMore || !activityState.nextCursor) return;
    setLoadingEarlier(true);
    try {
      const page = await api.getActivityEvents(activityRunId, {
        before: activityState.nextCursor,
        limit: 100,
      });
      dispatchActivity({ type: "prepend_page", page });
    } catch (error) {
      console.warn("[OpenOps][activity] 加载更早事件失败", error);
    } finally {
      setLoadingEarlier(false);
    }
  }, [runId, loadingEarlier, activityState.hasMore, activityState.nextCursor]);

  const send = async (text: string) => {
    setMessages((m) => [...m, { id: `u${m.length}`, role: "user", text }]);
    // 首条输入即时起名（后端 start_task 同规则落库；本地先行避免等下一次 /state 才见标题）
    if (API_MODE === "real" && runId && !chatTitle) setChatTitle(autoTitle(text));
    if (API_MODE === "real" && !runId) {
      // 实测坑：此前与 mock 合一个分支，会话创建失败时回「(mock 演示）任务已受理」误导排障
      appendMessage({ id: `e${Date.now()}`, role: "bot", text: "会话未就绪（创建失败或仍在连接），请稍后重试；持续失败请看上方错误或后端日志。" });
      return;
    }
    if (API_MODE !== "real") {
      setTaskStatus("running");  // mock 也走按钮两态，便于演示
      setTimeout(() => {
        setMessages((m) => [...m, { id: `b${m.length}`, role: "bot", text: "（mock 演示）任务已受理。", showCopy: true }]);
        setTaskStatus("completed");
      }, 900);
      return;
    }
    if (!runId) return;  // 上方两分支已保证非空；此守卫仅为类型收窄
    setRca(undefined);
    setHitl(undefined);
    if (TRANSPORT === "agui") {
      // B5：任务经 AG-UI 端点启动并流式接收（标准事件→对话区；CUSTOM→同一 openops 处理器）
      setTaskStatus("running");
      aguiActive.current = true;
      runAguiTask(runId, text, {
        onOpenOps: handleOpenOpsEvent,
        onAssistantDelta: streamDelta,
        onDone: () => {
          aguiActive.current = false;
          void refresh(runId);
        },
        onError: (msg) => {
          aguiActive.current = false;
          setTaskStatus((s) => (s === "running" ? "failed" : s));
          appendMessage({ id: `e${Date.now()}`, role: "bot", text: `任务异常：${msg}` });
        },
      });
      return;
    }
    try {
      const r = await api.startTask(runId, text);
      setTaskId(r.task_id);
      setTaskStatus("running");
    } catch (err) {
      setMessages((m) => [...m, { id: `e${m.length}`, role: "bot", text: `无法启动任务：${(err as Error).message}` }]);
    }
  };

  // 外链自动发送（自建渲染/mock 路径；copilot 路径由面板内 CopilotAutoSend 负责，勿双发）。
  // 就绪门槛：mock=demo 分支挂载完（conn open）；real=runId 就绪。ref 幂等防 StrictMode/重渲染重发。
  useEffect(() => {
    if (!autoQuestion || autoSentRef.current) return;
    if (runStatus === "closed") return;
    if (USE_COPILOT_CHAT && runId) return;
    const ready = API_MODE !== "real" ? conn === "open" : !!runId;
    if (!ready) return;
    autoSentRef.current = true;
    console.info("[autosend] sending via composer path");
    const q = autoQuestion;
    setAutoQuestion(null);
    void send(q);
    // send 每次渲染重建且仅作动作不作触发条件，不进依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoQuestion, runId, runStatus, conn]);

  // 审批决策（本端点击）：发决策 → 原地显结果 → HITL_RESULT_LINGER_MS 后按 id 守卫自动淡出。
  // 副作用不放进 setState updater（防 StrictMode 双发 decide）。远端/超时决策由 handleOpenOpsEvent 兜底。
  const resolveHitl = useCallback((d: "approved" | "rejected") => {
    if (!hitl) return;
    const id = hitl.approval_request_id;
    if (API_MODE === "real") void api.decideApproval(id, d);
    setHitl((h) => (h ? { ...h, status: d } : h));
    window.setTimeout(
      () => setHitl((cur) => (cur && cur.approval_request_id === id ? undefined : cur)),
      HITL_RESULT_LINGER_MS,
    );
  }, [hitl]);

  const running = taskStatus === "running";
  // 标题=会话名（real：run_title，未起名「新对话」；mock 保持 demo 文案）；徽标=真实 Agent 名
  const displayTitle = chatTitle ?? (API_MODE === "real" ? "新对话" : demo.chatTitle);
  const displayAgent = agentName ?? agents.find((a) => a.instance_id === instanceId)?.name ?? demo.agentName;
  const commitTitle = () => {
    const t = titleDraft.split(/\s+/).filter(Boolean).join(" ").slice(0, 60);
    setEditingTitle(false);
    if (!t || !runId || t === chatTitle) return;
    const prev = chatTitle;
    setChatTitle(t);
    api.renameRun(runId, t).catch((e) => { setChatTitle(prev); alert(`重命名失败：${(e as Error).message}`); });
  };
  return (
    <>
      {/* header */}
      <header style={{ flex: "0 0 auto", height: 56, borderBottom: `1px solid ${color.border}`, background: "#fff", display: "flex", alignItems: "center", padding: "0 20px", gap: 12 }}>
        {editingTitle ? (
          <input
            autoFocus
            value={titleDraft}
            onChange={(e) => setTitleDraft(e.target.value)}
            onBlur={commitTitle}
            onKeyDown={(e) => { if (e.key === "Enter") commitTitle(); if (e.key === "Escape") setEditingTitle(false); }}
            maxLength={60}
            style={{ fontSize: 15, fontWeight: 700, border: `1px solid ${color.brandTintBorder}`, borderRadius: radius.md, padding: "4px 8px", width: 280, outline: "none", color: color.textStrong }}
          />
        ) : (
          <div title={displayTitle} style={{ fontSize: 15, fontWeight: 700, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{displayTitle}</div>
        )}
        {!editingTitle && runId ? (
          <IconButton icon="pencil" title="重命名会话" onClick={() => { setTitleDraft(chatTitle ?? ""); setEditingTitle(true); }} />
        ) : null}
        <span title="创建时绑定，不可更改" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color: color.textNav, background: color.neutralBg, border: `1px solid ${color.border}`, padding: "4px 10px", borderRadius: radius.pill, whiteSpace: "nowrap", flex: "0 0 auto" }}>
          <Icon name="robot" size={14} color={color.brand} />当前使用：{displayAgent}<Icon name="lock" size={12} color={color.textFaint} />
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
        {USE_COPILOT_CHAT && runId && runStatus !== "closed" ? (
          // Part B：CopilotChat 接管对话区（含输入框/发送按钮原生两态/停止=取消桥）。
          // RCA 卡不进消息流——由 SSE 通道驱动，浮在面板上方（30.4 三层模型不变）；
          // HITL 审批卡是动作项，锚在 composer 正上方（CopilotHitlFloat，用户实测反馈改位）。
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
            {rca ? (
              <div className="oa-chat-rca-dock" style={{ flex: "0 0 auto", maxHeight: "44%", overflowY: "auto", borderBottom: `1px solid ${color.border}`, background: color.pageBg }}>
                <div style={{ maxWidth: 760, margin: "0 auto" }}>
                  <RcaCard rca={rca} />
                </div>
              </div>
            ) : null}
            <CopilotChatPanel
              runId={runId}
              instanceId={instanceId || currentAgentId || ""}
              autoQuestion={autoQuestion}
              onAutoSent={() => setAutoQuestion(null)}
              onOpenOps={handleOpenOpsEvent}
            />
            <CopilotHitlFloat hitl={hitl} onDecide={resolveHitl} />
          </div>
        ) : (
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          <div className="oa-fallback-chat-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
            <div className="oa-fallback-chat-list" style={{ maxWidth: 760, margin: "0 auto", display: "flex", flexDirection: "column" }}>
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
                  onDecide={resolveHitl}
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
            <Composer skills={skills} onSend={send} />
          )}
        </div>
        )}
        {activityOpen ? (
          <ActivityRail
            key={runId ?? explicitRunId ?? "demo"}
            groups={API_MODE === "real" ? [] : groupNodes(nodes, running)}
            generalEvents={hasUnifiedActivity ? railModel.events : undefined}
            rounds={hasUnifiedActivity ? railModel.rounds : undefined}
            hasMore={hasUnifiedActivity && railModel.hasMore}
            loadingMore={loadingEarlier}
            onLoadMore={hasUnifiedActivity ? loadEarlier : undefined}
          />
        ) : null}
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
    error: { label: "会话创建失败", tone: "warning" },
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
      <div className="oa-fallback-user-content" style={{ maxWidth: "80%", background: color.brand, color: "#fff", padding: "9px 13px", borderRadius: "14px 14px 4px 14px", whiteSpace: "pre-wrap" }}>{text}</div>
    </div>
  );
}

function BotBubble({ text, showCopy }: { text: string; showCopy?: boolean }) {
  const [copied, setCopied] = useState(false);
  const copiedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (copiedTimer.current !== null) clearTimeout(copiedTimer.current);
  }, []);

  const handleCopy = async () => {
    if (!await copyText(text)) return;
    setCopied(true);
    if (copiedTimer.current !== null) clearTimeout(copiedTimer.current);
    copiedTimer.current = setTimeout(() => {
      setCopied(false);
      copiedTimer.current = null;
    }, 2000);
  };

  return (
    <div style={{ display: "flex", gap: 11, animation: "omMsg .25s ease" }}>
      <div style={{ width: 30, height: 30, borderRadius: radius.md, background: color.brandTintBg, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 30px" }}>
        <Icon name="robot" size={17} color={color.brand} />
      </div>
      <div className="oa-fallback-bot-body" style={{ flex: 1 }}>
        <div className="oa-fallback-bot-content" style={{ background: "#fff", border: `1px solid ${color.border}`, color: color.textStrong, borderRadius: "4px 14px 14px 14px", whiteSpace: "pre-wrap" }}>{text}</div>
        {showCopy ? (
          <button
            type="button"
            className="oa-fallback-copy-button"
            aria-label={copied ? "已复制" : "复制消息"}
            title={copied ? "已复制" : "复制消息"}
            onClick={handleCopy}
          >
            <Icon name={copied ? "check" : "copy"} size={14} color="currentColor" />
          </button>
        ) : null}
      </div>
    </div>
  );
}
