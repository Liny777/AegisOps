import {
  Activity,
  AlertTriangle,
  Bot,
  Boxes,
  CheckCircle2,
  ChevronRight,
  Database,
  FileText,
  KeyRound,
  Lock,
  Plus,
  RefreshCw,
  Send,
  Server,
  Settings,
  ShieldCheck,
  Users,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

type Role = "user" | "platform_admin";
type RuntimeState =
  | "loading"
  | "ready_no_task"
  | "task_running"
  | "approval_pending"
  | "task_cancelled"
  | "task_failed"
  | "run_closed"
  | "agui_reconnecting"
  | "scope_blocked"
  | "model_unavailable";

type Instance = {
  id: string;
  name: string;
  templateName: string;
  workspaceId: string;
  scopeRevision: string;
  activeConfigVersion: number;
  status: "active" | "disabled";
  mainRoleAppend: string;
  llmConfigId: string;
  boundSkills: string[];
  boundMcps: string[];
};

type Run = {
  id: string;
  instanceId: string;
  status: "active" | "closed";
  selectedModel: string;
  activeTask?: { id: string; status: RuntimeState; prompt: string };
  messages: { role: "user" | "assistant"; content: string }[];
  approvals: Approval[];
  timeline: TimelineItem[];
  auditTraceId: string;
};

type Approval = {
  id: string;
  toolName: string;
  actionSummary: string;
  appids: string[];
  impact: string;
  decision: "pending" | "approved" | "rejected" | "cancelled";
};

type TimelineItem = {
  type: string;
  title: string;
  detail: string;
  time: string;
};

const models = [
  { id: "llm_platform_default", label: "平台默认模型", meta: "128K / tool calling" },
  { id: "llm_user_gpt4o", label: "我的兼容模型", meta: "64K / streaming" },
  { id: "llm_user_backup", label: "备用模型", meta: "32K / 低峰期" },
];

const skills = [
  { id: "skill_inspection", name: "应用巡检", source: "平台", agent: "巡检子 Agent", enabled: true },
  { id: "skill_diagnose", name: "异常定界", source: "平台", agent: "定界子 Agent", enabled: true },
  { id: "skill_recover", name: "恢复预案", source: "平台", agent: "恢复子 Agent", enabled: true },
  { id: "skill_user_logscan", name: "自定义日志扫描", source: "用户", agent: "main", enabled: true },
];

const mcps = [
  { id: "mcp_omodel_query", name: "oModel 查询 MCP", source: "平台", transport: "http", enabled: true },
  { id: "mcp_recovery", name: "恢复工具 MCP", source: "平台", transport: "http", enabled: true },
  { id: "mcp_user_metrics", name: "用户指标 HTTP MCP", source: "用户", transport: "http", enabled: true },
];

const initialInstance: Instance = {
  id: "agt_pay_fast_recovery",
  name: "支付域感知快恢",
  templateName: "感知快恢 Agent 模板",
  workspaceId: "ws_pay_abc",
  scopeRevision: "rev-20260708-001",
  activeConfigVersion: 1,
  status: "active",
  mainRoleAppend: "优先说明影响范围，再给出最小恢复动作建议。",
  llmConfigId: "llm_platform_default",
  boundSkills: ["skill_user_logscan"],
  boundMcps: ["mcp_user_metrics"],
};

function nowLabel() {
  return new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function makeRun(instanceId: string): Run {
  return {
    id: `run_${Math.random().toString(16).slice(2, 10)}`,
    instanceId,
    status: "active",
    selectedModel: "llm_platform_default",
    auditTraceId: `trace_${Math.random().toString(16).slice(2, 10)}`,
    messages: [
      {
        role: "assistant",
        content: "我已加载当前 AgentTeam 的工作范围、模板能力和实例配置。可以直接发起巡检、定界或恢复前确认。",
      },
    ],
    approvals: [],
    timeline: [
      {
        type: "openops.run.created",
        title: "Run 已创建",
        detail: "AgentScope session 已由 mock runtime 准备",
        time: nowLabel(),
      },
    ],
  };
}

function getPath() {
  return window.location.pathname;
}

function Pill({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "green" | "amber" | "red" | "blue" }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

function SectionTitle({ icon, title, desc }: { icon: React.ReactNode; title: string; desc?: string }) {
  return (
    <div className="section-title">
      <span className="section-icon">{icon}</span>
      <div>
        <h2>{title}</h2>
        {desc ? <p>{desc}</p> : null}
      </div>
    </div>
  );
}

export default function App() {
  const [path, setPath] = useState(getPath());
  const [role, setRole] = useState<Role>("user");
  const [whitelisted, setWhitelisted] = useState(true);
  const [instances, setInstances] = useState<Instance[]>([initialInstance]);
  const [runs, setRuns] = useState<Record<string, Run>>({});
  const [draftPrompt, setDraftPrompt] = useState("巡检 APP-A 最近 30 分钟的错误率和依赖变化");
  const [slashOpen, setSlashOpen] = useState(false);
  const [adminTab, setAdminTab] = useState("templates");
  const [sandboxLimit, setSandboxLimit] = useState(26);

  useEffect(() => {
    const onPop = () => setPath(getPath());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  function navigate(next: string) {
    window.history.pushState({}, "", next);
    setPath(next);
  }

  const currentInstance = useMemo(() => {
    const instanceId = path.match(/^\/agent-teams\/([^/]+)/)?.[1];
    const runId = path.match(/^\/agent-runs\/([^/]+)/)?.[1];
    const run = runId ? runs[runId] : undefined;
    return instances.find((item) => item.id === (instanceId || run?.instanceId)) || instances[0];
  }, [instances, path, runs]);

  const currentRun = useMemo(() => {
    const runId = path.match(/^\/agent-runs\/([^/]+)/)?.[1];
    if (runId && runs[runId]) return runs[runId];
    return Object.values(runs).find((run) => run.instanceId === currentInstance?.id && run.status === "active");
  }, [currentInstance?.id, path, runs]);

  function ensureRun(instanceId: string) {
    const existing = Object.values(runs).find((run) => run.instanceId === instanceId && run.status === "active");
    if (existing) return existing;
    const run = makeRun(instanceId);
    setRuns((prev) => ({ ...prev, [run.id]: run }));
    return run;
  }

  function startTask() {
    if (!currentInstance || !draftPrompt.trim()) return;
    const run = currentRun || ensureRun(currentInstance.id);
    const taskId = `task_${Math.random().toString(16).slice(2, 10)}`;
    const approval: Approval = {
      id: `apr_${Math.random().toString(16).slice(2, 10)}`,
      toolName: "recover_execute",
      actionSummary: "对 APP-A 执行单实例恢复预案",
      appids: ["APP-A"],
      impact: "可能重启一个支付应用实例，预计影响 20 秒以内。",
      decision: "pending",
    };
    setRuns((prev) => ({
      ...prev,
      [run.id]: {
        ...run,
        activeTask: { id: taskId, status: "approval_pending", prompt: draftPrompt },
        approvals: [approval],
        messages: [
          ...run.messages,
          { role: "user", content: draftPrompt },
          { role: "assistant", content: "已完成范围解析，发现 APP-A 需要执行恢复前确认。请在确认卡中处理。" },
        ],
        timeline: [
          ...run.timeline,
          { type: "openops.task.started", title: "Task 已启动", detail: draftPrompt, time: nowLabel() },
          { type: "openops.scope.resolved", title: "工作范围已解析", detail: "effective_appids=APP-A, APP-B, APP-C", time: nowLabel() },
          { type: "openops.approval.required", title: "等待 ASK 确认", detail: "recover_execute 需要用户确认", time: nowLabel() },
        ],
      },
    }));
    navigate(`/agent-runs/${run.id}`);
  }

  function decideApproval(decision: "approved" | "rejected") {
    if (!currentRun || !currentRun.approvals[0]) return;
    const approval = { ...currentRun.approvals[0], decision };
    const completed = decision === "approved";
    setRuns((prev) => ({
      ...prev,
      [currentRun.id]: {
        ...currentRun,
        approvals: [approval],
        activeTask: currentRun.activeTask
          ? { ...currentRun.activeTask, status: completed ? "ready_no_task" : "task_failed" }
          : undefined,
        messages: [
          ...currentRun.messages,
          {
            role: "assistant",
            content: completed
              ? "审批通过。恢复类 MCP 返回 execution_id=exec_20260708_001，当前任务已收口。"
              : "你已拒绝本次工具调用，当前任务停止在恢复前确认阶段。",
          },
        ],
        timeline: [
          ...currentRun.timeline,
          {
            type: completed ? "openops.tool.call.succeeded" : "openops.approval.rejected",
            title: completed ? "恢复工具调用成功" : "ASK 已拒绝",
            detail: completed ? "execution_id=exec_20260708_001" : "未调用外部 MCP",
            time: nowLabel(),
          },
        ],
      },
    }));
  }

  function cancelTask() {
    if (!currentRun?.activeTask) return;
    setRuns((prev) => ({
      ...prev,
      [currentRun.id]: {
        ...currentRun,
        activeTask: { ...currentRun.activeTask!, status: "task_cancelled" },
        approvals: currentRun.approvals.map((item) => (item.decision === "pending" ? { ...item, decision: "cancelled" } : item)),
        messages: [...currentRun.messages, { role: "assistant", content: "当前 task 已取消。Run 仍保持打开，可以继续提交新任务。" }],
        timeline: [
          ...currentRun.timeline,
          { type: "openops.task.cancelled", title: "Task 已取消", detail: "Run 保持 active，可继续对话", time: nowLabel() },
        ],
      },
    }));
  }

  function closeRun() {
    if (!currentRun) return;
    setRuns((prev) => ({
      ...prev,
      [currentRun.id]: {
        ...currentRun,
        status: "closed",
        activeTask: currentRun.activeTask ? { ...currentRun.activeTask, status: "run_closed" } : undefined,
        timeline: [...currentRun.timeline, { type: "openops.run.closed", title: "Run 已关闭", detail: "不再接受新 task", time: nowLabel() }],
      },
    }));
  }

  function saveSettings(update: Partial<Instance>) {
    if (!currentInstance) return;
    setInstances((prev) =>
      prev.map((item) =>
        item.id === currentInstance.id
          ? { ...item, ...update, activeConfigVersion: item.activeConfigVersion + 1 }
          : item,
      ),
    );
  }

  if (!whitelisted && path !== "/not-whitelisted") {
    return <Shell path={path} role={role} whitelisted={whitelisted} setRole={setRole} setWhitelisted={setWhitelisted} navigate={navigate}><NotWhitelisted /></Shell>;
  }

  let page: React.ReactNode;
  if (path === "/not-whitelisted") page = <NotWhitelisted />;
  else if (path === "/init") page = <InitWizard navigate={navigate} setInstances={setInstances} />;
  else if (path.includes("/settings")) page = currentInstance ? <SettingsPage instance={currentInstance} saveSettings={saveSettings} navigate={navigate} /> : <EmptyState />;
  else if (path.includes("/audit")) page = currentRun ? <AuditPage run={currentRun} /> : <EmptyState />;
  else if (path.startsWith("/admin")) page = role === "platform_admin" ? <AdminConsole tab={adminTab} setTab={setAdminTab} sandboxLimit={sandboxLimit} setSandboxLimit={setSandboxLimit} /> : <Forbidden />;
  else if (path.startsWith("/agent-teams") || path.startsWith("/agent-runs")) {
    page = currentInstance ? (
      currentRun ? (
        <Workbench
          instance={currentInstance}
          run={currentRun}
          draftPrompt={draftPrompt}
          setDraftPrompt={setDraftPrompt}
          slashOpen={slashOpen}
          setSlashOpen={setSlashOpen}
          navigate={navigate}
          startTask={startTask}
          decideApproval={decideApproval}
          cancelTask={cancelTask}
          closeRun={closeRun}
          selectModel={(modelId) => {
            setRuns((prev) => ({
              ...prev,
              [currentRun.id]: {
                ...currentRun,
                selectedModel: modelId,
                timeline: [
                  ...currentRun.timeline,
                  { type: "openops.model.selected", title: "Run 模型已切换", detail: modelId, time: nowLabel() },
                ],
              },
            }));
          }}
        />
      ) : (
        <StartRunPage
          instance={currentInstance}
          start={() => {
            const run = ensureRun(currentInstance.id);
            navigate(`/agent-runs/${run.id}`);
          }}
        />
      )
    ) : (
      <EmptyState />
    );
  } else {
    page = (
      <Home
        role={role}
        instances={instances}
        navigate={navigate}
        ensureRun={(instanceId) => {
          const run = ensureRun(instanceId);
          navigate(`/agent-runs/${run.id}`);
        }}
      />
    );
  }

  return (
    <Shell path={path} role={role} whitelisted={whitelisted} setRole={setRole} setWhitelisted={setWhitelisted} navigate={navigate}>
      {page}
    </Shell>
  );
}

function Shell({
  children,
  role,
  whitelisted,
  setRole,
  setWhitelisted,
  navigate,
}: {
  children: React.ReactNode;
  path: string;
  role: Role;
  whitelisted: boolean;
  setRole: (role: Role) => void;
  setWhitelisted: (value: boolean) => void;
  navigate: (path: string) => void;
}) {
  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" onClick={() => navigate("/")}>
          <span className="brand-mark">O</span>
          <span>
            <b>OpenOps V1</b>
            <small>SRE Agent Platform</small>
          </span>
        </button>
        <div className="topbar-actions">
          <select value={role} onChange={(event) => setRole(event.target.value as Role)} aria-label="mock role">
            <option value="user">普通用户</option>
            <option value="platform_admin">平台管理员</option>
          </select>
          <button className={whitelisted ? "ghost active" : "ghost"} onClick={() => setWhitelisted(!whitelisted)}>
            {whitelisted ? "白名单已开" : "未白名单"}
          </button>
        </div>
      </header>
      {children}
    </div>
  );
}

function Home({
  role,
  instances,
  navigate,
  ensureRun,
}: {
  role: Role;
  instances: Instance[];
  navigate: (path: string) => void;
  ensureRun: (instanceId: string) => void;
}) {
  return (
    <main className="home-grid">
      <section className="hero-panel">
        <Pill tone="blue">V1 原型</Pill>
        <h1>专业 SRE Agent 工作台</h1>
        <p>围绕企业工作范围、平台模板、main 实例覆盖、ASK 审批和审计回放构建。</p>
        <div className="hero-actions">
          {instances[0] ? <button className="primary" onClick={() => ensureRun(instances[0].id)}>进入工作台</button> : null}
          <button className="secondary" onClick={() => navigate("/init")}>初始化 AgentTeam</button>
          {role === "platform_admin" ? <button className="secondary" onClick={() => navigate("/admin/templates")}>进入管理台</button> : null}
        </div>
      </section>
      <section className="status-board">
        <Metric label="活跃实例" value={String(instances.length)} tone="blue" />
        <Metric label="可用平台工具" value="8" tone="green" />
        <Metric label="用户容器上限" value="26" tone="amber" />
        <Metric label="审计保留" value="30天" tone="neutral" />
      </section>
    </main>
  );
}

function StartRunPage({ instance, start }: { instance: Instance; start: () => void }) {
  return (
    <main className="center-page">
      <Bot size={36} />
      <h1>{instance.name}</h1>
      <p>当前实例尚未打开 Run。创建 Run 后进入 CopilotKit + AG-UI 工作台。</p>
      <button className="primary" onClick={start}>创建 Run 并进入</button>
    </main>
  );
}

function InitWizard({ navigate, setInstances }: { navigate: (path: string) => void; setInstances: React.Dispatch<React.SetStateAction<Instance[]>> }) {
  const [step, setStep] = useState(1);
  const [name, setName] = useState("新的感知快恢 AgentTeam");
  const [workspace, setWorkspace] = useState("ws_pay_abc");
  const [llm, setLlm] = useState("llm_platform_default");
  const steps = ["模板", "名称", "workspace", "LLM", "激活"];

  function activate() {
    const id = `agt_${Math.random().toString(16).slice(2, 8)}`;
    const instance: Instance = {
      ...initialInstance,
      id,
      name,
      workspaceId: workspace,
      llmConfigId: llm,
      activeConfigVersion: 1,
    };
    setInstances((prev) => [instance, ...prev]);
    navigate(`/agent-teams/${id}/chat`);
  }

  return (
    <main className="page">
      <SectionTitle icon={<Bot size={20} />} title="初始化向导" desc="V1 只包含模板、名称、workspace、LLM 和激活。" />
      <div className="wizard">
        <aside className="stepper">
          {steps.map((item, index) => (
            <button key={item} className={step === index + 1 ? "step active" : "step"} onClick={() => setStep(index + 1)}>
              <span>{index + 1}</span>
              {item}
            </button>
          ))}
        </aside>
        <section className="panel">
          {step === 1 ? (
            <FormBlock title="选择平台模板" desc="普通用户只能基于平台管理员发布的模板实例化。">
              <div className="choice selected">
                <Bot size={20} />
                <div>
                  <b>感知快恢 Agent 模板</b>
                  <p>main 调度巡检、定界、恢复子 Agent；sub agent 配置只读。</p>
                </div>
              </div>
            </FormBlock>
          ) : null}
          {step === 2 ? (
            <FormBlock title="命名 AgentTeam 实例" desc="名称用于工作台与审计回放展示。">
              <label>实例名称<input value={name} onChange={(event) => setName(event.target.value)} /></label>
            </FormBlock>
          ) : null}
          {step === 3 ? (
            <FormBlock title="绑定 workspace" desc="workspace_id 是唯一工作范围标识，APPID 范围由 oModel 解析。">
              <select value={workspace} onChange={(event) => setWorkspace(event.target.value)}>
                <option value="ws_pay_abc">ws_pay_abc / APP-A、APP-B、APP-C</option>
                <option value="ws_trade_cd">ws_trade_cd / APP-C、APP-D</option>
              </select>
              <Pill tone="green">ready</Pill>
            </FormBlock>
          ) : null}
          {step === 4 ? (
            <FormBlock title="选择运行模型" desc="实例级 LLM 覆盖会被 main/sub 共同使用。">
              <select value={llm} onChange={(event) => setLlm(event.target.value)}>
                {models.map((model) => (
                  <option key={model.id} value={model.id}>{model.label}</option>
                ))}
              </select>
              <p className="hint">用户自定义模型必须支持 OpenAI-compatible 和 tool calling。</p>
            </FormBlock>
          ) : null}
          {step === 5 ? (
            <FormBlock title="激活实例" desc="激活后进入对话工作台，后续自定义 Skill/MCP 从设置页维护。">
              <div className="summary-list">
                <span>模板：感知快恢 Agent 模板</span>
                <span>名称：{name}</span>
                <span>workspace：{workspace}</span>
                <span>模型：{models.find((item) => item.id === llm)?.label}</span>
              </div>
            </FormBlock>
          ) : null}
          <div className="form-actions">
            <button className="secondary" disabled={step === 1} onClick={() => setStep((prev) => Math.max(1, prev - 1))}>上一步</button>
            {step < 5 ? <button className="primary" onClick={() => setStep((prev) => prev + 1)}>下一步</button> : <button className="primary" onClick={activate}>激活并进入</button>}
          </div>
        </section>
      </div>
    </main>
  );
}

function Workbench({
  instance,
  run,
  draftPrompt,
  setDraftPrompt,
  slashOpen,
  setSlashOpen,
  navigate,
  startTask,
  decideApproval,
  cancelTask,
  closeRun,
  selectModel,
}: {
  instance: Instance;
  run: Run;
  draftPrompt: string;
  setDraftPrompt: (value: string) => void;
  slashOpen: boolean;
  setSlashOpen: (value: boolean) => void;
  navigate: (path: string) => void;
  startTask: () => void;
  decideApproval: (decision: "approved" | "rejected") => void;
  cancelTask: () => void;
  closeRun: () => void;
  selectModel: (modelId: string) => void;
}) {
  const pendingApproval = run.approvals.find((item) => item.decision === "pending");
  const runtimeState: RuntimeState = run.status === "closed" ? "run_closed" : pendingApproval ? "approval_pending" : run.activeTask?.status || "ready_no_task";
  const availableSkills = skills.filter((item) => item.source === "平台" || instance.boundSkills.includes(item.id));

  return (
    <main className="workspace">
      <aside className="workspace-nav">
        <div className="nav-instance">
          <b>{instance.name}</b>
          <span>{instance.workspaceId}</span>
        </div>
        <button onClick={() => navigate(`/agent-teams/${instance.id}/chat`)}><Bot size={16} /> 对话工作台</button>
        <button onClick={() => navigate(`/agent-teams/${instance.id}/settings`)}><Settings size={16} /> 设置</button>
        <button onClick={() => navigate("/init")}><Plus size={16} /> 新建实例</button>
        <button className="disabled"><Database size={16} /> 知识能力 V1 禁用</button>
        <button className="disabled"><Boxes size={16} /> 对象图下版本</button>
      </aside>
      <section className="chat-pane">
        <div className="chat-header">
          <div>
            <h1>对话工作台</h1>
            <p>CopilotKit UI + AG-UI 事件协议，状态事实来自 OpenOps 后端。</p>
          </div>
          <div className="header-controls">
            <select value={run.selectedModel} onChange={(event) => selectModel(event.target.value)} disabled={run.status === "closed"}>
              {models.map((model) => (
                <option key={model.id} value={model.id}>{model.label}</option>
              ))}
            </select>
            <Pill tone={runtimeState === "approval_pending" ? "amber" : runtimeState === "run_closed" ? "red" : "green"}>
              {runtimeState}
            </Pill>
          </div>
        </div>
        <div className="message-list">
          {run.messages.map((message, index) => (
            <div key={`${message.role}-${index}`} className={`message ${message.role}`}>
              <span>{message.role === "assistant" ? "Agent" : "用户"}</span>
              <p>{message.content}</p>
            </div>
          ))}
          <RcaCard />
          {pendingApproval ? <ApprovalCard approval={pendingApproval} approve={() => decideApproval("approved")} reject={() => decideApproval("rejected")} /> : null}
        </div>
        <div className="composer">
          {slashOpen ? (
            <div className="slash-menu">
              {availableSkills.map((skill) => (
                <button key={skill.id} onClick={() => { setDraftPrompt(`/${skill.name} ${draftPrompt.replace(/^\/[^ ]*\s?/, "")}`); setSlashOpen(false); }}>
                  <span>{skill.name}</span>
                  <small>{skill.source} / {skill.agent}</small>
                </button>
              ))}
            </div>
          ) : null}
          <textarea
            value={draftPrompt}
            onChange={(event) => {
              setDraftPrompt(event.target.value);
              setSlashOpen(event.target.value.startsWith("/"));
            }}
            placeholder="输入任务目标，使用 / 选择当前 RuntimePlan 已绑定 Skill"
            disabled={run.status === "closed"}
          />
          <div className="composer-actions">
            <button className="secondary" onClick={cancelTask} disabled={!run.activeTask || run.status === "closed"}>取消 Task</button>
            <button className="secondary" onClick={closeRun} disabled={run.status === "closed"}>关闭 Run</button>
            <button className="primary" onClick={startTask} disabled={run.status === "closed"}><Send size={16} /> 发送</button>
          </div>
        </div>
      </section>
      <aside className="timeline-pane">
        <SectionTitle icon={<Activity size={18} />} title="活动时间线" desc={`audit_trace_id=${run.auditTraceId}`} />
        <div className="timeline">
          {run.timeline.map((item, index) => (
            <div key={`${item.type}-${index}`} className="timeline-item">
              <span>{item.time}</span>
              <b>{item.title}</b>
              <p>{item.detail}</p>
            </div>
          ))}
        </div>
      </aside>
    </main>
  );
}

function SettingsPage({ instance, saveSettings, navigate }: { instance: Instance; saveSettings: (update: Partial<Instance>) => void; navigate: (path: string) => void }) {
  const [roleAppend, setRoleAppend] = useState(instance.mainRoleAppend);
  const [llm, setLlm] = useState(instance.llmConfigId);
  const [boundSkill, setBoundSkill] = useState(instance.boundSkills.includes("skill_user_logscan"));
  const [boundMcp, setBoundMcp] = useState(instance.boundMcps.includes("mcp_user_metrics"));

  return (
    <main className="page">
      <SectionTitle icon={<Settings size={20} />} title="实例设置" desc="这里只维护当前 AgentTeam 实例的 main 覆盖配置。" />
      <div className="settings-grid">
        <section className="panel">
          <h3>实例概览</h3>
          <div className="kv"><span>实例</span><b>{instance.name}</b></div>
          <div className="kv"><span>模板</span><b>{instance.templateName}</b></div>
          <div className="kv"><span>workspace_id</span><b>{instance.workspaceId}</b></div>
          <div className="kv"><span>scope_revision</span><b>{instance.scopeRevision}</b></div>
          <div className="kv"><span>active_config_version</span><b>v{instance.activeConfigVersion}</b></div>
        </section>
        <section className="panel">
          <h3>角色与模型</h3>
          <label>main role append<textarea value={roleAppend} onChange={(event) => setRoleAppend(event.target.value)} /></label>
          <label>Run 默认模型<select value={llm} onChange={(event) => setLlm(event.target.value)}>
            {models.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
          </select></label>
          <p className="hint">sub agent、平台默认 role、平台默认 Skill/MCP 均只读。</p>
          <button className="primary" onClick={() => saveSettings({ mainRoleAppend: roleAppend, llmConfigId: llm })}>保存为新配置版本</button>
        </section>
        <section className="panel">
          <h3>用户 Skill</h3>
          <ReadonlyList items={skills.filter((item) => item.source === "平台").map((item) => `${item.name} / ${item.agent}`)} title="平台默认绑定" />
          <ToggleRow label="自定义日志扫描" checked={boundSkill} onChange={(value) => { setBoundSkill(value); saveSettings({ boundSkills: value ? ["skill_user_logscan"] : [] }); }} />
          <button className="secondary">上传 Python Skill 包</button>
        </section>
        <section className="panel">
          <h3>用户 HTTP MCP</h3>
          <ReadonlyList items={mcps.filter((item) => item.source === "平台").map((item) => item.name)} title="平台默认 MCP" />
          <ToggleRow label="用户指标 HTTP MCP" checked={boundMcp} onChange={(value) => { setBoundMcp(value); saveSettings({ boundMcps: value ? ["mcp_user_metrics"] : [] }); }} />
          <button className="secondary">注册 HTTP MCP</button>
        </section>
        <section className="panel full">
          <h3>配置版本历史</h3>
          <table>
            <tbody>
              {[instance.activeConfigVersion, Math.max(1, instance.activeConfigVersion - 1)].map((version) => (
                <tr key={version}>
                  <td>v{version}</td>
                  <td>{version === instance.activeConfigVersion ? "当前 active" : "历史版本"}</td>
                  <td>{version === instance.activeConfigVersion ? "RuntimePlan 下一边界生效" : "仅审计回放"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <button className="secondary" onClick={() => navigate(`/agent-teams/${instance.id}/chat`)}>返回工作台</button>
        </section>
      </div>
    </main>
  );
}

function AdminConsole({
  tab,
  setTab,
  sandboxLimit,
  setSandboxLimit,
}: {
  tab: string;
  setTab: (value: string) => void;
  sandboxLimit: number;
  setSandboxLimit: (value: number) => void;
}) {
  const tabs = [
    ["templates", "模板"],
    ["mcp", "MCP Tool 标注"],
    ["assets", "资产"],
    ["users", "白名单"],
    ["sandbox", "沙箱容量"],
    ["audit", "审计 Trace"],
  ];
  return (
    <main className="admin-layout">
      <aside className="admin-tabs">
        {tabs.map(([id, label]) => (
          <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>
        ))}
      </aside>
      <section className="page admin-page">
        <SectionTitle icon={<ShieldCheck size={20} />} title="平台管理台" desc="管理员维护模板、MCP 标注、资产、准入、容量和审计。" />
        {tab === "templates" ? <AdminTemplates /> : null}
        {tab === "mcp" ? <AdminMcp /> : null}
        {tab === "assets" ? <AdminAssets /> : null}
        {tab === "users" ? <AdminUsers /> : null}
        {tab === "sandbox" ? <AdminSandbox limit={sandboxLimit} setLimit={setSandboxLimit} /> : null}
        {tab === "audit" ? <AdminAudit /> : null}
      </section>
    </main>
  );
}

function AdminTemplates() {
  return (
    <div className="panel">
      <h3>AgentTeam 模板</h3>
      <div className="template-editor">
        <div>
          <b>感知快恢 Agent 模板</b>
          <p>main + inspect / diagnose / recover sub agents。普通用户只能追加 main role。</p>
          <Pill tone="green">published</Pill>
        </div>
        <pre>{JSON.stringify({ main: "dispatcher", sub_agents: ["inspect", "diagnose", "recover"], llm: "platform_default" }, null, 2)}</pre>
      </div>
    </div>
  );
}

function AdminMcp() {
  return (
    <div className="panel">
      <h3>MCP Tool 标注</h3>
      <table>
        <thead><tr><th>Tool</th><th>审批</th><th>密钥</th><th>scope_mode</th><th>appid_arg_path</th><th>状态</th></tr></thead>
        <tbody>
          <tr><td>query_resource</td><td>否</td><td>否</td><td>required</td><td>$.appid</td><td><Pill tone="green">allowed</Pill></td></tr>
          <tr><td>recover_execute</td><td>是</td><td>否</td><td>required</td><td>$.appid</td><td><Pill tone="amber">ASK</Pill></td></tr>
          <tr><td>unknown_schema_changed</td><td>否</td><td>否</td><td>none</td><td>-</td><td><Pill tone="red">blocked</Pill></td></tr>
        </tbody>
      </table>
      <p className="hint">schema_hash 变化后旧标注不自动继承，重新标注前运行时 block。</p>
    </div>
  );
}

function AdminAssets() {
  return (
    <div className="panel">
      <h3>资产治理</h3>
      <div className="asset-grid">
        {skills.map((skill) => <AssetCard key={skill.id} title={skill.name} subtitle={`Skill / ${skill.source}`} />)}
        {mcps.map((mcp) => <AssetCard key={mcp.id} title={mcp.name} subtitle={`MCP / ${mcp.transport} / ${mcp.source}`} />)}
      </div>
    </div>
  );
}

function AdminUsers() {
  return (
    <div className="panel">
      <h3>用户与白名单</h3>
      <table><tbody>
        <tr><td>0026demo01</td><td>普通用户</td><td><Pill tone="green">enabled</Pill></td></tr>
        <tr><td>admin</td><td>平台管理员</td><td><Pill tone="green">enabled</Pill></td></tr>
      </tbody></table>
    </div>
  );
}

function AdminSandbox({ limit, setLimit }: { limit: number; setLimit: (value: number) => void }) {
  return (
    <div className="panel">
      <h3>沙箱与容量</h3>
      <div className="capacity-grid">
        <Metric label="当前用户容器" value="8" tone="green" />
        <Metric label="最大用户容器" value={String(limit)} tone="blue" />
        <Metric label="排队用户" value="0" tone="neutral" />
      </div>
      <label>max_user_containers_per_host<input type="number" value={limit} onChange={(event) => setLimit(Number(event.target.value))} /></label>
      <p className="hint">管理员可随 EC2 规格调整该值。idle 容器 TTL 到期后释放。</p>
    </div>
  );
}

function AdminAudit() {
  return (
    <div className="panel">
      <h3>审计与 Trace 回放</h3>
      <div className="timeline">
        {["run.created", "scope.resolved", "approval.required", "tool.call.succeeded", "run.closed"].map((item) => (
          <div className="timeline-item" key={item}>
            <span>{nowLabel()}</span>
            <b>{item}</b>
            <p>audit_trace_id=trace_demo / 参数已脱敏</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function ApprovalCard({ approval, approve, reject }: { approval: Approval; approve: () => void; reject: () => void }) {
  return (
    <div className="approval-card">
      <div>
        <Pill tone="amber">ASK / HITL</Pill>
        <h3>{approval.toolName}</h3>
        <p>{approval.actionSummary}</p>
      </div>
      <div className="approval-details">
        <span>目标 APPID：{approval.appids.join(", ")}</span>
        <span>影响说明：{approval.impact}</span>
        <span>超时：5 分钟</span>
      </div>
      <div className="form-actions">
        <button className="secondary" onClick={reject}><XCircle size={16} /> 拒绝</button>
        <button className="primary" onClick={approve}><CheckCircle2 size={16} /> 确认执行</button>
      </div>
    </div>
  );
}

function RcaCard() {
  return (
    <div className="rca-card">
      <div><Pill tone="blue">RCA 摘要</Pill><h3>支付错误率升高，疑似下游库存接口抖动</h3></div>
      <ul>
        <li>APP-A 错误率从 0.3% 升至 2.1%。</li>
        <li>拓扑依赖显示库存接口 P99 同步升高。</li>
        <li>建议先查询实例健康，再决定是否执行单实例恢复。</li>
      </ul>
    </div>
  );
}

function AuditPage({ run }: { run: Run }) {
  return (
    <main className="page">
      <SectionTitle icon={<FileText size={20} />} title="审计回放" desc={`audit_trace_id=${run.auditTraceId}`} />
      <div className="panel">
        <div className="timeline">
          {run.timeline.map((item, index) => (
            <div key={index} className="timeline-item">
              <span>{item.time}</span>
              <b>{item.type}</b>
              <p>{item.detail}</p>
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}

function NotWhitelisted() {
  return (
    <main className="center-page">
      <AlertTriangle size={36} />
      <h1>当前 W3 账号未进入白名单</h1>
      <p>V1 单机白名单灰度阶段仅允许被授权用户访问。</p>
    </main>
  );
}

function Forbidden() {
  return (
    <main className="center-page">
      <Lock size={36} />
      <h1>403</h1>
      <p>当前角色没有访问该页面的权限。</p>
    </main>
  );
}

function EmptyState() {
  return (
    <main className="center-page">
      <RefreshCw size={36} />
      <h1>状态需要恢复</h1>
      <p>可通过 GET /api/openops/v1/agent-runs/:id/state 恢复运行态。</p>
    </main>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone: "neutral" | "green" | "amber" | "blue" }) {
  return (
    <div className={`metric metric-${tone}`}>
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}

function FormBlock({ title, desc, children }: { title: string; desc: string; children: React.ReactNode }) {
  return (
    <div className="form-block">
      <h3>{title}</h3>
      <p>{desc}</p>
      <div className="form-body">{children}</div>
    </div>
  );
}

function ReadonlyList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="readonly-list">
      <b>{title}</b>
      {items.map((item) => <span key={item}>{item}</span>)}
    </div>
  );
}

function ToggleRow({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="toggle-row">
      <span>{label}</span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
    </label>
  );
}

function AssetCard({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="asset-card">
      <Server size={18} />
      <b>{title}</b>
      <span>{subtitle}</span>
    </div>
  );
}
