import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { color, radius, type Tone } from "../theme/tokens";
import { toneColor } from "../theme/tokens";
import { Icon, IconButton, Button } from "../ui";
import { api, API_MODE, forgetEnsuredRun, isAbortError } from "../lib/api";
import { subscribeSse } from "../lib/runtime/sse";
import { runAguiTask, TRANSPORT } from "../lib/runtime/agui";
import { approvalToHitl, buildApprovalFacts, eventToNode, groupNodes, projectRca } from "../lib/api/projection";
import { activityReducer, createActivityState, projectRailModel } from "../lib/activity";
import { API_BASE } from "../lib/api/client";
import { mockRcaFinal, mockRecoveryClosureEvents } from "../lib/api/mockData";
import { parseRcaCard } from "../lib/rca/schema";
import { initialRcaGuard, nextRcaGuard, shouldApplyRca, type RcaGuard } from "../lib/rca/model";
import { deriveRecoveryState } from "../lib/rca/recovery";
import type {
  ActivityNode,
  ChatMessage,
  HitlCardData,
  OpenOpsEvent,
  RcaCardData,
  StatusChip,
  WorkbenchState,
  Skill,
} from "../lib/api/types";
import { newSendNonce, type PendingSend } from "./copilot/programmaticSend";
import { HitlCard } from "./HitlCard";
import { Composer } from "./Composer";
import { resolveModelLabel } from "./modelLabel";
import { ActivityRail } from "./ActivityRail";
import { CopilotChatPanel, CopilotWorkbenchProvider } from "./copilot/CopilotChatPanel";
import { CopilotErrorBoundary } from "./copilot/CopilotErrorBoundary";
import { CopilotHitlFloat } from "./copilot/CopilotHitlFloat";
import { useApp, useSyncCurrentAgent } from "../lib/appState";
import { consumeAutoQuestion } from "../lib/autosend";
import type { ResolvedWorkbenchSession, WorkbenchTarget } from "../layout/workbenchSession";
import {
  clearRunRecoveryIssue,
  isCurrentRunAction,
  isCurrentRunStream,
  isOwnedOrStaleInitializationCancellation,
  recoverSnapshotMessages,
  resetActivityPageRequest,
  shouldShowWorkbenchPortal,
  workbenchTransitionUi,
  type WorkbenchRecoveryIssue,
} from "./workbenchRecovery";

// Part B：CopilotChat 接管对话区（real+agui）。回退开关：VITE_OPENOPS_COPILOT_CHAT=0 回自建渲染。
const USE_COPILOT_CHAT =
  API_MODE === "real" && TRANSPORT === "agui" &&
  (import.meta.env.VITE_OPENOPS_COPILOT_CHAT as string | undefined) !== "0";

type ConnState = "connecting" | "open" | "reconnecting" | "error";

// 审批卡结果驻留时长：批准/拒绝后原地显「已批准/已拒绝」这么久再自动淡出（用户拍板"显示结果后自动消失"）
const HITL_RESULT_LINGER_MS = 2200;

// 右侧宽面板宽度：默认 CSS clamp(420px, 33.333%, 760px)（右栏占内容区 1/3、对话区 2/3）；拖拽后 clamp 同区间并持久化
// （与用户无关的全局偏好，一个 key 即可）。
const RAIL_WIDTH_KEY = "openops.activityRail.width";
const RAIL_WIDTH_MIN = 420;
const RAIL_WIDTH_MAX = 760;
const clampRailWidth = (px: number) =>
  Math.round(Math.min(RAIL_WIDTH_MAX, Math.max(RAIL_WIDTH_MIN, px)));

function loadRailWidth(): number | null {
  try {
    const raw = window.localStorage.getItem(RAIL_WIDTH_KEY);
    const parsed = raw == null ? NaN : Number(raw);
    return Number.isFinite(parsed) ? clampRailWidth(parsed) : null;
  } catch {
    return null;
  }
}

function saveRailWidth(width: number): void {
  try {
    window.localStorage.setItem(RAIL_WIDTH_KEY, String(width));
  } catch {
    // Safari 隐私模式或禁用存储时仅当前会话生效。
  }
}

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
export function Workbench({
  target,
  sessionGeneration,
  visible,
  routeTransitionPending = false,
  onSessionResolved,
}: {
  target: WorkbenchTarget;
  /** 同 raw target 需要重新 ensure（例如 retained Run 已 closed）时递增；不得用于 Provider key。 */
  sessionGeneration: number;
  /** 仅控制浮层可见性；不得参与 Run 初始化或连接 effect。 */
  visible: boolean;
  /** URL 已更新但 120ms latest-wins 目标尚未完成切换。 */
  routeTransitionPending?: boolean;
  onSessionResolved: (session: ResolvedWorkbenchSession) => void;
}) {
  const { instanceId, explicitRunId } = target;
  const { agents, setCurrentAgentId } = useApp();
  const nav = useNavigate();
  const [demo] = useState<WorkbenchState>(() => api.demoState()); // 静态外观（chips/skills/models/摘要）
  const [runId, setRunId] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<"active" | "closed">("active");
  const [resolvedInstanceId, setResolvedInstanceId] = useState(instanceId);
  const [chatTitle, setChatTitle] = useState<string | null>(null);   // run_title（real）；null=未起名
  const [agentName, setAgentName] = useState<string | null>(null);   // /state 的 instance.instance_name
  const [initializationIssue, setInitializationIssue] = useState<WorkbenchRecoveryIssue | null>(null);
  // 自带模型降级告知（openops.model.user_llm_degraded）：绑的自带模型被删/禁用后，本次任务已改用
  // 平台默认模型。这是用户**唯一**能看见降级的地方——主 Agent 时间线 real 模式恒空，该事件也没有
  // delegation_id 进不了子 Agent 轮次，不弹这条横幅用户就只能在管理员审计页才看得到。
  const [modelDegraded, setModelDegraded] = useState<string | null>(null);
  const [retryGeneration, setRetryGeneration] = useState(0);
  const [copilotConnectionGeneration, setCopilotConnectionGeneration] = useState(0);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [taskStatus, setTaskStatus] = useState<string | null>(null);
  // 排队位次（1-based）：名额满时后端入队并下推，null=未排队
  const [queuePosition, setQueuePosition] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [hitl, setHitl] = useState<HitlCardData | undefined>(undefined);
  const [rca, setRca] = useState<RcaCardData | undefined>(undefined); // 诊断面板（右栏「诊断」tab）
  // rcaGuard 分段键（board_task_id ?? task_id）的可渲染镜像：ref 不触发重渲染，恢复节点
  // 的段过滤需要它进 useMemo 依赖。与 rcaGuardRef 在同四处赋值点同步 set。
  const [rcaBoardId, setRcaBoardId] = useState<string | null>(null);
  // /state.pending_approvals 原始行：恢复节点推导的种子（防 recent_events 窗口挤出 required）。
  const [pendingApprovalRows, setPendingApprovalRows] = useState<Record<string, unknown>[]>([]);
  const [pendingSend, setPendingSend] = useState<PendingSend | null>(null); // 卡片按钮 → copilot 程序化发送桥
  const [railWidth, setRailWidth] = useState<number | null>(() => loadRailWidth());
  const [skills, setSkills] = useState<Skill[]>(demo.skills); // real：拉与执行门禁同源的装配集，失败回退 demo
  const [modelLabel, setModelLabel] = useState("");  // 输入框只读展示的当前实例模型名（overlay→label，不可切换）
  const [wsStatus, setWsStatus] = useState<{ sync_status: string; app_ids: string[] } | null>(null); // 服务状态：oModel 同步态 + 范围 APPID 真值
  const [mcpStat, setMcpStat] = useState<{ active: number; total: number } | null>(null); // 服务状态：MCP 装配数 + 注册 active 数
  const [nodes, setNodes] = useState<ActivityNode[]>([]);
  const [activityState, dispatchActivity] = useReducer(activityReducer, undefined, createActivityState);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [conn, setConn] = useState<ConnState>("connecting");
  const [statusOpen, setStatusOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(true);
  const targetKey = `${instanceId}\u0000${explicitRunId ?? ""}`;
  type TransitionState = {
    key: string;
    status: "loading" | "ready" | "error";
  };
  const initialTransition: TransitionState = { key: targetKey, status: "loading" };
  const [transition, setTransition] = useState<TransitionState>(initialTransition);
  const transitionGuardRef = useRef<TransitionState>(initialTransition);
  // target prop 在 effect 之前已经生效；同步把 guard 标为 loading，旧 Run 的 resync
  // 就不会在这一帧覆盖/清除新 target 的切换结果。
  if (transitionGuardRef.current.key !== targetKey) {
    transitionGuardRef.current = { key: targetKey, status: "loading" };
  }
  const updateTransition = useCallback((next: TransitionState) => {
    transitionGuardRef.current = next;
    setTransition(next);
  }, []);
  const {
    failedCurrentTarget,
    showSwitchOverlay,
    showFailureOverlay,
    blockInput: workbenchInputBlocked,
  } = workbenchTransitionUi({
    hasDisplayedRun: Boolean(runId),
    routeTransitionPending,
    transitionKey: transition.key,
    targetKey,
    transitionStatus: transition.status,
  });
  const seen = useRef(new Set<string>());
  // 诊断面板 revision 守卫：task 分段单调（双通道重复投递/resync 旧快照都不闪回）。
  const rcaGuardRef = useRef<RcaGuard>(initialRcaGuard());
  const activeRunRef = useRef<string | null>(null);
  const activeInstanceRef = useRef<string>("");
  const runGenerationRef = useRef(0);
  const sseOpenRef = useRef(false);
  const sseHandleRef = useRef<{ close: () => void } | null>(null);
  const refreshRequestRef = useRef<AbortController | null>(null);
  const activityRequestRef = useRef<AbortController | null>(null);
  const initializationError = initializationIssue?.message ?? null;
  const effectiveInstanceId = resolvedInstanceId || instanceId;
  useSyncCurrentAgent(effectiveInstanceId);  // 新建/按 Run 恢复后同步侧栏，缺实例时兜底重拉
  // agui 流活跃期间对话区文本归 agui 独占（TEXT_MESSAGE_*）；SSE 的终态文本仅在无 agui 流（刷新接续）时追加，
  // 否则 SSE task.completed 先到会以同 event_id 建泡，agui 合成文本再追加 → 文本双写（双通道竞态）
  const aguiActive = useRef<{ runId: string; token: symbol } | null>(null);
  // 外链 ?q= 待发问题：挂载即消费（sessionStorage 一次性，刷新不重发）；
  // copilot 路径经 CopilotAutoSend 发送，自建/mock 路径由下方 effect 调 send()
  const [autoQuestion, setAutoQuestion] = useState<string | null>(() => consumeAutoQuestion());
  const autoSentRef = useRef(false);

  useEffect(() => {
    if (
      API_MODE !== "real" || !runId || !effectiveInstanceId ||
      transition.key !== targetKey || transition.status !== "ready"
    ) return;
    // ensureRun/createRun 已给出 canonical runId 时先上报 provisional active 身份：即便 /state
    // 仍在等待，设置页也能通过 /agent-runs/:runId 回到同一个 CopilotKit；hydrate 后再校正 closed。
    onSessionResolved({ runId, instanceId: effectiveInstanceId, runStatus });
  }, [runId, effectiveInstanceId, runStatus, onSessionResolved, targetKey, transition]);

  // `/agent-runs/:runId` 初始没有 instanceId；待 /state 解析归属实例后再加载同源 Skill 集。
  useEffect(() => {
    if (API_MODE !== "real" || !effectiveInstanceId) return;
    const controller = new AbortController();
    // 常驻 Workbench 跨 Agent 时先撤下旧能力；空列表与失败都不能继续暴露上一 Agent 的 Skill。
    setSkills([]);
    api.getAvailableSkills(effectiveInstanceId, { signal: controller.signal })
      .then((next) => {
        if (!controller.signal.aborted) setSkills(next);
      })
      .catch((error) => {
        if (!isAbortError(error)) console.warn("[OpenOps][skills] 能力列表读取失败", error);
      });
    return () => controller.abort();
  }, [effectiveInstanceId]);

  // 输入框模型徽标 + 服务状态真值（oModel 同步态 / 范围 APPID / MCP 装配数）：按当前实例并发拉取。
  useEffect(() => {
    if (API_MODE !== "real" || !effectiveInstanceId) return;
    const controller = new AbortController();
    setModelLabel(""); setWsStatus(null); setMcpStat(null);
    // 模板清单失败不摧毁整条链（catch 空数组 → 模板绑定显示兜底文案）
    Promise.all([api.getAgentTeam(effectiveInstanceId), api.getModelConfigs(),
                 api.getModelTemplates().catch(() => [])])
      .then(([team, models, mtpls]) => {
        if (controller.signal.aborted) return;
        setModelLabel(resolveModelLabel(team.overlay, models, mtpls));
        if (team.workspace_id) {
          api.getWorkspaceStatus(team.workspace_id)
            .then((ws) => { if (!controller.signal.aborted) setWsStatus({ sync_status: ws.sync_status, app_ids: ws.app_ids }); })
            .catch(() => undefined);
        }
      })
      .catch(() => undefined);
    // 统计要的是全量口径（active/总数）→ 用 getAllMcps 翻页取全；取分页首页会把统计算成「每页」数
    api.getAllMcps()
      .then((rows) => {
        if (controller.signal.aborted) return;
        setMcpStat({ active: rows.filter((r) => r.status === "active").length, total: rows.length });
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [effectiveInstanceId]);

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
        setQueuePosition(null);           // 轮到自己了，排队条撤下
        if (e.task_id) setTaskId(e.task_id);
        break;
      // 名额满时入队：位次随队列变化实时下推（复用本 run 的 SSE 通道，无需额外轮询）
      case "openops.task.queued":
      case "openops.task.queue_position":
        setTaskStatus("queued");
        setQueuePosition(Number(p.queue_position ?? 0) || null);
        if (e.task_id) setTaskId(e.task_id);
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
        setQueuePosition(null);
        if (aguiActive.current?.runId !== e.agent_run_id && firstDelivery) appendMessage({ id: e.event_id, role: "bot", text: e.message, showCopy: true });
        break;
      case "openops.task.failed":
        setTaskStatus("failed");
        setQueuePosition(null);
        if (aguiActive.current?.runId !== e.agent_run_id && firstDelivery) appendMessage({ id: e.event_id, role: "bot", text: e.message });
        break;
      case "openops.task.cancelled":
        setTaskStatus("cancelled");
        setQueuePosition(null);
        if (aguiActive.current?.runId !== e.agent_run_id && firstDelivery) appendMessage({ id: e.event_id, role: "bot", text: e.message });
        break;
      case "openops.rca.updated": {
        // 诊断面板：双通道同 event_id 各投递一次，仅首达应用（各通道自身有序 → 首达序即
        // 全序，迟到副本不会跨段重置守卫）→ zod 校验（版本漂移 payload 静默丢弃该 revision）
        // → 面板身份分段 revision 守卫。分段键用 payload.board_task_id（owner 主任务）：
        // envelope 的 task_id 是调用方（子 Agent 更新带子 task_id、主任务兜底带主 task_id），
        // 拿它分段会把跨归属切换误判成「新任务」重置守卫；缺失时回退 task_id（demo/旧后端）。
        // run 隔离已由函数首行 activeRunRef 早退保证。
        if (!firstDelivery) break;
        const parsed = parseRcaCard(p);
        if (!parsed) break;
        const boardId = p.board_task_id ? String(p.board_task_id) : e.task_id ?? null;
        if (!shouldApplyRca(rcaGuardRef.current, boardId, parsed.revision ?? null)) break;
        rcaGuardRef.current = nextRcaGuard(rcaGuardRef.current, boardId, parsed.revision ?? null);
        setRcaBoardId(boardId);
        setRca(projectRca(parsed as unknown as Record<string, unknown>, e.occurred_at));
        break;
      }
      case "openops.model.user_llm_degraded":
        // 绑的自带模型没了 → 已降级平台默认。横幅常驻直到用户去改绑或手动关闭：任务照跑，
        // 但用户必须知道这次的 prompt 发去了平台模型而不是他自己的模型。
        setModelDegraded(e.message || "原自带模型不可用，本次已改用平台默认模型，请重新选择模型");
        break;
      case "openops.run.closed":
        setRunStatus("closed");
        if (activeInstanceRef.current) forgetEnsuredRun(activeInstanceRef.current, e.agent_run_id);
        break;
      default:
        break;
    }
  }, [pushNode, appendMessage]);

  const applyStateSnapshot = useCallback((
    d: Record<string, any>,
    rid: string,
    fallbackInstanceId: string,
    replaceSession: boolean,
  ): { instanceId: string; runStatus: "active" | "closed"; lastEventSeq: number | null } => {
    const stateInstanceId = String(d.instance?.agent_team_instance_id ?? fallbackInstanceId ?? "");
    if (!stateInstanceId) throw new Error("会话状态缺少 Agent 归属信息");
    const nextRunStatus = d.run?.run_status === "closed" ? "closed" : "active";
    const activeTask = d.active_task as Record<string, any> | null | undefined;

    setResolvedInstanceId(stateInstanceId);
    setCurrentAgentId(stateInstanceId);
    setChatTitle((d.run?.run_title as string | undefined) || null);
    setAgentName(d.instance?.instance_name ? String(d.instance.instance_name) : null);
    setRunStatus(nextRunStatus);
    if (nextRunStatus === "closed") forgetEnsuredRun(stateInstanceId, rid);
    if (replaceSession) {
      setEditingTitle(false);
      setTaskId(activeTask?.task_id ? String(activeTask.task_id) : null);
      setTaskStatus(activeTask?.status ? String(activeTask.status) : null);
      setMessages(recoverSnapshotMessages(d, rid));
      setNodes([]);
      seen.current = new Set();
    } else if (activeTask) {
      setTaskId(activeTask.task_id ? String(activeTask.task_id) : null);
      setTaskStatus(activeTask.status ? String(activeTask.status) : null);
      if (activeTask.input_text) {
        setMessages((messages) => messages.length
          ? messages
          : [{ id: `u-${rid}`, role: "user", text: String(activeTask.input_text) }]);
      }
      const recoveredTerminal = recoverSnapshotMessages(d, rid).find((message) => message.role === "bot");
      if (recoveredTerminal) {
        setMessages((current) => current.some((message) => message.id === recoveredTerminal.id)
          ? current
          : [...current, recoveredTerminal]);
      }
    }

    const pending = (d.pending_approvals ?? []) as Record<string, unknown>[];
    // 恢复节点种子整体替换：/state 是待审批集合的权威快照（已决/超时行不在其中）。
    setPendingApprovalRows(pending);
    if (pending.length) setHitl(approvalToHitl(pending[0]));
    else if (replaceSession) setHitl(undefined);
    else setHitl((current) => (current && current.status !== "pending" ? current : undefined));

    // 诊断面板恢复：`/state` 顶层 rca 与 rca.updated payload 同形。切换会话（replaceSession）
    // 无条件恢复并重置守卫；同 Run resync/refresh 仅 revision 守卫通过才覆盖，且
    // d.rca==null **不清空**——重连内存 miss 不闪卡。分段键与 live 事件同源：
    // 优先 rca.board_task_id（owner 主任务身份），缺失回退 active_task.task_id。
    const snapshotTaskId = activeTask?.task_id ? String(activeTask.task_id) : null;
    const snapshotBoardId = d.rca?.board_task_id ? String(d.rca.board_task_id) : snapshotTaskId;
    const restoredRca = d.rca ? parseRcaCard(d.rca) : undefined;
    if (replaceSession) {
      rcaGuardRef.current = restoredRca
        ? nextRcaGuard(initialRcaGuard(), snapshotBoardId, restoredRca.revision ?? null)
        : initialRcaGuard();
      setRcaBoardId(rcaGuardRef.current.taskId);
      setRca(restoredRca ? projectRca(restoredRca as unknown as Record<string, unknown>) : undefined);
      setPendingSend(null); // 旧会话挂起的程序化发送不得落到新 thread
    } else if (restoredRca && shouldApplyRca(rcaGuardRef.current, snapshotBoardId, restoredRca.revision ?? null)) {
      rcaGuardRef.current = nextRcaGuard(rcaGuardRef.current, snapshotBoardId, restoredRca.revision ?? null);
      setRcaBoardId(rcaGuardRef.current.taskId);
      setRca(projectRca(restoredRca as unknown as Record<string, unknown>));
    }

    const recent = Array.isArray(d.recent_events) ? d.recent_events as Record<string, unknown>[] : [];
    for (const event of recent) {
      const id = event.event_id ?? event.audit_event_id;
      if (id) seen.current.add(String(id));
    }
    dispatchActivity({ type: replaceSession ? "replace_snapshot" : "hydrate", snapshot: d });

    const lastEventSeq = Number(d.last_event_seq);
    return {
      instanceId: stateInstanceId,
      runStatus: nextRunStatus,
      lastEventSeq: Number.isFinite(lastEventSeq) && lastEventSeq >= 0 ? lastEventSeq : null,
    };
  }, [setCurrentAgentId]);

  const refresh = useCallback(async (
    rid: string,
    fallbackInstanceId: string,
    signal?: AbortSignal,
  ): Promise<boolean> => {
    try {
      const d = (await api.getRunState(rid, { signal })) as Record<string, any>;
      if (activeRunRef.current !== rid) return false;
      applyStateSnapshot(d, rid, fallbackInstanceId, false);
      setInitializationIssue((current) => clearRunRecoveryIssue(current, rid));
      if (sseOpenRef.current) setConn("open");
      return true;
    } catch (error) {
      if (isAbortError(error) || activeRunRef.current !== rid) return false;
      // 新 target 正在 loading/error 时，当前 rid 仍是屏幕上保留的旧 Run；旧 resync
      // 不能覆盖新 target 的切换提示。
      if (transitionGuardRef.current.status !== "ready") return false;
      setInitializationIssue({
        owner: "run",
        runId: rid,
        message: `会话状态恢复失败：${(error as Error).message}`,
      });
      setConn("error");
      return false;
    }
  }, [applyStateSnapshot]);

  const openSse = useCallback((
    rid: string,
    fallbackInstanceId: string,
    initialLastEventId: number | null,
  ) => subscribeSse(`${API_BASE}/openops/v1/agent-runs/${rid}/events/stream`, {
    initialLastEventId,
    onStateChange: (state) => {
      if (activeRunRef.current !== rid) return;
      sseOpenRef.current = state === "open";
      setConn(state);
    },
    onResync: () => {
      if (activeRunRef.current !== rid) return;
      refreshRequestRef.current?.abort();
      const controller = new AbortController();
      refreshRequestRef.current = controller;
      void refresh(rid, fallbackInstanceId, controller.signal).finally(() => {
        if (refreshRequestRef.current === controller) refreshRequestRef.current = null;
      });
    },
    onEvent: (raw) => {
      if (activeRunRef.current === rid) handleOpenOpsEvent(raw as OpenOpsEvent);
    },
  }), [handleOpenOpsEvent, refresh]);

  // 显式 run_id 优先；否则 ensureRun。切换期间保留旧内容与旧连接，/state 成功后原子换入。
  useEffect(() => {
    if (API_MODE !== "real" || (!instanceId && !explicitRunId)) {
      setMessages(demo.messages);
      setHitl(demo.hitl);
      setRca(demo.rca);
      rcaGuardRef.current = initialRcaGuard();
      // 镜像同步：demo 板无任务归属（boardTaskId=null → 恢复推导不过滤，吃下 demo 快照全部事件）
      setRcaBoardId(null);
      setPendingApprovalRows([]);
      setPendingSend(null);
      setNodes(demo.activity.flatMap((group) => group.items));
      if (demo.activitySnapshot) dispatchActivity({ type: "hydrate", snapshot: demo.activitySnapshot });
      setConn("open");
      updateTransition({ key: targetKey, status: "ready" });
      return;
    }

    const generation = ++runGenerationRef.current;
    const controller = new AbortController();
    const hadDisplayedRun = activeRunRef.current !== null;
    refreshRequestRef.current?.abort();
    refreshRequestRef.current = null;
    updateTransition({ key: targetKey, status: "loading" });
    setInitializationIssue(null);
    setModelDegraded(null);  // 降级横幅属于上一个会话/Agent，切目标必须清，否则串台
    if (!hadDisplayedRun) setConn("connecting");

    void (async () => {
      try {
        const rid = explicitRunId ?? await api.ensureRun(instanceId, { signal: controller.signal });
        if (controller.signal.aborted || runGenerationRef.current !== generation) return;
        const snapshot = await api.getRunState(rid, { signal: controller.signal }) as Record<string, any>;
        if (controller.signal.aborted || runGenerationRef.current !== generation) return;

        // Validate/derive everything before moving the canonical event guard.
        const derived = applyStateSnapshot(snapshot, rid, instanceId, true);
        const previousSse = sseHandleRef.current;
        activeRunRef.current = rid;
        activeInstanceRef.current = derived.instanceId;
        sseOpenRef.current = false;
        setRunId(rid);
        setConn("connecting");
        sseHandleRef.current = openSse(rid, derived.instanceId, derived.lastEventSeq);
        previousSse?.close();
        setInitializationIssue(null);
        updateTransition({ key: targetKey, status: "ready" });
      } catch (error) {
        if (isOwnedOrStaleInitializationCancellation(
          controller.signal.aborted,
          generation,
          runGenerationRef.current,
        )) return;
        updateTransition({ key: targetKey, status: "error" });
        const reason = isAbortError(error)
          ? "会话请求被意外中止，请重试"
          : (error as Error).message;
        if (hadDisplayedRun) {
          setInitializationIssue({
            owner: "transition",
            generation,
            targetKey,
            message: `会话切换失败：${reason}；仍显示上一会话。`,
          });
        } else {
          setConn("error");
          setInitializationIssue({
            owner: "transition",
            generation,
            targetKey,
            message: `会话状态恢复失败：${reason}`,
          });
        }
      }
    })();

    return () => controller.abort();
  }, [
    instanceId,
    explicitRunId,
    sessionGeneration,
    retryGeneration,
    targetKey,
    applyStateSnapshot,
    openSse,
    demo,
    updateTransition,
  ]);

  // 只有 AppShell 真正卸载时才关闭当前会话；目标切换由成功后的原子交换负责。
  useEffect(() => () => {
    runGenerationRef.current += 1;
    refreshRequestRef.current?.abort();
    activityRequestRef.current?.abort();
    sseHandleRef.current?.close();
    sseHandleRef.current = null;
    activeRunRef.current = null;
    activeInstanceRef.current = "";
  }, []);

  const railModel = useMemo(() => projectRailModel(activityState), [activityState]);
  // 第六节点「恢复执行」：从活动事件投影（去重/时序/切会话清理全部复用活动流语义），
  // 不改 handleOpenOpsEvent 既有分支。
  const recovery = useMemo(
    () => deriveRecoveryState(activityState.events, {
      boardTaskId: rcaBoardId,
      pendingApprovals: pendingApprovalRows,
    }),
    [activityState.events, rcaBoardId, pendingApprovalRows],
  );
  const hasUnifiedActivity = railModel.events.length > 0 || railModel.rounds.length > 0;
  useEffect(() => {
    activityRequestRef.current = resetActivityPageRequest(
      activityRequestRef.current,
      setLoadingEarlier,
    );
  }, [runId]);
  const loadEarlier = useCallback(async () => {
    const activityRunId = runId ?? (API_MODE === "mock" ? "run_demo" : null);
    if (!activityRunId || loadingEarlier || !activityState.hasMore || !activityState.nextCursor) return;
    activityRequestRef.current?.abort();
    const controller = new AbortController();
    activityRequestRef.current = controller;
    setLoadingEarlier(true);
    try {
      const page = await api.getActivityEvents(activityRunId, {
        before: activityState.nextCursor,
        limit: 100,
        signal: controller.signal,
      });
      if (controller.signal.aborted || (API_MODE === "real" && activeRunRef.current !== activityRunId)) return;
      dispatchActivity({ type: "prepend_page", page });
    } catch (error) {
      if (!isAbortError(error)) console.warn("[OpenOps][activity] 加载更早事件失败", error);
    } finally {
      if (activityRequestRef.current === controller) {
        activityRequestRef.current = null;
        setLoadingEarlier(false);
      }
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
      // 诊断两段式推进：发送即回到进行中变体，900ms 后 mockRcaFinal() 定格闭环
      // ——mock/e2e 可完整演示「实时步骤 → 五步全绿定格」全程。
      if (demo.rca) setRca(demo.rca);
      setTimeout(() => {
        setMessages((m) => [...m, { id: `b${m.length}`, role: "bot", text: "（mock 演示）任务已受理。", showCopy: true }]);
        setTaskStatus("completed");
        if (demo.rca) setRca(mockRcaFinal());
        // 恢复闭环合成事件：demo 待审批 appr_1 → 已批准 → 工具执行成功，第六节点全程演示
        // 「待审批 → 已执行」（事件 id 固定，活动流按 event_id 去重，重复发送幂等）。
        dispatchActivity({ type: "merge_events", events: mockRecoveryClosureEvents(), source: "live" });
      }, 900);
      return;
    }
    if (!runId) return;  // 上方两分支已保证非空；此守卫仅为类型收窄
    const actionRunId = runId;
    setHitl(undefined);
    if (TRANSPORT === "agui") {
      // B5：任务经 AG-UI 端点启动并流式接收（标准事件→对话区；CUSTOM→同一 openops 处理器）
      setTaskStatus("running");
      const streamRunId = actionRunId;
      const streamToken = Symbol(streamRunId);
      aguiActive.current = { runId: streamRunId, token: streamToken };
      const ownsVisibleRun = () => isCurrentRunStream(
        activeRunRef.current,
        streamRunId,
        aguiActive.current?.token ?? null,
        streamToken,
      );
      runAguiTask(streamRunId, text, {
        onOpenOps: handleOpenOpsEvent,
        onAssistantDelta: (messageId, delta) => {
          if (ownsVisibleRun()) streamDelta(messageId, delta);
        },
        onDone: () => {
          const ownsStream = aguiActive.current?.token === streamToken;
          if (ownsStream) aguiActive.current = null;
          if (activeRunRef.current === streamRunId) void refresh(streamRunId, effectiveInstanceId);
        },
        onError: (msg) => {
          if (!ownsVisibleRun()) return;
          aguiActive.current = null;
          setTaskStatus((s) => (s === "running" ? "failed" : s));
          appendMessage({ id: `e${Date.now()}`, role: "bot", text: `任务异常：${msg}` });
        },
      });
      return;
    }
    try {
      const r = await api.startTask(actionRunId, text);
      if (!isCurrentRunAction(activeRunRef.current, actionRunId)) return;
      setTaskId(r.task_id);
      if (r.status === "queued") {        // 名额满：显示排队条，轮到时 SSE 推 task.started
        setTaskStatus("queued");
        setQueuePosition(r.queue_position ?? 1);
      } else {
        setTaskStatus("running");
        setQueuePosition(null);
      }
    } catch (err) {
      if (!isCurrentRunAction(activeRunRef.current, actionRunId)) return;
      setMessages((m) => [...m, { id: `e${m.length}`, role: "bot", text: `无法启动任务：${(err as Error).message}` }]);
    }
  };

  // 外链自动发送（自建渲染/mock 路径；copilot 路径由面板内 CopilotAutoSend 负责，勿双发）。
  // 就绪门槛：mock=demo 分支挂载完（conn open）；real=runId 就绪。ref 幂等防 StrictMode/重渲染重发。
  useEffect(() => {
    if (!autoQuestion || autoSentRef.current) return;
    if (runStatus === "closed") return;
    if (USE_COPILOT_CHAT && runId) return;
    const ready = API_MODE !== "real"
      ? conn === "open"
      : Boolean(runId) && !workbenchInputBlocked;
    if (!ready) return;
    autoSentRef.current = true;
    console.info("[autosend] sending via composer path");
    const q = autoQuestion;
    setAutoQuestion(null);
    void send(q);
    // send 每次渲染重建且仅作动作不作触发条件，不进依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoQuestion, runId, runStatus, conn, workbenchInputBlocked]);

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

  /** 诊断时间线按钮 → 以用户身份发消息（可见可审计，用户拍板）。copilot 路径经 pendingSend
   *  状态桥（右栏在 CopilotKit Provider 外，必须经 CopilotProgrammaticSend 转发）；
   *  自建/mock 路径直接 send()。按钮层已有禁用态，这里再兜一层守卫防竞态双发。 */
  const requestAgentSend = (message: string) => {
    if (runStatus === "closed" || running || pendingSend) return;
    if (USE_COPILOT_CHAT && runId) {
      setPendingSend({ text: message, nonce: newSendNonce() });
      return;
    }
    void send(message);
  };

  // pendingSend 兜底超时：copilot 连接非 ready 时 sender 根本不挂载（挂载后自身有 10s 放弃），
  // 不清除的话卡片按钮会在本会话内永久禁用。取 12s，略大于 sender 自身超时，挂载路径优先生效。
  useEffect(() => {
    if (!pendingSend) return;
    const nonce = pendingSend.nonce;
    const timer = window.setTimeout(
      () => setPendingSend((cur) => (cur?.nonce === nonce ? null : cur)),
      12_000,
    );
    return () => window.clearTimeout(timer);
  }, [pendingSend]);

  // 拖拽调宽：clamp 到 [420, 760] 并持久化（clamp 与存储一处收口，手柄只报原始位置）。
  const handleRailResize = useCallback((width: number) => {
    const clamped = clampRailWidth(width);
    setRailWidth(clamped);
    saveRailWidth(clamped);
  }, []);

  // 标题=会话名（real：run_title，未起名「新对话」；mock 保持 demo 文案）；徽标=真实 Agent 名
  const displayTitle = chatTitle ?? (API_MODE === "real" ? "新对话" : demo.chatTitle);
  const displayAgent = API_MODE === "real"
    ? agentName ?? agents.find((a) => a.instance_id === effectiveInstanceId)?.name ??
      (initializationError ? "Agent 信息读取失败" : "正在读取 Agent…")
    : demo.agentName;
  const retryRecovery = () => {
    setInitializationIssue(null);
    if (!activeRunRef.current) setConn("connecting");
    setRetryGeneration((generation) => generation + 1);
  };
  const commitTitle = () => {
    const t = titleDraft.split(/\s+/).filter(Boolean).join(" ").slice(0, 60);
    setEditingTitle(false);
    if (!t || !runId || t === chatTitle) return;
    const actionRunId = runId;
    const prev = chatTitle;
    setChatTitle(t);
    api.renameRun(actionRunId, t).catch((e) => {
      if (!isCurrentRunAction(activeRunRef.current, actionRunId)) return;
      setChatTitle(prev);
      alert(`重命名失败：${(e as Error).message}`);
    });
  };
  const closeCurrentRun = () => {
    if (!runId) return;
    const actionRunId = runId;
    void api.closeRun(actionRunId).then(() => {
      if (isCurrentRunAction(activeRunRef.current, actionRunId)) setRunStatus("closed");
    });
  };

  // 新 URL 恢复失败时页面仍展示上一 Run，但任何输入都必须保持禁用；否则用户会在
  // 侧栏高亮新会话的同时，把操作误发到旧 thread。

  /** 名额满时的排队条：位次由后端经 SSE 实时下推，轮到自动开始（不必重发）。
   *
   *  ⚠ **必须两条渲染路径都挂**：真实产品走 copilotChat（CopilotChatPanel），fallbackChat 只在
   *  `VITE_OPENOPS_COPILOT_CHAT=0` 或 mock 下出现。只挂 fallback 的话，线上永远看不见这条。
   *  两边共用同一份状态——`handleOpenOpsEvent` 是共享的（copilot 路径经 onOpenOps 传入），
   *  agui 侧同样调 start_task，队列事件照常以 CUSTOM 透传。 */
  const queueBanner = taskStatus === "queued" && queuePosition !== null ? (
    <div data-testid="queue-banner" style={{
      display: "flex", alignItems: "center", gap: 10, margin: "0 24px 8px",
      padding: "8px 12px", borderRadius: radius.lg,
      background: color.warningBg, border: `1px solid ${color.warningBorder}`,
      fontSize: 12.5, color: color.warningText,
    }}>
      <Icon name="hourglass" size={14} color={color.warningText} />
      <span style={{ flex: 1 }}>
        正在排队，前面还有 <b>{Math.max(0, queuePosition - 1)}</b> 个——轮到你时会自动开始，不用重发。
      </span>
      <button onClick={() => { if (taskId) void api.cancelTask(taskId); }}
        style={{ border: "none", background: "transparent", color: color.warningText,
                 fontSize: 12.5, fontWeight: 600, cursor: "pointer", padding: 0 }}>
        取消排队
      </button>
    </div>
  ) : null;

  const fallbackChat = (
    <div
      inert={workbenchInputBlocked}
      style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}
    >
      <div className="oa-fallback-chat-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
        <div className="oa-fallback-chat-list" style={{ maxWidth: 760, margin: "0 auto", display: "flex", flexDirection: "column" }}>
          {messages.length === 0 && !running ? (
            <div style={{ textAlign: "center", color: color.textSubtle, fontSize: 13, padding: "40px 0" }}>
              <Icon name="message-2" size={22} color={color.brand} />
              <div style={{ marginTop: 10 }}>描述你的排障任务，Agent 会按「巡检 → 诊断 → 恢复」推进，恢复动作需你确认。</div>
            </div>
          ) : null}
          {messages.map((message) => (message.role === "user"
            ? <UserBubble key={message.id} text={message.text} />
            : <BotBubble key={message.id} text={message.text} showCopy={message.showCopy} />))}
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
        <>
          {queueBanner}
          <Composer skills={skills} onSend={send} modelLabel={modelLabel} />
        </>
      )}
    </div>
  );

  const copilotChat = runId ? (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
      <CopilotChatPanel
        runId={runId}
        instanceId={effectiveInstanceId}
        modelLabel={modelLabel}
        readOnly={runStatus === "closed"}
        blocked={workbenchInputBlocked}
        blockedMessage={failedCurrentTarget ? "目标会话恢复失败，请使用上方“重试”后再继续" : undefined}
        autoQuestion={autoQuestion}
        onAutoSent={() => setAutoQuestion(null)}
        pendingSend={pendingSend}
        onPendingSent={() => setPendingSend(null)}
        onOpenOps={handleOpenOpsEvent}
        onRetryConnection={() => setCopilotConnectionGeneration((generation) => generation + 1)}
      />
      {/* 面板 flex:1 → 排队条挂在其后即贴着输入框下沿（用户刚打完字，视线就在这儿） */}
      {queueBanner}
      {runStatus === "active" && shouldShowWorkbenchPortal(visible, workbenchInputBlocked)
        ? <CopilotHitlFloat hitl={hitl} onDecide={resolveHitl} />
        : null}
    </div>
  ) : fallbackChat;

  const copilotProviderKey = `${runId ?? targetKey}:${copilotConnectionGeneration}`;
  const chatSurface = USE_COPILOT_CHAT && runId ? (
    <CopilotErrorBoundary resetKey={copilotProviderKey}>
      <CopilotWorkbenchProvider key={copilotProviderKey}>{copilotChat}</CopilotWorkbenchProvider>
    </CopilotErrorBoundary>
  ) : fallbackChat;
  return (
    <div
      data-testid="workbench-session"
      data-run-id={runId ?? ""}
      style={{ position: "relative", flex: 1, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column" }}
    >
      <div
        inert={workbenchInputBlocked}
        style={{ flex: 1, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column" }}
      >
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
          <Button variant="ghost" icon="lock" onClick={closeCurrentRun}>关闭会话</Button>
        ) : null}
        <IconButton icon="timeline-event" title="活动栏" active={activityOpen} onClick={() => setActivityOpen((v) => !v)} />
        <IconButton icon="list-check" title="服务状态" active={statusOpen} onClick={() => setStatusOpen((v) => !v)} />
      </header>

      {/* 自带模型降级：样式同下方初始化告警，但动作是「去改绑」而非「重试」——降级不是可重试的
          错误，重试只会再降级一次。关闭仅收起本次提示，不改任何配置。 */}
      {modelDegraded ? (
        <div
          role="alert"
          style={{
            flex: "0 0 auto",
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "9px 20px",
            borderBottom: `1px solid ${color.border}`,
            background: toneColor.warning.bg,
            color: toneColor.warning.text,
            fontSize: 12.5,
          }}
        >
          <Icon name="alert-triangle" size={15} />
          <span style={{ flex: 1 }}>{modelDegraded}</span>
          {effectiveInstanceId ? (
            <Button variant="ghost" onClick={() => nav(`/agent-teams/${effectiveInstanceId}/edit`)}>
              重新选择模型
            </Button>
          ) : null}
          <IconButton icon="x" title="关闭提示" onClick={() => setModelDegraded(null)} />
        </div>
      ) : null}

      {initializationError ? (
        <div
          role="alert"
          style={{
            flex: "0 0 auto",
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "9px 20px",
            borderBottom: `1px solid ${color.border}`,
            background: toneColor.warning.bg,
            color: toneColor.warning.text,
            fontSize: 12.5,
          }}
        >
          <span style={{ flex: 1 }}>{initializationError}</span>
          <Button variant="ghost" onClick={retryRecovery}>重试</Button>
        </div>
      ) : null}

      {statusOpen ? (
        <div style={{ flex: "0 0 auto", borderBottom: `1px solid ${color.border}`, background: color.surfaceAlt, padding: "12px 20px", display: "flex", flexWrap: "wrap", gap: 10 }}>
          {(API_MODE === "real" ? buildStatusChips(conn, wsStatus, mcpStat) : demo.statusChips).map((s) => (
            <span key={s.key} style={{ display: "inline-flex", flexWrap: "wrap", alignItems: "center", gap: 7, maxWidth: "100%", fontSize: 12, fontWeight: 600, color: color.textBody, background: "#fff", border: `1px solid ${color.border}`, padding: "6px 11px", borderRadius: radius.md }}>
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: toneColor[s.tone].dot }} />{s.label}<span style={{ color: color.textSubtle, fontWeight: 500, whiteSpace: "normal", wordBreak: "break-word" }}>{s.value}</span>
            </span>
          ))}
        </div>
      ) : null}


      <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
        {chatSurface}
        {activityOpen ? (
          <ActivityRail
            key={runId ?? explicitRunId ?? "demo"}
            groups={API_MODE === "real" ? [] : groupNodes(nodes, running)}
            rounds={hasUnifiedActivity ? railModel.rounds : undefined}
            hasMore={hasUnifiedActivity && railModel.hasMore}
            loadingMore={loadingEarlier}
            onLoadMore={hasUnifiedActivity ? loadEarlier : undefined}
            rca={rca}
            rcaLive={running && rca?.status !== "concluded"}
            rcaActionsEnabled={!running && !pendingSend && !workbenchInputBlocked}
            onRcaAction={runStatus === "closed" ? undefined : (action) => requestAgentSend(action.message)}
            recovery={recovery}
            width={railWidth}
            onResize={handleRailResize}
          />
        ) : null}
      </div>
      </div>
      {showSwitchOverlay ? (
        <div
          data-testid="workbench-switch-overlay"
          role="status"
          aria-live="polite"
          style={{
            position: "absolute",
            inset: 0,
            zIndex: 80,
            display: "grid",
            placeItems: "center",
            pointerEvents: "auto",
            background: "rgba(247, 248, 250, 0.58)",
            backdropFilter: "blur(1px)",
          }}
        >
          <div style={{ display: "inline-flex", alignItems: "center", gap: 8, padding: "8px 12px", border: `1px solid ${color.border}`, borderRadius: radius.pill, background: "rgba(255,255,255,.94)", color: color.textBody, fontSize: 12.5, boxShadow: "0 6px 20px rgba(31,38,51,.08)" }}>
            <Icon name="loader-2" size={14} color={color.brand} spin />正在切换到最后选择的会话…
          </div>
        </div>
      ) : null}
      {showFailureOverlay ? (
        <div
          data-testid="workbench-failure-overlay"
          role="alert"
          style={{
            position: "absolute",
            inset: 0,
            zIndex: 90,
            display: "grid",
            placeItems: "center",
            pointerEvents: "auto",
            background: "rgba(247, 248, 250, 0.74)",
            backdropFilter: "blur(1px)",
          }}
        >
          <div style={{ display: "grid", justifyItems: "center", gap: 10, maxWidth: 460, padding: "18px 20px", border: `1px solid ${color.border}`, borderRadius: radius.xl, background: "rgba(255,255,255,.97)", color: color.textBody, fontSize: 12.5, textAlign: "center", boxShadow: "0 10px 30px rgba(31,38,51,.10)" }}>
            <Icon name="alert-triangle" size={18} color={toneColor.warning.dot} />
            <span>{initializationError ?? "目标会话恢复失败；当前仅保留上一会话只读画面。"}</span>
            <Button variant="secondary" onClick={retryRecovery}>重试目标会话</Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** 服务状态面板 4 chip 的真值组装（real 模式）：AG-UI 连接态 / MCP 装配数 / oModel 同步态 / 范围 APPID。 */
function buildStatusChips(
  conn: ConnState,
  wsStatus: { sync_status: string; app_ids: string[] } | null,
  mcpStat: { active: number; total: number } | null,
): StatusChip[] {
  const agui: Record<ConnState, { value: string; tone: Tone }> = {
    open: { value: "已连接", tone: "good" },
    connecting: { value: "连接中", tone: "neutral" },
    reconnecting: { value: "重连中", tone: "warning" },
    error: { value: "连接异常", tone: "danger" },
  };
  const syncMap: Record<string, { value: string; tone: Tone }> = {
    ready: { value: "已同步", tone: "good" },
    syncing: { value: "同步中", tone: "warning" },
    failed: { value: "同步失败", tone: "danger" },
  };
  const sync = wsStatus
    ? (syncMap[wsStatus.sync_status] ?? { value: wsStatus.sync_status, tone: "neutral" as Tone })
    : { value: "…", tone: "neutral" as Tone };
  const apps = wsStatus?.app_ids ?? [];
  // 不截断：APPID 过多时由 chip 内部换行展示全部（见渲染处 whiteSpace/wordBreak）；空格分隔便于自然折行。
  const scopeValue = apps.length ? apps.join(" / ") : (wsStatus ? "无" : "…");
  return [
    { key: "agui", label: "AG-UI", value: agui[conn].value, tone: agui[conn].tone },
    { key: "mcp", label: "MCP 服务", value: mcpStat ? `${mcpStat.active}/${mcpStat.total} · active` : "…",
      tone: mcpStat ? (mcpStat.active > 0 ? "good" : "warning") : "neutral" },
    { key: "omodel", label: "OModel", value: sync.value, tone: sync.tone },
    { key: "scope", label: "范围", value: scopeValue, tone: apps.length ? "good" : "neutral" },
  ];
}

function ConnBadge({ conn, closed }: { conn: ConnState; closed: boolean }) {
  if (closed) {
    return <span style={{ fontSize: 11.5, fontWeight: 600, color: color.textSubtle }}>已关闭</span>;
  }
  const map: Record<ConnState, { label: string; tone: "good" | "warning" | "neutral" }> = {
    open: { label: "实时", tone: "good" },
    connecting: { label: "连接中", tone: "neutral" },
    reconnecting: { label: "重连中", tone: "warning" },
    error: { label: "恢复失败", tone: "warning" },
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
