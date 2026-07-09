import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { color, radius } from "../theme/tokens";
import { Icon, Interactive, Pill, Button, TextInput } from "../ui";
import { useApp } from "../lib/appState";
import { api } from "../lib/api";
import type { AssetRow, ConfigVersionRow, ModelOption } from "../lib/api/types";

type Tab = "lib" | "model";

/** 实例配置（isSettings）：Agent 卡片总览 → per-agent（Skill·MCP 库 / 模型配置）。 */
export function SettingsPage() {
  const nav = useNavigate();
  const { agents } = useApp();
  const [detailId, setDetailId] = useState<string | null>(null);
  const detail = agents.find((a) => a.instance_id === detailId) ?? null;

  return (
    <>
      <header style={{ flex: "0 0 auto", height: 56, borderBottom: `1px solid ${color.border}`, background: "#fff", display: "flex", alignItems: "center", padding: "0 24px", gap: 12 }}>
        {detail ? (
          <Interactive as="button" onClick={() => setDetailId(null)}
            baseStyle={{ border: `1px solid ${color.border}`, background: "#fff", cursor: "pointer", width: 32, height: 32, borderRadius: radius.md, display: "inline-flex", alignItems: "center", justifyContent: "center", color: "#697283" }}
            hoverStyle={{ background: color.pageBg }}>
            <Icon name="arrow-left" size={17} />
          </Interactive>
        ) : null}
        <div style={{ fontSize: 15, fontWeight: 700 }}>{detail ? detail.name : "Agent 设置"}</div>
        <span style={{ fontSize: 12, color: color.textSubtle }}>{detail ? `${detail.template} · ${detail.workspace_label}` : "维护你拥有的 AgentTeam 实例：模型、Skill、HTTP MCP 与配置版本"}</span>
      </header>

      {detail ? <AgentDetail /> : <AgentCards onOpen={setDetailId} onNew={() => nav("/init")} />}
    </>
  );
}

function AgentCards({ onOpen, onNew }: { onOpen: (id: string) => void; onNew: () => void }) {
  const { agents } = useApp();
  return (
    <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "26px 24px 40px" }}>
      <div style={{ maxWidth: 1000, margin: "0 auto" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 16 }}>
          {agents.map((ag) => (
            <Interactive key={ag.instance_id} onClick={() => onOpen(ag.instance_id)}
              baseStyle={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xxl, padding: 18, cursor: "pointer", display: "flex", flexDirection: "column", gap: 12 }}
              hoverStyle={{ borderColor: color.brand, boxShadow: "0 4px 14px rgba(22,131,255,.10)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
                <div style={{ width: 40, height: 40, borderRadius: radius.lg, background: color.brandTintBg, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 40px" }}>
                  <Icon name="robot" size={21} color={color.brand} />
                </div>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 14.5, fontWeight: 700, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{ag.name}</div>
                  <div style={{ fontSize: 11.5, color: color.textSubtle, marginTop: 2 }}>{ag.template}</div>
                </div>
                <Pill tone="good">active</Pill>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: color.textMuted }}>
                <Icon name="target" size={14} color={color.textSubtle} />{ag.workspace_label}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, borderTop: `1px solid ${color.borderFaint}`, paddingTop: 12 }}>
                <span style={{ fontSize: 12, color: color.textNav }}>{ag.counts} · 配置 {ag.active_config_version}</span>
                <div style={{ flex: 1 }} />
                <span style={{ fontSize: 12, fontWeight: 600, color: color.brand, display: "inline-flex", alignItems: "center", gap: 4, whiteSpace: "nowrap" }}>配置 Skill / MCP<Icon name="chevron-right" size={14} color={color.brand} /></span>
              </div>
            </Interactive>
          ))}
          <Interactive as="button" onClick={onNew}
            baseStyle={{ minHeight: 150, border: `2px dashed ${color.borderInput}`, background: "rgba(247,248,250,.5)", borderRadius: radius.xxl, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 9, cursor: "pointer" }}
            hoverStyle={{ borderColor: "rgba(22,131,255,.4)", background: "rgba(22,131,255,.04)" }}>
            <div style={{ width: 36, height: 36, borderRadius: "50%", background: color.brandTintBg, display: "flex", alignItems: "center", justifyContent: "center" }}><Icon name="plus" size={19} color={color.brand} /></div>
            <span style={{ fontSize: 13, fontWeight: 600, color: color.brand }}>新建 Agent · 进入初始化向导</span>
          </Interactive>
        </div>
      </div>
    </div>
  );
}

function AgentDetail() {
  const [tab, setTab] = useState<Tab>("lib");
  return (
    <>
      <div style={{ flex: "0 0 auto", background: "#fff", borderBottom: `1px solid ${color.border}`, padding: "0 24px", display: "flex", gap: 6 }}>
        {([["lib", "Skill · MCP 库", "puzzle"], ["model", "模型配置", "cpu"]] as const).map(([k, label, icon]) => {
          const active = tab === k;
          return (
            <div key={k} onClick={() => setTab(k)} style={{ display: "flex", alignItems: "center", gap: 6, padding: "13px 4px", margin: "0 8px", cursor: "pointer", fontSize: 13.5, fontWeight: active ? 700 : 500, color: active ? color.brand : color.textNav, borderBottom: `2px solid ${active ? color.brand : "transparent"}` }}>
              <Icon name={icon} size={16} />{label}
            </div>
          );
        })}
      </div>
      {tab === "lib" ? <LibTab /> : <ModelTab />}
    </>
  );
}

function LibTab() {
  const [search, setSearch] = useState("");
  const [bound, setBound] = useState<AssetRow[]>([]);
  const [lib, setLib] = useState<AssetRow[]>([]);
  const [versions, setVersions] = useState<ConfigVersionRow[]>([]);
  useEffect(() => {
    api.getBoundSkills().then(setBound);
    api.getSkillLibrary().then(setLib);
    api.getConfigVersions().then(setVersions);
  }, []);

  return (
    <div style={{ flex: 1, minHeight: 0, overflowY: "auto", background: color.surfaceAlt, padding: "22px 30px 40px" }}>
      <div style={{ maxWidth: 860 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
          <div style={{ width: 340 }}><TextInput value={search} onChange={setSearch} placeholder="搜索 Skill / HTTP MCP…" icon="search" /></div>
          <div style={{ flex: 1 }} />
          <Button variant="secondary" icon="upload">上传 Skill</Button>
          <Button variant="secondary" icon="plug">注册 HTTP MCP</Button>
        </div>

        <SectionLabel>当前已绑定（main Agent）</SectionLabel>
        <AssetTable rows={bound} action="解绑" />

        <div style={{ height: 22 }} />
        <SectionLabel>我的资产库</SectionLabel>
        <AssetTable rows={lib} action="toggle" />

        <div style={{ height: 22 }} />
        <SectionLabel>配置版本历史</SectionLabel>
        <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, overflow: "hidden" }}>
          {versions.map((v, i) => (
            <div key={v.config_version_id} style={{ display: "grid", gridTemplateColumns: "70px 1fr 120px 120px", gap: 10, padding: "11px 16px", fontSize: 12.5, alignItems: "center", borderTop: i ? `1px solid ${color.borderFaint}` : "none" }}>
              <span style={{ fontWeight: 700 }}>{v.version_no}{v.status === "active" ? <Pill tone="good" style={{ marginLeft: 6 }}>active</Pill> : null}</span>
              <span style={{ color: color.textBody }}>{v.change_reason}</span>
              <span style={{ color: color.textSubtle }}>{v.created_by}</span>
              <span style={{ color: color.textSubtle, textAlign: "right" }}>{v.creation_date}</span>
            </div>
          ))}
        </div>
        <div style={{ marginTop: 12, padding: "11px 13px", borderRadius: radius.lg, background: color.brandTintBg, border: `1px solid rgba(22,131,255,.18)`, fontSize: 12, color: color.brandStrong, lineHeight: 1.6 }}>
          保存配置会生成新的 active 版本。运行中的 Run 不重建会话，但下一次模型/工具边界会重新编译 RuntimePlan 生效。
        </div>
      </div>
    </div>
  );
}

function ModelTab() {
  const [models, setModels] = useState<ModelOption[]>([]);
  const [picked, setPicked] = useState<string>("");
  useEffect(() => { api.getModelConfigs().then((m) => { setModels(m); setPicked(m.find((x) => x.current)?.llm_config_id ?? m[0]?.llm_config_id ?? ""); }); }, []);
  return (
    <div style={{ flex: 1, minHeight: 0, overflowY: "auto", background: color.surfaceAlt, padding: "26px 30px 40px" }}>
      <div style={{ maxWidth: 720 }}>
        <h2 style={{ fontSize: 19, fontWeight: 700, margin: "0 0 6px" }}>模型配置</h2>
        <div style={{ fontSize: 12.5, color: color.textSubtle, marginBottom: 22 }}>为该 Agent 选择默认模型；可使用平台模型，也可添加自己的模型（仅支持 OpenAI 兼容协议，且必须支持 tool calling）。</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {models.map((m) => {
            const on = picked === m.llm_config_id;
            return (
              <Interactive key={m.llm_config_id} onClick={() => setPicked(m.llm_config_id)}
                baseStyle={{ display: "flex", alignItems: "center", gap: 12, border: `1px solid ${on ? color.brand : color.border}`, background: on ? color.brandTintBg : "#fff", borderRadius: radius.xl, padding: "13px 15px", cursor: "pointer" }}
                hoverStyle={on ? {} : { borderColor: color.brandTintBorder }}>
                <Icon name="cpu" size={18} color={on ? color.brand : color.textSubtle} />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13.5, fontWeight: 600, color: color.textStrong }}>{m.label}</div>
                  <div style={{ fontSize: 11.5, color: color.textSubtle, marginTop: 2 }}>{m.note}</div>
                </div>
                <div style={{ width: 18, height: 18, borderRadius: "50%", border: `2px solid ${on ? color.brand : "#cfd3da"}`, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  {on ? <div style={{ width: 8, height: 8, borderRadius: "50%", background: color.brand }} /> : null}
                </div>
              </Interactive>
            );
          })}
        </div>
        <button style={{ marginTop: 12, border: `1px dashed #c9cdd6`, background: "#fff", cursor: "pointer", color: color.brand, fontSize: 12.5, fontWeight: 600, padding: "9px 15px", borderRadius: radius.lg, display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Icon name="plus" size={15} color={color.brand} />添加自定义模型（OpenAI 兼容）
        </button>
        <div style={{ marginTop: 16, padding: "11px 13px", borderRadius: radius.lg, background: color.brandTintBg, border: `1px solid rgba(22,131,255,.18)`, fontSize: 12, color: color.brandStrong, lineHeight: 1.6 }}>
          Secret 明文仅在创建时提交，保存后只显示脱敏指纹；探测失败的模型不能设为默认。对话输入框里的模型切换是会话级临时选择，不会修改这里的默认配置。
        </div>
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10, color: color.textStrong }}>{children}</div>;
}

function AssetTable({ rows, action }: { rows: AssetRow[]; action: "解绑" | "toggle" }) {
  return (
    <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, overflow: "hidden" }}>
      {rows.length === 0 ? (
        <div style={{ padding: "18px 16px", fontSize: 12.5, color: color.textSubtle }}>暂无资产，点右上「上传 Skill / 注册 MCP」。</div>
      ) : rows.map((r, i) => (
        <div key={r.id} style={{ display: "grid", gridTemplateColumns: "1fr 70px 90px 1fr 80px", gap: 10, padding: "12px 16px", fontSize: 12.5, alignItems: "center", borderTop: i ? `1px solid ${color.borderFaint}` : "none" }}>
          <span style={{ fontWeight: 600, color: color.textStrong }}>{r.name}</span>
          <span style={{ color: color.textSubtle }}>{r.version}</span>
          <Pill tone={r.statusTone}>{r.status}</Pill>
          <span style={{ color: color.textSubtle }}>{r.meta}</span>
          <span style={{ textAlign: "right", fontSize: 12, fontWeight: 600, color: color.brand, cursor: "pointer" }}>
            {action === "解绑" ? "解绑" : r.bound ? "已绑定" : "绑定"}
          </span>
        </div>
      ))}
    </div>
  );
}
