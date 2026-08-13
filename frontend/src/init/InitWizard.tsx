import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { color, radius, shadow } from "../theme/tokens";
import { Icon, useHover, Button, TextInput } from "../ui";
import { api } from "../lib/api";
import { useApp } from "../lib/appState";
import type { ModelTemplateOption, Template, Workspace, OmodelStatistics } from "../lib/api/types";
import { WorkspaceDialog } from "./WorkspaceDialog";
import { submitCustomLlm, useConnTest, ConnTestResult, HeadersEditor, headersToRecord, PROTOCOL_LABEL, DEFAULT_CONTEXT_WINDOW, type HeaderRow } from "../settings/AddCustomModelDialog";
import { PendingQuestionNotice } from "../pages/states";

// 2026-07-14 改版：删「选择模板」页（感知快恢是唯一模板，默认用它）。三步=配置 → 确认能力清单
//（含 OModel 看护空间初始化 loading）→ 激活。三个步名字面量均被 e2e 断言依赖，改名须同步 e2e/smoke.spec.ts。
const STEPS = ["配置 Agent", "确认能力清单", "激活 Agent"];

/** 自定义模型卡展示元数据（创建成功时从表单捕获；仅展示用，事实在后端 llm-config）。 */
interface CustomLlmMeta {
  modelName: string;
  baseUrl: string;
}

/** 初始化向导（30.2 改版）：模板 → 配置（名称+身份+模型+范围合一页）→ 激活。壳外全屏。
 * 编辑态（/agent-teams/:instanceId/edit）复用同一向导：预填名称/范围/模型，模板锁定不可换，
 * 保存走 :update 更新同一实例后回 Agent 清单。 */
export function InitWizard() {
  const nav = useNavigate();
  const { instanceId } = useParams();
  const editing = !!instanceId;
  const { refresh } = useApp();
  const [step, setStep] = useState(0);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [tplId, setTplId] = useState("");
  const [name, setName] = useState("");
  const [wsId, setWsId] = useState("");
  // 模型三态（38 号）：template=选一套模型模板（主/子 Agent 组合）；custom=BYO 自带模型（主/子都用它）；
  // legacy=存量实例绑的旧版单平台模型（仅编辑态出现，保存原样回传，引导换绑模板）
  const [llm, setLlm] = useState<"template" | "custom" | "legacy">("template");
  const [customLlmId, setCustomLlmId] = useState("");
  const [customLlmLabel, setCustomLlmLabel] = useState("");
  const [customLlmMeta, setCustomLlmMeta] = useState<CustomLlmMeta | null>(null);
  const [modelTemplates, setModelTemplates] = useState<ModelTemplateOption[]>([]); // 管理员编排且对本用户可见（38.1 模板级授权：scope+白名单）的模板
  const [selectedModelTemplateId, setSelectedModelTemplateId] = useState(""); // 选中的模板 id（空=平台默认兜底卡）
  const [legacyPlatformId, setLegacyPlatformId] = useState(""); // 存量 overlay.platform_model_id（仅编辑态非空）
  const [legacyLabel, setLegacyLabel] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false); // BYO 高级折叠区
  const [wsDialog, setWsDialog] = useState<null | { id?: string }>(null);  // null=关；{}=新建；{id}=编辑
  const [confirmDelWs, setConfirmDelWs] = useState<Workspace | null>(null);
  const [delBusy, setDelBusy] = useState(false);
  const [omodelReady, setOmodelReady] = useState(false);  // step1 OModel 看护空间初始化 loading 是否转完
  const [activating, setActivating] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (editing && instanceId) {
      // 编辑态：并取模板/范围/实例详情/模型清单，预填 名称+范围+模板（锁定）+模型
      // （模型模板清单由下方公共 effect 拉取，此处只定预填态，避免两处 setState 竞态）
      // 预填互斥优先级与后端解析同序：custom(BYO) > model_template_id > platform_model_id(legacy)
      Promise.all([api.getTemplates(), api.getWorkspaces(), api.getAgentTeam(instanceId), api.getModelConfigs()])
        .then(([tpls, wss, d, models]) => {
          setTemplates(tpls);
          setWorkspaces(wss);
          setTplId(tpls.some((t) => t.template_version_id === d.template_version_id)
            ? d.template_version_id : tpls[0]?.template_version_id ?? "");
          setName(d.name);
          setWsId(d.workspace_id);
          const llmId = typeof d.overlay.user_llm_config_id === "string" ? d.overlay.user_llm_config_id : "";
          const mtplId = typeof d.overlay.model_template_id === "string" ? d.overlay.model_template_id : "";
          const platformId = typeof d.overlay.platform_model_id === "string" ? d.overlay.platform_model_id : "";
          if (llmId) {
            setLlm("custom");
            setCustomLlmId(llmId);
            setCustomLlmLabel(models.find((m) => m.llm_config_id === llmId)?.label ?? "自定义模型");
            setAdvancedOpen(true); // BYO 藏在高级折叠区：绑定态进来必须展开，否则选中卡不可见
          } else if (mtplId) {
            // 绑定的模板缺席/停用（清单只回 active+已授权）→ 保持选中原样保存由后端裁决，卡区显提示条引导换绑
            setLlm("template");
            setSelectedModelTemplateId(mtplId);
          } else if (platformId) {
            // 存量单平台模型绑定：legacy 卡展示 + 引导换绑模板；保存不动即原样回传
            setLlm("legacy");
            setLegacyPlatformId(platformId);
            setLegacyLabel(models.find((m) => m.llm_config_id === "platform:" + platformId)?.label ?? platformId);
          }
        })
        .catch((e: unknown) => setErr(e instanceof Error ? e.message : "加载失败，请重试"));
      return;
    }
    api.getTemplates().then((t) => { setTemplates(t); setTplId(t[0]?.template_version_id ?? ""); });
    api.getWorkspaces().then(setWorkspaces);
  }, [editing, instanceId]);

  // 模型模板清单（38 号）：/models/templates 返回管理员编排且对本用户可见（38.1 模板级授权）的 active 模板。
  // 新建态默认选中 is_default 模板（无默认取首条）；编辑态由上面的编辑预填决定。
  // 拉取失败置空清单 → 渲染「平台默认配置」兜底卡，不死路。
  useEffect(() => {
    api.getModelTemplates()
      .then((rows) => {
        const active = rows.filter((t) => t.status === "active");
        setModelTemplates(active);
        if (!editing) {
          const def = active.find((t) => t.is_default) ?? active[0];
          if (def) setSelectedModelTemplateId(def.model_template_id);
        }
      })
      .catch(() => setModelTemplates([]));
  }, [editing]);

  // 门条件：step0 配置（名称+范围+模型，custom 须已创建否则死路；template 恒预选/兜底卡有效；legacy 有效）；
  // step1 OModel 初始化转完；step2 放行
  const canNext = [!!name.trim() && !!wsId && (llm === "custom" ? !!customLlmId : true), omodelReady, true][step];

  const activate = () => {
    setActivating(true);
    setErr("");
    const done = editing && instanceId
      // 编辑态：更新同一实例（模板不送），刷新全局列表后回清单页。
      // 模型四态互斥：custom > template > legacy（原样回传）> 全 null=平台默认
      ? api.updateAgentTeam(instanceId, {
          name, workspace_id: wsId,
          user_llm_config_id: llm === "custom" && customLlmId ? customLlmId : null,
          model_template_id: llm === "template" && selectedModelTemplateId ? selectedModelTemplateId : null,
          platform_model_id: llm === "legacy" && legacyPlatformId ? legacyPlatformId : null,
        }).then(() => { refresh(); nav("/agents"); })
      : api.createAgentTeam({
          template_version_id: tplId, name, workspace_id: wsId,
          initial_overlay_json:
            llm === "custom" && customLlmId ? { user_llm_config_id: customLlmId }
            : llm === "template" && selectedModelTemplateId ? { model_template_id: selectedModelTemplateId }
            : undefined,
        }).then((r) => { nav(`/agent-teams/${r.instance_id}/chat`); });
    done.catch((e: unknown) => {
      // 无 .catch 时 400 会让 activating 永远为 true → 一直转圈；这里收口：停转 + 显因（如"同名实例已存在"）
      setActivating(false);
      setErr(e instanceof Error ? e.message : editing ? "保存失败，请重试" : "激活失败，请重试");
    });
  };

  const tpl = templates.find((t) => t.template_version_id === tplId);

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: color.pageBg }}>
      {/* header + stepper */}
      <header style={{ flex: "0 0 auto", height: 60, borderBottom: `1px solid  rgb(226, 229, 234)`, background: "#fff", display: "flex", alignItems: "center", padding: "0 24px", gap: 14 }}>
        <div style={{ width: 30, height: 30, borderRadius: 8, background: color.brandGrad, display: "flex", alignItems: "center", justifyContent: "center", boxShadow: shadow.brand }}>
          <Icon name="robot" size={18} color="#fff" />
        </div>
        <div style={{ fontSize: 15, fontWeight: 800 }}>{"运维Agent"}</div>
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 0, maxWidth: 520, margin: "0 auto" }}>
          {STEPS.map((s, i) => (
            <div key={s} style={{ display: "flex", alignItems: "center", flex: i === STEPS.length - 1 ? "0 0 auto" : 1 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                <div style={{ width: 22, height: 22, borderRadius: "50%", background: i < step ? color.good : i === step ? color.brand : "#e6e8ec", color: i <= step ? "#fff" : color.textSubtle, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11.5, fontWeight: 700, flex: "0 0 22px" }}>
                  {i < step ? <Icon name="check" size={12} color="#fff" /> : i + 1}
                </div>
                <span style={{ fontSize: 12.5, fontWeight: i === step ? 700 : 500, color: i === step ? color.brand : i < step ? color.textStrong : color.textSubtle, whiteSpace: "nowrap" }}>{s}</span>
              </div>
              {i < STEPS.length - 1 ? <div style={{ flex: 1, height: 2, margin: "0 10px", background: i < step ? color.good : "#e6e8ec", minWidth: 16 }} /> : null}
            </div>
          ))}
        </div>
        <Icon name="x" size={20} color={color.textSubtle} onClick={() => nav(-1)} />
      </header>

      {/* body */}
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "36px 24px" }}>
        <div style={{ maxWidth: 1280, margin: "0 auto" }}>
          {/* 外链 ?q= 场景：问题已保留，初始化完成进入对话时自动发送（editing 复用向导时不涉及） */}
          {!editing ? <PendingQuestionNotice /> : null}
          {step === 0 ? (
            <StepConfigure
              name={name} onName={setName}
              workspaces={workspaces} wsId={wsId} onPickWs={setWsId} onCreateWs={() => setWsDialog({})}
              onEditWs={(id) => setWsDialog({ id })} onDeleteWs={(ws) => setConfirmDelWs(ws)}
              llm={llm} onLlm={setLlm}
              modelTemplates={modelTemplates} selectedModelTemplateId={selectedModelTemplateId}
              onPickTemplate={(id) => { setSelectedModelTemplateId(id); setLlm("template"); }}
              legacyPlatformId={legacyPlatformId} legacyLabel={legacyLabel}
              advancedOpen={advancedOpen} onToggleAdvanced={() => setAdvancedOpen((v) => !v)}
              customLlmId={customLlmId} customLlmLabel={customLlmLabel} customLlmMeta={customLlmMeta}
              onCustomCreated={(id, label, meta) => {
                setCustomLlmId(id); setCustomLlmLabel(label); setCustomLlmMeta(meta); setLlm("custom");
              }}
              onCustomRemoved={() => {
                // 删除 BYO 后的落点：存量 legacy 绑定优先回 legacy；否则回模板态（保留/补选默认）
                setCustomLlmId(""); setCustomLlmLabel(""); setCustomLlmMeta(null);
                if (legacyPlatformId) { setLlm("legacy"); return; }
                setLlm("template");
                if (!selectedModelTemplateId) {
                  const def = modelTemplates.find((t) => t.is_default) ?? modelTemplates[0];
                  if (def) setSelectedModelTemplateId(def.model_template_id);
                }
              }}
            />
          ) : null}
          {step === 1 ? (
            <StepCapabilities capabilities={tpl?.capabilities ?? []} wsId={wsId} ready={omodelReady} onReady={() => setOmodelReady(true)} />
          ) : null}
          {step === 2 ? <StepActivate name={name} activating={activating} editing={editing} /> : null}
        </div>
      </div>

      {/* footer */}
      <div style={{ flex: "0 0 auto", borderTop: `1px solid rgb(226, 229, 234)`, background: "#fff", padding: "14px 24px", display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ maxWidth: 720, margin: "0 auto", width: "100%", display: "flex", gap: 10 }}>
          {step > 0 ? <Button variant="secondary" icon="arrow-left" onClick={() => setStep((s) => s - 1)}>上一步</Button> : null}
          <div style={{ flex: 1 }} />
          {err ? (
            <div style={{ display: "flex", alignItems: "center", gap: 6, color: color.dangerText, fontSize: 12.5, fontWeight: 600, maxWidth: 380 }}>
              <Icon name="alert-triangle" size={15} color={color.dangerText} />
              <span>{err}</span>
            </div>
          ) : null}
          {step < 2 ? (
            <Button icon="arrow-right" disabled={!canNext} onClick={() => setStep((s) => s + 1)}>下一步</Button>
          ) : (
            <Button icon={editing ? "device-floppy" : "rocket"} disabled={activating} onClick={activate}>
              {editing ? (activating ? "保存中…" : "保存修改") : (activating ? "激活中…" : "开始对话")}
            </Button>
          )}
        </div>
      </div>

      <WorkspaceDialog open={!!wsDialog} editWorkspaceId={wsDialog?.id ?? null} onClose={() => setWsDialog(null)}
        onSaved={(id) => {
          // 创建：选中新范围；编辑：保持原选中。都重拉列表（否则配置页网格还是旧的）
          if (!wsDialog?.id) setWsId(id);
          setWsDialog(null);
          api.getWorkspaces().then(setWorkspaces);
        }} />

      {/* 删除系统范围二次确认 */}
      {confirmDelWs ? (
        <div onClick={() => !delBusy && setConfirmDelWs(null)}
          style={{ position: "fixed", inset: 0, zIndex: 60, background: "rgba(20,24,31,.42)", display: "flex", alignItems: "center", justifyContent: "center", animation: "omFade .16s ease" }}>
          <div onClick={(e) => e.stopPropagation()} style={{ width: 420, background: "#fff", borderRadius: radius.modal, padding: "22px 22px 18px", animation: "omPop .2s ease" }}>
            <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8 }}>删除「{confirmDelWs.name}」？</div>
            <div style={{ fontSize: 12.5, color: color.textMuted, lineHeight: 1.6, marginBottom: 18 }}>
              软删除该系统范围。绑定此范围的 Agent 将在下次运行时无法解析看护范围（scope 解析失败），请先确认无 Agent 依赖它。
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <Button variant="secondary" disabled={delBusy} onClick={() => setConfirmDelWs(null)}>取消</Button>
              <Button icon={delBusy ? "loader-2" : "trash"} disabled={delBusy} style={{ background: color.danger }} onClick={() => {
                const id = confirmDelWs.workspace_id;
                setDelBusy(true);
                api.deleteWorkspace(id)
                  .then(() => {
                    if (wsId === id) setWsId("");  // 删的是当前选中范围 → 清空选中
                    setConfirmDelWs(null); setDelBusy(false);
                    return api.getWorkspaces().then(setWorkspaces);
                  })
                  .catch((e: unknown) => { setDelBusy(false); alert(e instanceof Error ? e.message : "删除失败，请重试"); });
              }}>{delBusy ? "删除中…" : "确认删除"}</Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Title({ t, d }: { t: string; d: string }) {
  return (
    <div style={{ marginBottom: 22 }}>
      <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 6px" }}>{t}</h2>
      <div style={{ fontSize: 13, color: color.textSubtle }}>{d}</div>
    </div>
  );
}

function SectionLabel({ text, required, right }: { text: string; required?: boolean; right?: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
      <label style={{ fontSize: 13, fontWeight: 600, color: color.textStrong }}>
        {text}{required ? <span style={{ color: color.dangerText }}> *</span> : null}
      </label>
      {right ?? null}
    </div>
  );
}

// 能力识别卡：标题 name 是模板 sub_agent 的 label——管理员可编辑的自由文本（后端无白名单/无 maxLength），
// 可能很长。标题走「省略号截断 + title 补全」（与 SettingsPage 卡片标题+徽标同惯例），徽标恒定不收缩；
// 抽成组件便于 e2e 以极长名回归横向不溢出。
export function CapabilityCard({ name }: { name: string }) {
  const meta = CAP_META[name] ?? { icon: "bolt", desc: "平台内置能力。" };
  return (
    <div style={{ border: `1px solid rgb(226, 229, 234)`, borderRadius: radius.xl, padding: 15, display: "flex", alignItems: "flex-start", gap: 11, background: "#fff" }}>
      <div style={{ width: 40, height: 40, borderRadius: radius.lg, background: color.brandTintBg, color: color.brand, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 40px" }}>
        <Icon name={meta.icon} size={21} color={color.brand} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 3, minWidth: 0 }}>
          <span data-testid="cap-title" title={name} style={{ fontSize: 14, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}>{name}</span>
          <span style={{ fontSize: 10, fontWeight: 600, color: color.goodText, background: color.goodBg, padding: "2px 6px", borderRadius: 5, whiteSpace: "nowrap", flexShrink: 0 }}>已识别</span>
        </div>
        <p style={{ fontSize: 12, color: color.textSubtle, margin: 0, lineHeight: 1.55 }}>{meta.desc}</p>
      </div>
    </div>
  );
}

/** step1「确认能力清单」：能力识别卡片 + Agent 看护空间 OModel 初始化 loading（2s 装饰过场）+
 * 看护空间概览（统计四数，走后端代理 wesee/statistics）+ 看护空间图谱（iframe，域名从后端 page base 派生 origin）。
 * ready 一旦转完（onReady）即置位，回退再进本步直接显完成态不重转；下一步按钮由外层 canNext=ready 门控
 * （统计/图谱为信息展示，不参与门控）。 */
function StepCapabilities({ capabilities, wsId, ready, onReady }: { capabilities: string[]; wsId: string; ready: boolean; onReady: () => void }) {
  useEffect(() => {
    if (ready) return;
    const t = setTimeout(onReady, 2000);  // 纯前端仪式感过场：workspace 已在配置步选定，V1 无 OModel 预热接口
    return () => clearTimeout(t);
  }, [ready, onReady]);

  // 看护空间统计四数（后端代理 wesee/statistics；上游失败后端已降级为 0，取不到则本地置 null 显占位）
  const [stats, setStats] = useState<OmodelStatistics | null>(null);
  useEffect(() => {
    if (!wsId) { setStats(null); return; }
    let alive = true;
    setStats(null);
    api.getWorkspaceStatistics(wsId).then(
      (s) => { if (alive) setStats(s); },
      () => { if (alive) setStats(null); },
    );
    return () => { alive = false; };
  }, [wsId]);

  // 图谱 iframe：域名从后端下发的 page base 派生 origin（不在前端硬编码）；mock/未配 → 空态
  const [pageBase, setPageBase] = useState<string | null>(null); // null=加载中
  useEffect(() => {
    let alive = true;
    api.getOmodelPageBase().then(
      (b) => { if (alive) setPageBase(b); },
      () => { if (alive) setPageBase(""); },
    );
    return () => { alive = false; };
  }, []);
  const graphOrigin = useMemo(() => {
    if (!pageBase) return "";
    try { return new URL(pageBase).origin; } catch { return ""; }
  }, [pageBase]);
  const iframeSrc = graphOrigin && wsId
    ? `${graphOrigin}/wesee/omodel/#/?dataSource=api&workspace=${encodeURIComponent(wsId)}&entity=true`
    : "";

  return (
    <div style={{ background: "#fff", border: "1px solid rgb(226, 229, 234)", borderRadius: radius.xxl, padding: "26px 28px", display: "flex", flexDirection: "column", gap: 24 }}>
      <Title t="确认能力清单" d="确认本 Agent 已具备的内置能力，并等待看护空间初始化完成后进入激活流程" />
      {capabilities.length ? (
        <div style={{ marginBottom: 26 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: color.textStrong, marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
            <Icon name="bolt" size={16} color={color.brand} />能力识别
            <span style={{ fontSize: 11, fontWeight: 400, color: color.textSubtle }}>Agent 已具备的内置能力</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
            {capabilities.map((c) => <CapabilityCard key={c} name={c} />)}
          </div>
        </div>
      ) : null}

      {/* Agent 看护空间 OModel 初始化（占原「准备激活」块位置的装饰 loading，2s 转完） */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, border: `1px solid ${ready ? color.goodBorder : color.border}`, background: ready ? color.goodBg : "rgba(247,248,250,.5)", borderRadius: radius.xl, padding: "16px 18px" }}>
        <div style={{ width: 38, height: 38, borderRadius: radius.md, background: ready ? color.goodBg : color.brandTintBg, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 38px" }}>
          <Icon name={ready ? "circle-check" : "loader-2"} size={20} color={ready ? color.good : color.brand} spin={!ready} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13.5, fontWeight: 600, color: ready ? color.goodText : color.textStrong }}>
            {ready ? "Agent 看护空间已就绪" : "Agent 看护空间 OModel 初始化中…"}
          </div>
          <div style={{ fontSize: 12, color: color.textSubtle, marginTop: 2 }}>
            {ready ? "看护范围已对接OModel（ 建议您接入WeOps-APM、日志、指标数据，OModel数据越全面，Agent能力越强 ）" : "正在对接 OModel 看护范围，请稍候（约 2 秒）。"}
          </div>
        </div>
      </div>

      {/* 看护空间概览：oModel 统计四数（节点/关系/节点类型/关系类型） */}
      <div style={{ marginTop: 24 }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: color.textStrong, marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
          <Icon name="chart-dots-3" size={16} color={color.brand} />看护空间概览
          <span style={{ fontSize: 11, fontWeight: 400, color: color.textSubtle }}>当前看护范围在 OModel 中的规模</span>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
          <StatCard icon="topology-star-3" label="节点数" value={stats?.node_count} />
          <StatCard icon="affiliate" label="关系数" value={stats?.relation_count} />
          <StatCard icon="category" label="节点类型" value={stats?.node_type_count} />
          <StatCard icon="binary-tree-2" label="关系类型" value={stats?.relation_type_count} />
        </div>
      </div>

      {/* 看护空间图谱：嵌入 oModel 实体图 iframe（域名后端下发；mock/未配显空态） */}
      <div style={{ marginTop: 24 }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: color.textStrong, marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
          <Icon name="chart-dots" size={16} color={color.brand} />看护空间图谱
          <span style={{ fontSize: 11, fontWeight: 400, color: color.textSubtle }}>OModel实体关系拓扑{iframeSrc ? <>（您也可以导航到<a href={iframeSrc} target="_blank" rel="noopener noreferrer" style={{ color: color.brand, textDecoration: "none" }}>OModel</a>查看详情）</> : null}</span>
        </div>
        <div style={{ height: 420, border: `1px solid ${color.border}`, borderRadius: radius.xl, overflow: "hidden", background: "#fff", display: "flex" }}>
          {pageBase === null ? (
            <GraphPlaceholder icon="loader-2" spin text="正在获取 OModel 图谱地址…" />
          ) : iframeSrc ? (
            <iframe
              key={`${iframeSrc}&isShow=false`} /* workspace 切换时强制重载，避免对端 SPA 内部路由残留 */
              src={`${iframeSrc}&isShow=false`}
              title="oModel 看护空间图谱"
              style={{ flex: 1, width: "100%", height: "100%", border: "none", background: "#fff" }}
            />
          ) : (
            <GraphPlaceholder icon="topology-star-3" text="图谱在内网环境可用：需后端配置 OPENOPS_OMODEL_BASE_URL 后显示" />
          )}
        </div>
      </div>
    </div>
  );
}

/** 看护空间概览的单个数字卡：图标 + 大号数字（未取到显 —）+ 中文标签。 */
function StatCard({ icon, label, value }: { icon: string; label: string; value?: number }) {
  return (
    <div style={{ border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "16px 18px", background: "#fff", display: "flex", alignItems: "center", gap: 13 }}>
      <div style={{ width: 40, height: 40, borderRadius: radius.lg, background: color.brandTintBg, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 40px" }}>
        <Icon name={icon} size={21} color={color.brand} />
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 22, fontWeight: 800, lineHeight: 1.1, color: color.textStrong }}>
          {value == null ? "—" : value.toLocaleString()}
        </div>
        <div style={{ fontSize: 12, color: color.textSubtle, marginTop: 3 }}>{label}</div>
      </div>
    </div>
  );
}

/** 图谱容器空态/加载态占位。 */
function GraphPlaceholder({ icon, text, spin }: { icon: string; text: string; spin?: boolean }) {
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, color: color.textSubtle }}>
      <div style={{ width: 48, height: 48, borderRadius: 14, background: color.pageBg, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <Icon name={icon} size={24} color={color.textFaint} spin={spin} />
      </div>
      <div style={{ fontSize: 12.5, maxWidth: 380, textAlign: "center", lineHeight: 1.7 }}>{text}</div>
    </div>
  );
}

/** 单选卡右侧的 radio 圆点。 */
function RadioDot({ on }: { on: boolean }) {
  return (
    <div style={{ width: 18, height: 18, borderRadius: "50%", border: `2px solid ${on ? color.brand : "#cfd3da"}`, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 18px" }}>
      {on ? <div style={{ width: 8, height: 8, borderRadius: "50%", background: color.brand }} /> : null}
    </div>
  );
}

/** 合并配置页（原型 STEP1）：名称 → 身份确认 → 模型供应商（选一套模型模板）→ 系统看护范围。 */
function StepConfigure({
  name, onName,
  workspaces, wsId, onPickWs, onCreateWs, onEditWs, onDeleteWs,
  llm, onLlm, modelTemplates, selectedModelTemplateId, onPickTemplate,
  legacyPlatformId, legacyLabel, advancedOpen, onToggleAdvanced,
  customLlmId, customLlmLabel, customLlmMeta, onCustomCreated, onCustomRemoved,
}: {
  name: string; onName: (v: string) => void;
  workspaces: Workspace[]; wsId: string; onPickWs: (id: string) => void; onCreateWs: () => void;
  onEditWs: (id: string) => void; onDeleteWs: (ws: Workspace) => void;
  llm: "template" | "custom" | "legacy"; onLlm: (v: "template" | "custom" | "legacy") => void;
  modelTemplates: ModelTemplateOption[]; selectedModelTemplateId: string; onPickTemplate: (id: string) => void;
  legacyPlatformId: string; legacyLabel: string;
  advancedOpen: boolean; onToggleAdvanced: () => void;
  customLlmId: string; customLlmLabel: string; customLlmMeta: CustomLlmMeta | null;
  onCustomCreated: (id: string, label: string, meta: CustomLlmMeta) => void;
  onCustomRemoved: () => void;
}) {
  const { me } = useApp(); // 身份确认用真实登录账号（IAM 开启=工号/姓名；mock=演示身份）
  const [showAdd, setShowAdd] = useState(false);
  const selectedWs = workspaces.find((w) => w.workspace_id === wsId);
  // 编辑态绑定的模板不在清单里（被停用/撤销授权）→ 提示条引导换绑（原样保存由后端裁决）
  const boundTemplateMissing = llm === "template" && !!selectedModelTemplateId
    && !modelTemplates.some((t) => t.model_template_id === selectedModelTemplateId);
  return (
    <>
      <div style={{ background: "#fff", border: "1px solid rgb(226, 229, 234)", borderRadius: radius.xxl, padding: "26px 28px", display: "flex", flexDirection: "column", gap: 24 }}>
      <div style={{ marginBottom: 2 }}>
          <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 6px" }}>创建看护您系统的感知快恢Agent</h2>
          <div style={{ fontSize: 13, color: color.textSubtle }}>一页完成：名称、模型与看护范围；身份使用你当前的登录账号</div>
        </div>
        {/* ① 名称 */}
        <div>
          <SectionLabel text="Agent 名称" required />
          <div style={{ fontSize: 12, color: color.textSubtle, margin: "0 0 8px", lineHeight: 1.55 }}>
            建议用「看护系统范围 + 感知快恢Agent」，如「运行观测-感知快恢Agent」，便于快速识别 Agent 看护的系统范围
          </div>
          <TextInput value={name} onChange={onName} placeholder="如：支付域-感知快恢Agent" />
          {!name.trim() ? <div style={{ fontSize: 12, color: color.dangerText, marginTop: 6 }}>名称必填</div> : null}
        </div>

        {/* ② 身份确认 */}
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12, border: `1px solid rgb(226, 229, 234)`, borderRadius: radius.xl, background: "rgba(247,248,250,.5)", padding: 14 }}>
          <div style={{ width: 32, height: 32, borderRadius: radius.md, background: color.brandTintBg, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 32px" }}>
            <Icon name="user" size={17} color={color.brand} />
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 3 }}>身份确认</div>
            <div style={{ fontSize: 12, color: color.textSubtle, lineHeight: 1.6, overflowWrap: "anywhere", wordBreak: "break-word" }}>
              你将以 <span style={{ fontWeight: 600, color: color.textStrong }}>{me ? `${me.user_id}（${me.display_name}）` : "当前登录账号"}</span> 的身份创建此 Agent，所有操作将关联到该账号。
            </div>
          </div>
        </div>

        {/* ③ 模型供应商（38 号：选一套模型模板 = 主 Agent + 子 Agent 模型组合） */}
        <div>
          <SectionLabel text="模型供应商" />
          <div style={{ fontSize: 12, color: color.textSubtle, margin: "0 0 10px" }}>选择一套模型模板（主 Agent 与子 Agent 分别使用的模型，由管理员编排）；也可在高级选项接入自带模型。</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {boundTemplateMissing ? (
              <div style={{ display: "flex", alignItems: "flex-start", gap: 8, background: "#fff7e8", border: "1px solid #f5dcb0", borderRadius: radius.lg, padding: "9px 12px", fontSize: 12, color: "#8a5a00", lineHeight: 1.6 }}>
                <Icon name="alert-triangle" size={14} color="#c58a00" />
                <span>原绑定的模型模板已停用或不再对你可见：保存将维持原绑定（下次运行回退平台默认模型），建议改选下方的一套模板。</span>
              </div>
            ) : null}
            {modelTemplates.length > 0 ? modelTemplates.map((t) => (
              <ModelTemplateCard key={t.model_template_id} tpl={t}
                selected={llm === "template" && selectedModelTemplateId === t.model_template_id}
                onClick={() => onPickTemplate(t.model_template_id)} />
            )) : (
              // 无可用模板（管理员未编排 / 主子槽位授权不全）：走后端默认的兜底卡，避免无模型可选
              <LlmCard selected={llm === "template" && !selectedModelTemplateId} onClick={() => onPickTemplate("")}
                icon="sparkles" iconBg={color.brandTintBg} iconColor={color.brand}
                label="平台默认配置" badge="平台提供" />
            )}

            {legacyPlatformId ? (
              <>
                <LlmCard selected={llm === "legacy"} onClick={() => onLlm("legacy")}
                  icon="cpu" iconBg={color.neutralBg} iconColor={color.textNav}
                  label={legacyLabel || legacyPlatformId} badge="旧版单模型配置" />
                {llm === "legacy" ? (
                  <div style={{ fontSize: 11.5, color: color.textSubtle, lineHeight: 1.6, padding: "0 2px" }}>
                    当前为旧版单模型绑定（主 / 子 Agent 共用该模型）。建议改选上方的一套模型模板，让主 / 子 Agent 分别使用编排好的模型。
                  </div>
                ) : null}
              </>
            ) : null}

            {/* BYO 高级折叠区：自带模型（OpenAI 兼容，须支持 tool calling）；选中后主/子 Agent 都用它（覆盖整套模板） */}
            <div onClick={onToggleAdvanced}
              style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", padding: "4px 2px", fontSize: 12, fontWeight: 600, color: color.brand, userSelect: "none" }}>
              <Icon name={advancedOpen ? "chevron-down" : "chevron-right"} size={14} color={color.brand} />
              高级选项：接入自带模型（选中后主 / 子 Agent 都使用它）
            </div>
            {advancedOpen ? (
              <>
                {customLlmId ? (
                  <LlmCard selected={llm === "custom"} onClick={() => onLlm("custom")}
                    icon="cpu" iconBg={color.neutralBg} iconColor={color.textNav}
                    label={customLlmLabel} badge="可用" badgeTone="good"
                    extra={customLlmMeta ? <div style={{ fontSize: 11, color: color.textSubtle, marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", fontFamily: "ui-monospace, monospace" }}>{customLlmMeta.baseUrl}</div> : null}
                    trailing={<Icon name="trash" size={16} color={color.textFaint} title="删除该自定义模型（回退模型模板）" onClick={onCustomRemoved} />} />
                ) : null}
                {showAdd ? (
                  <InlineAddModel
                    onCancel={() => setShowAdd(false)}
                    onCreated={(id, label, meta) => { setShowAdd(false); onCustomCreated(id, label, meta); }}
                  />
                ) : !customLlmId ? (
                  <AddModelButton onClick={() => setShowAdd(true)} />
                ) : null}
              </>
            ) : null}
          </div>
        </div>

        {/* ④ 系统看护范围 */}
        <div>
          <SectionLabel text="系统看护范围" required
            right={<span title={selectedWs?.name ?? undefined} style={{ fontSize: 12, color: color.textSubtle, minWidth: 0, maxWidth: "60%", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>已选：{selectedWs?.name ?? "—"}</span>} />
          <div style={{ fontSize: 12, color: color.textSubtle, margin: "0 0 12px" }}>选择您的感知快恢 Agent 看护的系统范围，可以选择一个或多个应用服务</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 12 }}>
                     {workspaces.map((ws) => (
              <WorkspaceCard key={ws.workspace_id} ws={ws} on={ws.workspace_id === wsId} onPick={onPickWs}
                onEdit={() => onEditWs(ws.workspace_id)} onDelete={() => onDeleteWs(ws)} />
            ))}
            <NewWsButton onClick={onCreateWs} />
          </div>
          {!wsId ? <div style={{ fontSize: 12, color: color.dangerText, marginTop: 8 }}>请选择或新建一个系统范围</div> : null}
        </div>
      </div>
    </>
  );
}

/** 模型模板卡（38 号）：模板名 + 主/子 Agent 槽位模型行。export 供 e2e fixture 溢出回归复用；
 * 标题 span 保留 data-testid="llm-label"（与 LlmCard 同一宽度断言体系）。 */
export function ModelTemplateCard({ tpl, selected, onClick }: {
  tpl: ModelTemplateOption; selected: boolean; onClick: () => void;
}) {
  const { hovered, bind } = useHover();
  const borderColor = selected ? color.brand : hovered ? "#1890FF" : "rgb(226, 229, 234)";
  return (
    <div onClick={onClick} {...bind}
      style={{ border: `1px solid ${borderColor}`, background: selected ? color.brandTintBg : "#fff", borderRadius: radius.xl, padding: "12px 14px", cursor: "pointer", display: "flex", alignItems: "center", gap: 12 }}>
      <div style={{ width: 34, height: 34, borderRadius: radius.md, background: color.brandTintBg, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 34px" }}>
        <Icon name="sparkles" size={17} color={color.brand} />
      </div>
      <div style={{ flex: "1 1 auto", minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <span data-testid="llm-label" title={tpl.display_name} style={{ fontSize: 13, fontWeight: 600, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{tpl.display_name}</span>
          <span style={{ fontSize: 10, color: tpl.is_default ? color.goodText : color.textSubtle, background: tpl.is_default ? color.goodBg : color.neutralBg, padding: "2px 6px", borderRadius: 5, whiteSpace: "nowrap", flexShrink: 0 }}>
            {tpl.is_default ? "默认推荐" : "平台模板"}
          </span>
        </div>
        <div title={`主 Agent：${tpl.main_model.display_name} · 子 Agent：${tpl.sub_model.display_name}`}
          style={{ fontSize: 11, color: color.textSubtle, marginTop: 3, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          主 Agent：{tpl.main_model.display_name} · 子 Agent：{tpl.sub_model.display_name}
        </div>
      </div>
      <RadioDot on={selected} />
    </div>
  );
}

export function LlmCard({ selected, onClick, icon, iconBg, iconColor, label, badge, badgeTone, extra, trailing }: {
  selected: boolean; onClick: () => void; icon: string; iconBg: string; iconColor: string;
  label: string; badge?: string; badgeTone?: "good"; extra?: React.ReactNode; trailing?: React.ReactNode;
}) {
  const { hovered, bind } = useHover();
  const borderColor = selected ? color.brand : hovered ? "#1890FF" : "rgb(226, 229, 234)";
  return (
    <div onClick={onClick} {...bind}
      style={{ border: `1px solid ${borderColor}`, background: selected ? color.brandTintBg : "#fff", borderRadius: radius.xl, padding: "12px 14px", cursor: "pointer", display: "flex", alignItems: "center", gap: 12 }}>
      <div style={{ width: 34, height: 34, borderRadius: radius.md, background: iconBg, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 34px" }}>
        <Icon name={icon} size={17} color={iconColor} />
      </div>
      {/* flex:"1 1 auto"（非 flex:1=1 1 0%）：让名字容器 flex-basis=内容宽而非 0，才会与右侧 extra
          （自定义卡的 baseUrl）一同参与收缩竞争；否则 basis:0 → 收缩权重 0 → 被挤成 0 宽、名字连省略号都不显。 */}
      <div style={{ flex: "1 1 auto", minWidth: 0, display: "flex", alignItems: "center", gap: 8 }}>
        <span data-testid="llm-label" title={label} style={{ fontSize: 13, fontWeight: 600, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{label}</span>
        {badge ? <span style={{ fontSize: 10, color: badgeTone === "good" ? color.goodText : color.textSubtle, background: badgeTone === "good" ? color.goodBg : color.neutralBg, padding: "2px 6px", borderRadius: 5, whiteSpace: "nowrap", flexShrink: 0 }}>{badge}</span> : null}
      </div>
      {extra}
      {trailing}
      <RadioDot on={selected} />
    </div>
  );
}
 
export function WorkspaceCard({ ws, on, onPick, onEdit, onDelete }: {
  ws: Workspace; on: boolean; onPick: (id: string) => void; onEdit: () => void; onDelete: () => void;
}) {
  const { hovered, bind } = useHover();
  const borderColor = on ? color.brand : hovered ? "#1890FF" : "rgb(226, 229, 234)";
  return (
    <div onClick={() => onPick(ws.workspace_id)} {...bind}
      style={{ position: "relative", border: `1px solid ${borderColor}`, background: on ? color.brandTintBg : "#fff", borderRadius: radius.xl, padding: 14, cursor: "pointer" }}>
      {on ? (
        <div style={{ position: "absolute", top: 12, right: 12, width: 20, height: 20, borderRadius: "50%", background: color.brand, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Icon name="check" size={13} color="#fff" />
        </div>
      ) : null}
      <div title={ws.name} style={{ fontSize: 14, fontWeight: 600, paddingRight: 24, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{ws.name}</div>
      <div style={{ fontSize: 11, color: color.textSubtle, marginTop: 5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", fontFamily: "ui-monospace, monospace" }}>{ws.workspace_id}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 9, paddingRight: 34 }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: color.goodText, background: color.goodBg, border: `1px solid ${color.goodBorder}`, padding: "2px 7px", borderRadius: radius.pill }}>{ws.sync_status}</span>
        <span style={{ fontSize: 11, color: color.textSubtle, background: color.neutralBg, padding: "2px 7px", borderRadius: 5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{ws.scope_revision}</span>
      </div>
      {/* 设置：点开菜单（编辑/删除）；stopPropagation 防触发卡片单选 */}
      <WsCardMenu onEdit={onEdit} onDelete={onDelete} />
    </div>
  );
}

function WsCardMenu({ onEdit, onDelete }: { onEdit: () => void; onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const { hovered, bind } = useHover();
  return (
    <>
      <div {...bind} title="设置" onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        style={{ position: "absolute", bottom: 10, right: 10, width: 26, height: 26, borderRadius: 6, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", background: open || hovered ? color.neutralBg : "transparent", border: `1px solid ${open ? color.border : "transparent"}` }}>
        <Icon name="settings" size={15} color={color.textSubtle} />
      </div>
      {open ? (
        <>
          <div onClick={(e) => { e.stopPropagation(); setOpen(false); }} style={{ position: "fixed", inset: 0, zIndex: 40 }} />
          <div onClick={(e) => e.stopPropagation()}
            style={{ position: "absolute", bottom: 42, right: 10, zIndex: 41, background: "#fff", border: `1px solid ${color.border}`, borderRadius: 8, boxShadow: "0 8px 24px rgba(20,24,31,.16)", minWidth: 112, overflow: "hidden", padding: "4px 0" }}>
            <WsMenuItem icon="pencil" label="编辑" onClick={(e) => { e.stopPropagation(); setOpen(false); onEdit(); }} />
            <WsMenuItem icon="trash" label="删除" danger onClick={(e) => { e.stopPropagation(); setOpen(false); onDelete(); }} />
          </div>
        </>
      ) : null}
    </>
  );
}

function WsMenuItem({ icon, label, danger, onClick }: {
  icon: string; label: string; danger?: boolean; onClick: (e: React.MouseEvent) => void;
}) {
  const { hovered, bind } = useHover();
  const c = danger ? color.dangerText : color.textNav;
  return (
    <div {...bind} onClick={onClick}
      style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", cursor: "pointer", fontSize: 12.5, fontWeight: 500, color: c, background: hovered ? (danger ? "#fdf3f3" : "#f5f7fa") : "#fff", whiteSpace: "nowrap" }}>
      <Icon name={icon} size={14} color={c} />{label}
    </div>
  );
}
 
function NewWsButton({ onClick }: { onClick: () => void }) {
  const { hovered, bind } = useHover();
  const borderColor = hovered ? "rgba(22,131,255,.4)" : "rgb(226, 229, 234)";
  return (
    <div onClick={onClick} {...bind}
      style={{ minHeight: 104, border: `2px dashed ${borderColor}`, background: hovered ? "rgba(22,131,255,.05)" : "rgba(247,248,250,.5)", borderRadius: radius.xl, cursor: "pointer", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8 }}>
      <div style={{ width: 32, height: 32, borderRadius: "50%", background: "#fff", border: "1px solid rgb(226, 229, 234)", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <Icon name="plus" size={17} color={color.brand} />
      </div>
      <span style={{ fontSize: 13, fontWeight: 600, color: color.brand }}>新建系统范围</span>
    </div>
  );
}
 
function AddModelButton({ onClick }: { onClick: () => void }) {
  const { hovered, bind } = useHover();
  const borderColor = hovered ? "rgba(22,131,255,.4)" : "rgb(226, 229, 234)";
  return (
    <button type="button" onClick={onClick} {...bind}
      style={{ width: "100%", border: `2px dashed ${borderColor}`, background: hovered ? "rgba(22,131,255,.02)" : "#fff", borderRadius: radius.xl, padding: "11px 0", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 7, color: color.brand, fontSize: 13, fontWeight: 600, fontFamily: "inherit" }}>
      <Icon name="plus" size={16} color={color.brand} />添加自定义模型
    </button>
  );
}

/** 页内内联「添加自定义模型」表单（原型口径；提交链与设置页弹窗共用 submitCustomLlm）。 */
function InlineAddModel({ onCancel, onCreated }: {
  onCancel: () => void;
  onCreated: (id: string, label: string, meta: CustomLlmMeta) => void;
}) {
  const [displayName, setDisplayName] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://");
  const [modelName, setModelName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [contextWindow, setContextWindow] = useState(DEFAULT_CONTEXT_WINDOW);
  const [headerRows, setHeaderRows] = useState<HeaderRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const test = useConnTest();

  // 探测相关字段（含自定义 Header）一改即让上次「测试连接」失效，须重测才能保存
  const onBaseUrl = (v: string) => { setBaseUrl(v); test.reset(); };
  const onModelName = (v: string) => { setModelName(v); test.reset(); };
  const onApiKey = (v: string) => { setApiKey(v); test.reset(); };
  const onHeaders = (rows: HeaderRow[]) => { setHeaderRows(rows); test.reset(); };

  const fieldsOk = displayName.trim().length > 0 && /^https?:\/\/.+/.test(baseUrl.trim())
    && modelName.trim().length > 0 && apiKey.trim().length > 0 && contextWindow > 0;
  const canSave = fieldsOk && test.state === "ok";

  const runTest = () => {
    if (!fieldsOk || test.state === "testing") return;
    void test.run(() => api.testLlmConnection({ base_url: baseUrl.trim(), model_name: modelName.trim(), api_key: apiKey.trim(), extra_headers: headersToRecord(headerRows) }));
  };

  const submit = () => {
    if (!canSave || busy) return;
    setBusy(true);
    setError("");
    submitCustomLlm({ displayName, baseUrl, modelName, apiKey, contextWindow, extraHeaders: headersToRecord(headerRows) })
      .then((r) => onCreated(r.id, r.label, { modelName: modelName.trim(), baseUrl: baseUrl.trim() }))
      .catch((e: unknown) => { setError(e instanceof Error ? e.message : "创建失败"); setBusy(false); });
  };

  return (
    <div style={{ border: `1px solid rgba(22,131,255,.30)`, background: "#fafcff", borderRadius: radius.xl, padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <span style={{ fontSize: 13, fontWeight: 700 }}>添加自定义模型</span>
        <Icon name="x" size={16} color={color.textSubtle} onClick={busy ? undefined : onCancel} />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 11 }}>
        <InlineField label="展示名" required><TextInput value={displayName} onChange={setDisplayName} placeholder="例：我的 GPT-4o" /></InlineField>
        <div style={{ display: "flex", gap: 10 }}>
          <div style={{ flex: 1 }}>
            <InlineField label="接口协议">
              <div style={{ height: 36, display: "flex", alignItems: "center", padding: "0 11px", fontSize: 13, color: color.textNav, background: color.neutralBg, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, boxSizing: "border-box" }}>{PROTOCOL_LABEL}</div>
            </InlineField>
          </div>
          <div style={{ flex: 1 }}>
            <InlineField label="上下文长度（token）">
              <input type="number" min={1} step={1000} value={contextWindow} onChange={(e) => setContextWindow(Number(e.target.value) || 0)} placeholder="128000"
                style={{ width: "100%", height: 36, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, padding: "0 11px", fontSize: 13, outline: "none", boxSizing: "border-box" }} />
            </InlineField>
          </div>
        </div>
        <InlineField label="API 地址" required><TextInput value={baseUrl} onChange={onBaseUrl} placeholder="https://api.example.com/v1" mono /></InlineField>
        <div style={{ display: "flex", gap: 10 }}>
          <div style={{ flex: 1 }}>
            <InlineField label="模型标识" required><TextInput value={modelName} onChange={onModelName} placeholder="gpt-4o" mono /></InlineField>
          </div>
          <div style={{ flex: 1 }}>
            <InlineField label="API Key" required>
              <input type="text" value={apiKey} onChange={(e) => onApiKey(e.target.value)} placeholder="sk-…" autoComplete="new-password"
                style={{ width: "100%", height: 36, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, padding: "0 11px", fontSize: 13, outline: "none", boxSizing: "border-box", fontFamily: "ui-monospace, monospace" }} />
            </InlineField>
          </div>
        </div>
        <InlineField label="自定义 Header（可选）">
          <HeadersEditor rows={headerRows} onChange={onHeaders} disabled={busy} />
        </InlineField>
        <div style={{ fontSize: 11, color: color.textSubtle }}>须先「测试连接」通过（须支持 tool calling）才能添加；Key 明文仅此刻提交、加密存 user_secret，接口永不回显。</div>
        {error ? <div style={{ fontSize: 12, color: color.dangerText }}>{error}</div> : null}
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Button variant="secondary" icon={test.state === "testing" ? "loader-2" : "plug-connected"}
            disabled={!fieldsOk || test.state === "testing"} onClick={runTest}>测试连接</Button>
          <ConnTestResult state={test.state} reason={test.reason} mock={test.mock} />
          <div style={{ flex: 1 }} />
          <Button variant="secondary" onClick={onCancel} disabled={busy}>取消</Button>
          <Button icon={busy ? "loader-2" : "plus"} disabled={!canSave || busy} onClick={submit}
            title={!canSave && fieldsOk ? "请先测试连接通过" : undefined}>{busy ? "创建中…" : "添加"}</Button>
        </div>
      </div>
    </div>
  );
}

function InlineField({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 12, fontWeight: 600, color: color.textSubtle, marginBottom: 5 }}>
        {label}{required ? <span style={{ color: color.dangerText }}> *</span> : null}
      </div>
      {children}
    </div>
  );
}

// 能力卡文案（激活页「能力识别」，原型 STEP2 并入）：模板 capabilities 中文名 → 图标+描述
const CAP_META: Record<string, { icon: string; desc: string }> = {
  "巡检": { icon: "radar", desc: "基于看护范围查看健康状态、异常信号与风险，只做查询不做变更。" },
  // 「诊断」为现口径（seed 新库）；「定界」键保留兼容存量环境的旧模板 label（管理员未同步前仍显示旧词）
  "诊断": { icon: "stethoscope", desc: "结合告警/指标/日志/链路/拓扑按五步法诊断问题边界，输出证据与假设排行。" },
  "定界": { icon: "stethoscope", desc: "结合告警/指标/日志/链路/拓扑判断问题边界，输出证据与假设排行。" },
  "恢复": { icon: "first-aid-kit", desc: "执行受控恢复动作：先核对目标与影响面，需人工批准后执行。" },
};

function StepActivate({ name, activating, editing }: { name: string; activating: boolean; editing?: boolean }) {
  const items = editing
    ? ["更新名称与系统看护范围", "模型有变更时生成新配置版本（历史版本保留）", "返回 Agent 清单"]
    : ["创建 AgentTeam 实例", "同步系统范围（OModel 就绪）", "装配默认能力", "启动 Agent 服务"];
  return (
    <div style={{ padding: "8px 0" }}>
      <div style={{ textAlign: "center" }}>
        <div style={{ width: 64, height: 64, borderRadius: 18, background: color.brandGrad, display: "inline-flex", alignItems: "center", justifyContent: "center", boxShadow: shadow.brand, marginBottom: 18, animation: activating ? "omPulse 1.4s ease-in-out infinite" : undefined }}>
          <Icon name={activating ? "loader-2" : "rocket"} size={30} color="#fff" spin={activating} />
        </div>
        <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 6px", overflowWrap: "anywhere", wordBreak: "break-word" }}>
          {editing ? (activating ? "正在保存…" : `保存对「${name || "Agent"}」的修改`) : (activating ? "正在激活…" : `「${name || "新 Agent"}」激活成功`)}
        </h2>
        <div style={{ fontSize: 13, color: color.textSubtle, marginBottom: 22 }}>
          {editing ? "保存将更新实例信息，必要时生成新配置版本，然后返回 Agent 清单。" : "激活将创建实例与初始配置版本，然后进入对话工作台。"}
        </div>
        <div style={{ maxWidth: 380, margin: "0 auto", textAlign: "left", display: "flex", flexDirection: "column", gap: 10 }}>
          {items.map((it, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13, color: color.textBody }}>
              <Icon name={activating ? "loader-2" : "circle-check"} size={17} color={activating ? color.textSubtle : color.good} spin={activating} />{it}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
