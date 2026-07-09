import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { color, radius, shadow } from "../theme/tokens";
import { Icon, Interactive, Button, TextInput } from "../ui";
import { api } from "../lib/api";
import type { Template, Workspace } from "../lib/api/types";
import { WorkspaceDialog } from "./WorkspaceDialog";

const STEPS = ["选择模板", "填写信息", "系统范围", "配置模型", "激活 Agent"];

/** 初始化向导（30.2）：模板 → 名称 → workspace → LLM → 激活。壳外全屏。 */
export function InitWizard() {
  const nav = useNavigate();
  const [step, setStep] = useState(0);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [tplId, setTplId] = useState("");
  const [name, setName] = useState("");
  const [wsId, setWsId] = useState("");
  const [llm, setLlm] = useState<"platform" | "custom">("platform");
  const [wsDialog, setWsDialog] = useState(false);
  const [activating, setActivating] = useState(false);

  useEffect(() => {
    api.getTemplates().then((t) => { setTemplates(t); setTplId(t[0]?.template_version_id ?? ""); });
    api.getWorkspaces().then(setWorkspaces);
  }, []);

  const canNext = [!!tplId, !!name.trim(), !!wsId, true, true][step];

  const activate = () => {
    setActivating(true);
    api.createAgentTeam({ template_version_id: tplId, name, workspace_id: wsId }).then((r) => {
      nav(`/agent-teams/${r.instance_id}/chat`);
    });
  };

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: color.pageBg }}>
      {/* header + stepper */}
      <header style={{ flex: "0 0 auto", height: 60, borderBottom: `1px solid ${color.border}`, background: "#fff", display: "flex", alignItems: "center", padding: "0 24px", gap: 14 }}>
        <div style={{ width: 30, height: 30, borderRadius: 8, background: color.brandGrad, display: "flex", alignItems: "center", justifyContent: "center", boxShadow: shadow.brand }}>
          <Icon name="robot" size={18} color="#fff" />
        </div>
        <div style={{ fontSize: 15, fontWeight: 800 }}>初始化 Agent</div>
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 0, maxWidth: 640, margin: "0 auto" }}>
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
        <div style={{ maxWidth: 680, margin: "0 auto" }}>
          {step === 0 ? <StepTemplate templates={templates} tplId={tplId} onPick={setTplId} /> : null}
          {step === 1 ? <StepName name={name} onName={setName} /> : null}
          {step === 2 ? <StepWorkspace workspaces={workspaces} wsId={wsId} onPick={setWsId} onCreate={() => setWsDialog(true)} /> : null}
          {step === 3 ? <StepLlm llm={llm} onLlm={setLlm} /> : null}
          {step === 4 ? <StepActivate name={name} activating={activating} /> : null}
        </div>
      </div>

      {/* footer */}
      <div style={{ flex: "0 0 auto", borderTop: `1px solid ${color.border}`, background: "#fff", padding: "14px 24px", display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ maxWidth: 680, margin: "0 auto", width: "100%", display: "flex", gap: 10 }}>
          {step > 0 ? <Button variant="secondary" icon="arrow-left" onClick={() => setStep((s) => s - 1)}>上一步</Button> : null}
          <div style={{ flex: 1 }} />
          {step < 4 ? (
            <Button icon="arrow-right" disabled={!canNext} onClick={() => setStep((s) => s + 1)}>下一步</Button>
          ) : (
            <Button icon="rocket" disabled={activating} onClick={activate}>{activating ? "激活中…" : "激活 Agent"}</Button>
          )}
        </div>
      </div>

      <WorkspaceDialog open={wsDialog} onClose={() => setWsDialog(false)} onCreated={(id) => { setWsId(id); setWsDialog(false); }} />
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

function StepTemplate({ templates, tplId, onPick }: { templates: Template[]; tplId: string; onPick: (id: string) => void }) {
  return (
    <>
      <Title t="选择模板" d="你将基于平台模板实例化一个 AgentTeam（用户视角单 Agent，背后 main + sub 由平台维护）。" />
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {templates.map((tpl) => {
          const on = tpl.template_version_id === tplId;
          return (
            <Interactive key={tpl.template_version_id} onClick={() => onPick(tpl.template_version_id)}
              baseStyle={{ border: `1px solid ${on ? color.brand : color.border}`, background: on ? color.brandTintBg : "#fff", borderRadius: radius.xxl, padding: 18, cursor: "pointer", display: "flex", gap: 14, alignItems: "flex-start" }}
              hoverStyle={on ? {} : { borderColor: color.brandTintBorder }}>
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
                    <span key={c} style={{ fontSize: 11.5, fontWeight: 600, color: color.textNav, background: color.neutralBg, border: `1px solid ${color.border}`, padding: "3px 9px", borderRadius: radius.pill }}>{c}</span>
                  ))}
                </div>
              </div>
              <div style={{ width: 20, height: 20, borderRadius: "50%", border: `2px solid ${on ? color.brand : "#cfd3da"}`, display: "flex", alignItems: "center", justifyContent: "center", marginTop: 4 }}>
                {on ? <div style={{ width: 9, height: 9, borderRadius: "50%", background: color.brand }} /> : null}
              </div>
            </Interactive>
          );
        })}
      </div>
    </>
  );
}

function StepName({ name, onName }: { name: string; onName: (v: string) => void }) {
  return (
    <>
      <Title t="填写 Agent 信息" d="给这个 Agent 起个名字；身份使用你当前的 W3 员工账号。" />
      <label style={{ fontSize: 13, fontWeight: 600, display: "block", marginBottom: 8 }}>Agent 名称</label>
      <TextInput value={name} onChange={onName} placeholder="例如：支付域感知快恢" />
      <div style={{ marginTop: 18, display: "flex", alignItems: "center", gap: 10, background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.lg, padding: "12px 14px" }}>
        <Icon name="user-circle" size={20} color={color.textSubtle} />
        <div>
          <div style={{ fontSize: 12.5, fontWeight: 600 }}>当前身份：0026demo01（林一）</div>
          <div style={{ fontSize: 11.5, color: color.textSubtle }}>W3 员工账号，只读</div>
        </div>
      </div>
    </>
  );
}

function StepWorkspace({ workspaces, wsId, onPick, onCreate }: { workspaces: Workspace[]; wsId: string; onPick: (id: string) => void; onCreate: () => void }) {
  return (
    <>
      <Title t="选择或创建系统范围" d="系统范围（workspace）= 命名的 APPID 集合 + scope_revision。运行时范围由 oModel 按你的授权解析。" />
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {workspaces.map((ws) => {
          const on = ws.workspace_id === wsId;
          return (
            <Interactive key={ws.workspace_id} onClick={() => onPick(ws.workspace_id)}
              baseStyle={{ border: `1px solid ${on ? color.brand : color.border}`, background: on ? color.brandTintBg : "#fff", borderRadius: radius.xl, padding: "14px 16px", cursor: "pointer", display: "flex", alignItems: "center", gap: 12 }}
              hoverStyle={on ? {} : { borderColor: color.brandTintBorder }}>
              <Icon name="target" size={18} color={color.brand} />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13.5, fontWeight: 600 }}>{ws.name}</div>
                <div style={{ fontSize: 11.5, color: color.textSubtle, marginTop: 2 }}>{ws.workspace_id} · {ws.scope_revision} · 更新于 {ws.updated}</div>
              </div>
              <span style={{ fontSize: 11, fontWeight: 600, color: color.goodText, background: color.goodBg, border: `1px solid ${color.goodBorder}`, padding: "2px 8px", borderRadius: radius.pill }}>{ws.sync_status}</span>
            </Interactive>
          );
        })}
        <Interactive as="button" onClick={onCreate}
          baseStyle={{ border: `2px dashed ${color.borderInput}`, background: "rgba(247,248,250,.5)", borderRadius: radius.xl, padding: "14px 16px", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 8, color: color.brand, fontSize: 13, fontWeight: 600 }}
          hoverStyle={{ borderColor: "rgba(22,131,255,.4)", background: "rgba(22,131,255,.04)" }}>
          <Icon name="plus" size={16} color={color.brand} />从应用树创建新的系统范围
        </Interactive>
      </div>
    </>
  );
}

function StepLlm({ llm, onLlm }: { llm: "platform" | "custom"; onLlm: (v: "platform" | "custom") => void }) {
  return (
    <>
      <Title t="配置模型" d="默认使用平台模型即可启动；也可添加自己的 OpenAI 兼容模型（必须支持 tool calling）。" />
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {([["platform", "使用平台默认模型（Qwen3.5）", "无需 Secret，直接启动"], ["custom", "使用我的自定义模型（OpenAI 兼容）", "填 base_url / model / API Key，创建时探测"]] as const).map(([v, t, d]) => {
          const on = llm === v;
          return (
            <Interactive key={v} onClick={() => onLlm(v)}
              baseStyle={{ border: `1px solid ${on ? color.brand : color.border}`, background: on ? color.brandTintBg : "#fff", borderRadius: radius.xl, padding: "14px 16px", cursor: "pointer", display: "flex", alignItems: "center", gap: 12 }}
              hoverStyle={on ? {} : { borderColor: color.brandTintBorder }}>
              <Icon name="cpu" size={18} color={on ? color.brand : color.textSubtle} />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13.5, fontWeight: 600 }}>{t}</div>
                <div style={{ fontSize: 11.5, color: color.textSubtle, marginTop: 2 }}>{d}</div>
              </div>
              <div style={{ width: 18, height: 18, borderRadius: "50%", border: `2px solid ${on ? color.brand : "#cfd3da"}`, display: "flex", alignItems: "center", justifyContent: "center" }}>
                {on ? <div style={{ width: 8, height: 8, borderRadius: "50%", background: color.brand }} /> : null}
              </div>
            </Interactive>
          );
        })}
      </div>
      <div style={{ marginTop: 16, padding: "11px 13px", borderRadius: radius.lg, background: color.brandTintBg, border: `1px solid rgba(22,131,255,.18)`, fontSize: 12, color: color.brandStrong, lineHeight: 1.6 }}>
        Secret 明文仅在创建时提交、加密存 user_secret，API 永不回显；探测失败的模型不能激活。
      </div>
    </>
  );
}

function StepActivate({ name, activating }: { name: string; activating: boolean }) {
  const items = ["创建 AgentTeam 实例", "同步系统范围（oModel 就绪）", "装配模板默认能力（巡检 / 定界 / 恢复）", "启动 Agent 服务"];
  return (
    <div style={{ textAlign: "center", padding: "20px 0" }}>
      <div style={{ width: 64, height: 64, borderRadius: 18, background: color.brandGrad, display: "inline-flex", alignItems: "center", justifyContent: "center", boxShadow: shadow.brand, marginBottom: 18, animation: activating ? "omPulse 1.4s ease-in-out infinite" : undefined }}>
        <Icon name={activating ? "loader-2" : "rocket"} size={30} color="#fff" spin={activating} />
      </div>
      <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 6px" }}>{activating ? "正在激活…" : `准备激活「${name || "新 Agent"}」`}</h2>
      <div style={{ fontSize: 13, color: color.textSubtle, marginBottom: 22 }}>激活将创建实例与初始配置版本，然后进入对话工作台。</div>
      <div style={{ maxWidth: 380, margin: "0 auto", textAlign: "left", display: "flex", flexDirection: "column", gap: 10 }}>
        {items.map((it, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13, color: color.textBody }}>
            <Icon name={activating ? "loader-2" : "circle-check"} size={17} color={activating ? color.textSubtle : color.good} spin={activating} />{it}
          </div>
        ))}
      </div>
    </div>
  );
}
