import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { color, radius, shadow } from "../theme/tokens";
import { Icon, useHover, Button, TextInput } from "../ui";
import { api } from "../lib/api";
import { useApp } from "../lib/appState";
import type { Template, Workspace } from "../lib/api/types";
import { WorkspaceDialog } from "./WorkspaceDialog";
import { submitCustomLlm } from "../settings/AddCustomModelDialog";
import { PendingQuestionNotice } from "../pages/states";

// 2026-07-13 改版（对齐更新版设计原型）：五步 → 三步，原 填写信息/系统范围/配置模型 合并为「配置 Agent」一页。
// 「选择模板」「激活 Agent」两个字面量被 e2e 断言依赖，改名须同步 e2e/smoke.spec.ts。
const STEPS = ["选择模板", "配置 Agent", "激活 Agent"];

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
  const [llm, setLlm] = useState<"platform" | "custom">("platform");
  const [customLlmId, setCustomLlmId] = useState("");
  const [customLlmLabel, setCustomLlmLabel] = useState("");
  const [customLlmMeta, setCustomLlmMeta] = useState<CustomLlmMeta | null>(null);
  const [wsDialog, setWsDialog] = useState(false);
  const [activating, setActivating] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (editing && instanceId) {
      // 编辑态：并取模板/范围/实例详情/模型清单，预填 名称+范围+模板（锁定）+模型
      Promise.all([api.getTemplates(), api.getWorkspaces(), api.getAgentTeam(instanceId), api.getModelConfigs()])
        .then(([tpls, wss, d, models]) => {
          setTemplates(tpls);
          setWorkspaces(wss);
          setTplId(tpls.some((t) => t.template_version_id === d.template_version_id)
            ? d.template_version_id : tpls[0]?.template_version_id ?? "");
          setName(d.name);
          setWsId(d.workspace_id);
          const llmId = typeof d.overlay.user_llm_config_id === "string" ? d.overlay.user_llm_config_id : "";
          if (llmId) {
            setLlm("custom");
            setCustomLlmId(llmId);
            setCustomLlmLabel(models.find((m) => m.llm_config_id === llmId)?.label ?? "自定义模型");
          }
        })
        .catch((e: unknown) => setErr(e instanceof Error ? e.message : "加载失败，请重试"));
      return;
    }
    api.getTemplates().then((t) => { setTemplates(t); setTplId(t[0]?.template_version_id ?? ""); });
    api.getWorkspaces().then(setWorkspaces);
  }, [editing, instanceId]);

  // 合并页门条件：名称 + 范围 + 模型（custom 分支须已创建，否则是死路）
  const canNext = [!!tplId, !!name.trim() && !!wsId && (llm === "platform" || !!customLlmId), true][step];

  const activate = () => {
    setActivating(true);
    setErr("");
    const done = editing && instanceId
      // 编辑态：更新同一实例（模板不送），刷新全局列表后回清单页
      ? api.updateAgentTeam(instanceId, {
          name, workspace_id: wsId,
          user_llm_config_id: llm === "custom" && customLlmId ? customLlmId : null,
        }).then(() => { refresh(); nav("/agents"); })
      : api.createAgentTeam({
          template_version_id: tplId, name, workspace_id: wsId,
          initial_overlay_json: llm === "custom" && customLlmId ? { user_llm_config_id: customLlmId } : undefined,
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
          {step === 0 ? <StepTemplate templates={templates} tplId={tplId} onPick={setTplId} locked={editing} /> : null}
          {step === 1 ? (
            <StepConfigure
              name={name} onName={setName}
              workspaces={workspaces} wsId={wsId} onPickWs={setWsId} onCreateWs={() => setWsDialog(true)}
              llm={llm} onLlm={setLlm}
              customLlmId={customLlmId} customLlmLabel={customLlmLabel} customLlmMeta={customLlmMeta}
              onCustomCreated={(id, label, meta) => {
                setCustomLlmId(id); setCustomLlmLabel(label); setCustomLlmMeta(meta); setLlm("custom");
              }}
              onCustomRemoved={() => { setCustomLlmId(""); setCustomLlmLabel(""); setCustomLlmMeta(null); setLlm("platform"); }}
            />
          ) : null}
          {step === 2 ? <StepActivate name={name} activating={activating} capabilities={tpl?.capabilities ?? []} editing={editing} /> : null}
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
              {editing ? (activating ? "保存中…" : "保存修改") : (activating ? "激活中…" : "激活 Agent")}
            </Button>
          )}
        </div>
      </div>

      <WorkspaceDialog open={wsDialog} onClose={() => setWsDialog(false)}
        onCreated={(id) => {
          // 创建成功：选中新范围 + 重拉列表（否则配置页网格还是旧的，看不到刚建的 workspace）
          setWsId(id); setWsDialog(false);
          api.getWorkspaces().then(setWorkspaces);
        }} />
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

function StepTemplate({ templates, tplId, onPick, locked }: { templates: Template[]; tplId: string; onPick: (id: string) => void; locked?: boolean }) {
  return (
    <>
      <Title t="选择模板" d={locked
        ? "编辑模式下模板不可更换（模板升级由平台统一发布），确认后进入下一步修改配置。"
        : "你将基于平台模板实例化一个 AgentTeam（用户视角单 Agent，背后 main + sub 由平台维护）。"} />
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {templates.map((tpl) => {
          const on = tpl.template_version_id === tplId;
          return <TemplateCard key={tpl.template_version_id} tpl={tpl} on={on} locked={locked} onPick={onPick} />;
        })}
      </div>
    </>
  );
}

function TemplateCard({ tpl, on, locked, onPick }: { tpl: Template; on: boolean; locked?: boolean; onPick: (id: string) => void }) {
  const { hovered, bind } = useHover();
  const borderColor = on ? color.brand : hovered ? "#1890FF" : "rgb(226, 229, 234)";
  return (
    <div onClick={() => { if (!locked) onPick(tpl.template_version_id); }} {...(locked ? {} : bind)}
      style={{ border: `1px solid ${borderColor}`, background: on ? color.brandTintBg : "#fff", borderRadius: radius.xxl, padding: 18, cursor: locked ? "default" : "pointer", display: "flex", gap: 14, alignItems: "flex-start", opacity: locked && !on ? 0.55 : 1 }}>
      <div style={{ width: 42, height: 42, borderRadius: radius.lg, background: color.brandTintBg, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 42px" }}>
        <Icon name="robot" size={22} color={color.brand} />
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 15, fontWeight: 700 }}>{tpl.name}</span>
          <span style={{ fontSize: 11, fontWeight: 600, color: color.brandStrong, background: "#fff", border: `1px solid ${color.brandTintBorder}`, padding: "2px 8px", borderRadius: radius.sm }}>{tpl.active_version} · active</span>
        </div>
        <div style={{ fontSize: 12.5, color: color.textMuted, margin: "5px 0 9px", lineHeight: 1.6 }}>{tpl.desc}</div>
        <div style={{ display: "flex", gap: 6 }}>
          {tpl.capabilities.map((c) => (
            <span key={c} style={{ fontSize: 11.5, fontWeight: 600, color: color.textNav, background: color.neutralBg, border: "1px solid rgb(226, 229, 234)", padding: "3px 9px", borderRadius: radius.pill }}>{c}</span>
          ))}
        </div>
      </div>
      <div style={{ width: 20, height: 20, borderRadius: "50%", border: `2px solid ${on ? color.brand : "#cfd3da"}`, display: "flex", alignItems: "center", justifyContent: "center", marginTop: 4 }}>
        {on ? <div style={{ width: 9, height: 9, borderRadius: "50%", background: color.brand }} /> : null}
      </div>
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

/** 合并配置页（原型 STEP1）：名称 → 身份确认 → 模型供应商 → 系统看护范围。 */
function StepConfigure({
  name, onName,
  workspaces, wsId, onPickWs, onCreateWs,
  llm, onLlm,
  customLlmId, customLlmLabel, customLlmMeta, onCustomCreated, onCustomRemoved,
}: {
  name: string; onName: (v: string) => void;
  workspaces: Workspace[]; wsId: string; onPickWs: (id: string) => void; onCreateWs: () => void;
  llm: "platform" | "custom"; onLlm: (v: "platform" | "custom") => void;
  customLlmId: string; customLlmLabel: string; customLlmMeta: CustomLlmMeta | null;
  onCustomCreated: (id: string, label: string, meta: CustomLlmMeta) => void;
  onCustomRemoved: () => void;
}) {
  const { me } = useApp(); // 身份确认用真实登录账号（IAM 开启=工号/姓名；mock=演示身份）
  const [showAdd, setShowAdd] = useState(false);
  const selectedWs = workspaces.find((w) => w.workspace_id === wsId);
  return (
    <>
      <div style={{ background: "#fff", border: "1px solid rgb(226, 229, 234)", borderRadius: radius.xxl, padding: "26px 28px", display: "flex", flexDirection: "column", gap: 24 }}>
      <div style={{ marginBottom: 2 }}>
          <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 6px" }}>配置 Agent</h2>
          <div style={{ fontSize: 13, color: color.textSubtle }}>一页完成：名称、模型与看护范围；身份使用你当前的登录账号。</div>
        </div>
        {/* ① 名称 */}
        <div>
          <SectionLabel text="Agent 名称" required />
          <div style={{ fontSize: 12, color: color.textSubtle, margin: "0 0 8px", lineHeight: 1.55 }}>
            建议用「看护系统范围 + 感知快恢Agent」，如「运行观测-感知快恢Agent」，便于快速识别 Agent 看护的系统范围。
          </div>
          <TextInput value={name} onChange={onName} placeholder="如：支付域-感知快恢Agent" />
          {!name.trim() ? <div style={{ fontSize: 12, color: color.dangerText, marginTop: 6 }}>名称必填</div> : null}
        </div>

        {/* ② 身份确认 */}
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12, border: `1px solid rgb(226, 229, 234)`, borderRadius: radius.xl, background: "rgba(247,248,250,.5)", padding: 14 }}>
          <div style={{ width: 32, height: 32, borderRadius: radius.md, background: color.brandTintBg, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 32px" }}>
            <Icon name="user" size={17} color={color.brand} />
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 3 }}>身份确认</div>
            <div style={{ fontSize: 12, color: color.textSubtle, lineHeight: 1.6 }}>
              你将以 <span style={{ fontWeight: 600, color: color.textStrong }}>{me ? `${me.user_id}（${me.display_name}）` : "当前登录账号"}</span> 的身份创建此 Agent，所有操作将关联到该账号。
            </div>
          </div>
        </div>

        {/* ③ 模型供应商 */}
        <div>
          <SectionLabel text="模型供应商" />
          <div style={{ fontSize: 12, color: color.textSubtle, margin: "0 0 10px" }}>选择 Agent 使用的模型：可用平台提供的模型，也可接入自带模型（OpenAI 兼容，须支持 tool calling）。</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <LlmCard selected={llm === "platform"} onClick={() => onLlm("platform")}
              icon="sparkles" iconBg={color.brandTintBg} iconColor={color.brand}
              label="平台默认模型（Qwen3.5）" badge="平台提供" />

            {customLlmId ? (
              <LlmCard selected={llm === "custom"} onClick={() => onLlm("custom")}
                icon="cpu" iconBg={color.neutralBg} iconColor={color.textNav}
                label={customLlmLabel} badge="可用" badgeTone="good"
                extra={customLlmMeta ? <div style={{ fontSize: 11, color: color.textSubtle, marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", fontFamily: "ui-monospace, monospace" }}>{customLlmMeta.baseUrl}</div> : null}
                trailing={<Icon name="trash" size={16} color={color.textFaint} title="删除该自定义模型（回退平台默认）" onClick={onCustomRemoved} />} />
            ) : null}

            {showAdd ? (
              <InlineAddModel
                onCancel={() => setShowAdd(false)}
                onCreated={(id, label, meta) => { setShowAdd(false); onCustomCreated(id, label, meta); }}
              />
            ) : !customLlmId ? (
 <AddModelButton onClick={() => setShowAdd(true)} />
            ) : null}
          </div>
        </div>

        {/* ④ 系统看护范围 */}
        <div>
          <SectionLabel text="系统看护范围" required
            right={<span style={{ fontSize: 12, color: color.textSubtle }}>已选：{selectedWs?.name ?? "—"}</span>} />
          <div style={{ fontSize: 12, color: color.textSubtle, margin: "0 0 12px" }}>选择 Agent 看护的系统范围（workspace = 命名的 APPID 集合）；运行时范围由 oModel 按你的授权解析。</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 12 }}>
                     {workspaces.map((ws) => (
              <WorkspaceCard key={ws.workspace_id} ws={ws} on={ws.workspace_id === wsId} onPick={onPickWs} />
            ))}
            <NewWsButton onClick={onCreateWs} />
          </div>
          {!wsId ? <div style={{ fontSize: 12, color: color.dangerText, marginTop: 8 }}>请选择或新建一个系统范围</div> : null}
        </div>
      </div>
    </>
  );
}

function LlmCard({ selected, onClick, icon, iconBg, iconColor, label, badge, badgeTone, extra, trailing }: {
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
      <div style={{ flex: 1, display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>{label}</span>
        {badge ? <span style={{ fontSize: 10, color: badgeTone === "good" ? color.goodText : color.textSubtle, background: badgeTone === "good" ? color.goodBg : color.neutralBg, padding: "2px 6px", borderRadius: 5 }}>{badge}</span> : null}
      </div>
      {extra}
      {trailing}
      <RadioDot on={selected} />
    </div>
  );
}
 
function WorkspaceCard({ ws, on, onPick }: { ws: Workspace; on: boolean; onPick: (id: string) => void }) {
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
      <div style={{ fontSize: 14, fontWeight: 600, paddingRight: 24 }}>{ws.name}</div>
      <div style={{ fontSize: 11, color: color.textSubtle, marginTop: 5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", fontFamily: "ui-monospace, monospace" }}>{ws.workspace_id}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 9 }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: color.goodText, background: color.goodBg, border: `1px solid ${color.goodBorder}`, padding: "2px 7px", borderRadius: radius.pill }}>{ws.sync_status}</span>
        <span style={{ fontSize: 11, color: color.textSubtle, background: color.neutralBg, padding: "2px 7px", borderRadius: 5 }}>{ws.scope_revision}</span>
      </div>
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const ok = displayName.trim().length > 0 && /^https?:\/\/.+/.test(baseUrl.trim())
    && modelName.trim().length > 0 && apiKey.trim().length > 0;

  const submit = () => {
    if (!ok || busy) return;
    setBusy(true);
    setError("");
    submitCustomLlm({ displayName, baseUrl, modelName, apiKey })
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
        <InlineField label="API 地址" required><TextInput value={baseUrl} onChange={setBaseUrl} placeholder="https://api.example.com/v1" mono /></InlineField>
        <div style={{ display: "flex", gap: 10 }}>
          <div style={{ flex: 1 }}>
            <InlineField label="模型标识" required><TextInput value={modelName} onChange={setModelName} placeholder="gpt-4o" mono /></InlineField>
          </div>
          <div style={{ flex: 1 }}>
            <InlineField label="API Key" required>
              <input type="text" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-…" autoComplete="new-password"
                style={{ width: "100%", height: 36, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, padding: "0 11px", fontSize: 13, outline: "none", boxSizing: "border-box", fontFamily: "ui-monospace, monospace" }} />
            </InlineField>
          </div>
        </div>
        <div style={{ fontSize: 11, color: color.textSubtle }}>接口协议：OpenAI 兼容（须支持 tool calling）；Key 明文仅此刻提交、加密存 user_secret，接口永不回显。</div>
        {error ? <div style={{ fontSize: 12, color: color.dangerText }}>{error}</div> : null}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <Button variant="secondary" onClick={onCancel} disabled={busy}>取消</Button>
          <Button icon={busy ? "loader-2" : "plus"} disabled={!ok || busy} onClick={submit}>{busy ? "创建并探测中…" : "添加"}</Button>
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
  "定界": { icon: "stethoscope", desc: "结合告警/指标/日志/链路/拓扑判断问题边界，输出证据与假设排行。" },
  "恢复": { icon: "first-aid-kit", desc: "执行受控恢复动作：先核对目标与影响面，需人工批准后执行。" },
};

function StepActivate({ name, activating, capabilities, editing }: { name: string; activating: boolean; capabilities: string[]; editing?: boolean }) {
  const items = editing
    ? ["更新名称与系统看护范围", "模型有变更时生成新配置版本（历史版本保留）", "返回 Agent 清单"]
    : ["创建 AgentTeam 实例", "同步系统范围（oModel 就绪）", "装配模板默认能力（巡检 / 定界 / 恢复）", "启动 Agent 服务"];
  return (
    <div style={{ padding: "8px 0" }}>
      {/* 能力识别（原型「确认 Agent 能力清单」并入）：所选模板已具备的内置能力 */}
      {capabilities.length ? (
        <div style={{ marginBottom: 26 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: color.textStrong, marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
            <Icon name="bolt" size={16} color={color.brand} />能力识别
            <span style={{ fontSize: 11, fontWeight: 400, color: color.textSubtle }}>Agent 已具备的内置能力</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
            {capabilities.map((c) => {
              const meta = CAP_META[c] ?? { icon: "bolt", desc: "平台内置能力。" };
              return (
                <div key={c} style={{ border: `1px solid rgb(226, 229, 234)`, borderRadius: radius.xl, padding: 15, display: "flex", alignItems: "flex-start", gap: 11, background: "#fff" }}>
                  <div style={{ width: 40, height: 40, borderRadius: radius.lg, background: color.brandTintBg, color: color.brand, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 40px" }}>
                    <Icon name={meta.icon} size={21} color={color.brand} />
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 3 }}>
                      <span style={{ fontSize: 14, fontWeight: 600, whiteSpace: "nowrap" }}>{c}</span>
                      <span style={{ fontSize: 10, fontWeight: 600, color: color.goodText, background: color.goodBg, padding: "2px 6px", borderRadius: 5, whiteSpace: "nowrap" }}>已识别</span>
                    </div>
                    <p style={{ fontSize: 12, color: color.textSubtle, margin: 0, lineHeight: 1.55 }}>{meta.desc}</p>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      <div style={{ textAlign: "center" }}>
        <div style={{ width: 64, height: 64, borderRadius: 18, background: color.brandGrad, display: "inline-flex", alignItems: "center", justifyContent: "center", boxShadow: shadow.brand, marginBottom: 18, animation: activating ? "omPulse 1.4s ease-in-out infinite" : undefined }}>
          <Icon name={activating ? "loader-2" : "rocket"} size={30} color="#fff" spin={activating} />
        </div>
        <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 6px" }}>
          {editing ? (activating ? "正在保存…" : `保存对「${name || "Agent"}」的修改`) : (activating ? "正在激活…" : `准备激活「${name || "新 Agent"}」`)}
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
