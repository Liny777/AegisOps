import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { color, radius } from "../theme/tokens";
import { Icon, Interactive, Pill, Button, TextInput, Toggle } from "../ui";
import { useApp, useSyncCurrentAgent } from "../lib/appState";
import { api } from "../lib/api";
import type { AgentInstance, AssetRow, ConfigVersionRow } from "../lib/api/types";

type Tab = "skill" | "mcp";
type Filter = "all" | "on" | "off";

/** 实例配置（isSettings·新原型）：视图完全由路由驱动 —— `/agent-teams/:id/settings` = 当前 Agent
 * 插件配置（侧栏「插件」直达，仅 Skill/MCP 两 tab——模型/提示词在编辑向导与创建向导里管），
 * `/agents` = 全部 Agent 清单（picker「全部 Agents」进入；新建→/init?new=1、编辑→编辑向导 /edit、
 * 启停/删除入口在卡片上）。切换视图一律 nav() 产生历史条目，返回统一 nav(-1)，来路由历史栈表达。 */
export function SettingsPage() {
  const nav = useNavigate();
  const { instanceId } = useParams();
  useSyncCurrentAgent(instanceId);  // 直链/新建实例进入：全局列表缺它则重拉（/agents 时 undefined no-op）
  const { agents, refresh } = useApp();
  const detail = instanceId ? agents.find((a) => a.instance_id === instanceId) ?? null : null;

  return (
    <>
      <header style={{ flex: "0 0 auto", height: 56, borderBottom: `1px solid ${color.border}`, background: "#fff", display: "flex", alignItems: "center", padding: "0 24px", gap: 12 }}>
        <Interactive as="button" onClick={() => nav(-1)}
          baseStyle={{ border: `1px solid ${color.border}`, background: "#fff", cursor: "pointer", width: 32, height: 32, borderRadius: radius.md, display: "inline-flex", alignItems: "center", justifyContent: "center", color: "#697283" }}
          hoverStyle={{ background: color.pageBg }}>
          <Icon name="arrow-left" size={17} />
        </Interactive>
        <div style={{ fontSize: 15, fontWeight: 700 }}>{detail ? detail.name : "Agent 设置"}</div>
        <span style={{ fontSize: 12, color: color.textSubtle }}>{detail ? `${detail.template} · ${detail.workspace_label}` : "管理你的全部 Agent"}</span>
        <div style={{ flex: 1 }} />
        {/* ?new=1：显式新建旁路 InitGuard 的「已有实例弹回工作台」（老用户建第二个实例的唯一入口） */}
        {!detail ? <Button icon="plus" onClick={() => nav("/init?new=1")}>新建 Agent</Button> : null}
      </header>

      {/* 编辑 → 编辑向导（预填名称/范围/模型，保存更新同一实例）；插件配置走侧栏「插件」入口 */}
      {detail ? <AgentDetail instance={detail} /> : <AgentListPage agents={agents} onOpen={(id) => nav(`/agent-teams/${id}/edit`)} onChanged={refresh} />}
    </>
  );
}

/** 新原型：清单页 = 大标题 + 搜索/状态筛选 + 卡片（Toggle/编辑/删除/对话）+ 空态。 */
function AgentListPage({ agents, onOpen, onChanged }: {
  agents: AgentInstance[]; onOpen: (id: string) => void; onChanged: () => void;
}) {
  const nav = useNavigate();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [confirmDel, setConfirmDel] = useState<AgentInstance | null>(null);
  const [creatingRunId, setCreatingRunId] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return agents.filter((a) => {
      if (filter === "on" && a.status !== "active") return false;
      if (filter === "off" && a.status !== "disabled") return false;
      if (q && !(a.name.toLowerCase().includes(q) || a.workspace_label.toLowerCase().includes(q))) return false;
      return true;
    });
  }, [agents, search, filter]);

  return (
    <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: 24 }}>
      <div style={{ maxWidth: 1160, margin: "0 auto" }}>
        <div style={{ fontSize: 20, fontWeight: 800, marginBottom: 16 }}>我的感知快恢 Agent 清单</div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
          <div style={{ flex: 1, maxWidth: 420 }}>
            <TextInput value={search} onChange={setSearch} icon="search" placeholder="搜索 Agent 名称、系统范围…" style={{ height: 40, borderRadius: 10, fontSize: 13.5 }} />
          </div>
          <div style={{ position: "relative" }}>
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value as Filter)}
              style={{ appearance: "none", WebkitAppearance: "none", height: 40, border: `1px solid ${color.borderInput}`, background: "#fff", borderRadius: 10, padding: "0 34px 0 14px", fontSize: 13.5, color: color.textStrong, cursor: "pointer", outline: "none" }}
            >
              <option value="all">全部状态</option>
              <option value="on">已启用</option>
              <option value="off">已停用</option>
            </select>
            <Icon name="chevron-down" size={15} color={color.textSubtle} style={{ position: "absolute", right: 12, top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }} />
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))", gap: 18 }}>
          {filtered.map((ag) => {
            const on = ag.status === "active";
            return (
              <div key={ag.instance_id} style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xxl, display: "flex", flexDirection: "column" }}>
                <div style={{ padding: "18px 18px 16px", display: "flex", flexDirection: "column", gap: 14 }}>
                  <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
                    <div style={{ width: 42, height: 42, borderRadius: 11, background: color.brandTintBg, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 42px" }}>
                      <Icon name="robot" size={22} color={color.brand} />
                    </div>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span style={{ fontSize: 15, fontWeight: 700, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{ag.name}</span>
                        {!on ? <Pill tone="warning">已停用</Pill> : null}
                      </div>
                      <div style={{ fontSize: 12.5, color: color.textMuted, marginTop: 3, lineHeight: 1.5 }}>{ag.desc ?? "自动接管告警，执行诊断与恢复"}</div>
                    </div>
                    <Toggle on={on} onChange={(v) => api.toggleInstance(ag.instance_id, v).then(onChanged).catch((e) => alert((e as Error).message))} />
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    <InfoRow icon="stack-2" label="系统范围" value={ag.workspace_label || ag.workspace_id} />
                    <InfoRow icon="cpu" label="模型提供商" value={ag.model ?? "千问 (平台提供)"} />
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "stretch", borderTop: `1px solid #f0f1f4` }}>
                  <CardAction icon="pencil" label="编辑" color={color.textNav} hoverBg="#f7f8fa" onClick={() => onOpen(ag.instance_id)} />
                  <div style={{ width: 1, background: "#f0f1f4" }} />
                  <CardAction icon="trash" label="删除" color={color.dangerText} hoverBg="#fdf3f3" onClick={() => setConfirmDel(ag)} />
                  <div style={{ width: 1, background: "#f0f1f4" }} />
                  <CardAction
                    icon={creatingRunId === ag.instance_id ? "loader-2" : "message"}
                    label={creatingRunId === ag.instance_id ? "创建中" : "对话 Agent"}
                    color={color.brand}
                    hoverBg="#f5f8ff"
                    bold
                    disabled={creatingRunId !== null}
                    onClick={async () => {
                      setCreatingRunId(ag.instance_id);
                      try {
                        const runId = await api.createRun(ag.instance_id);
                        setCreatingRunId(null);
                        nav(`/agent-teams/${ag.instance_id}/chat?run_id=${encodeURIComponent(runId)}`);
                      } catch (e) {
                        alert((e as Error).message);
                        setCreatingRunId(null);
                      }
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
        {filtered.length === 0 ? (
          <div style={{ textAlign: "center", color: color.textSubtle, fontSize: 13, padding: "40px 0" }}>没有匹配的 Agent</div>
        ) : null}
      </div>

      {/* 删除二次确认 */}
      {confirmDel ? (
        <div onClick={() => setConfirmDel(null)} style={{ position: "fixed", inset: 0, zIndex: 60, background: "rgba(20,24,31,.42)", display: "flex", alignItems: "center", justifyContent: "center", animation: "omFade .16s ease" }}>
          <div onClick={(e) => e.stopPropagation()} style={{ width: 420, background: "#fff", borderRadius: radius.modal, padding: "22px 22px 18px", animation: "omPop .2s ease" }}>
            <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8 }}>删除「{confirmDel.name}」？</div>
            <div style={{ fontSize: 12.5, color: color.textMuted, lineHeight: 1.6, marginBottom: 18 }}>软删除该 Agent 实例：历史 Run 与审计按 30 天保留策略清理；有任务运行中时不能删除。</div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <Button variant="secondary" onClick={() => setConfirmDel(null)}>取消</Button>
              <Button style={{ background: color.danger }} onClick={() => {
                api.deleteInstance(confirmDel.instance_id).then(() => { setConfirmDel(null); onChanged(); }).catch((e) => alert((e as Error).message));
              }}>确认删除</Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function InfoRow({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
      <Icon name={icon} size={15} color={color.textSubtle} />
      <span style={{ color: color.textSubtle, width: 68, flex: "0 0 68px" }}>{label}</span>
      <span style={{ color: color.textBody, fontWeight: 500 }}>{value}</span>
    </div>
  );
}

function CardAction({ icon, label, color: c, hoverBg, bold, disabled, onClick }: {
  icon: string; label: string; color: string; hoverBg: string; bold?: boolean; disabled?: boolean; onClick: () => void;
}) {
  return (
    <Interactive disabled={disabled} onClick={onClick}
      baseStyle={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 6, padding: "12px 0", cursor: disabled ? "wait" : "pointer", fontSize: 13, fontWeight: bold ? 700 : 600, color: c, opacity: disabled ? 0.72 : 1 }}
      hoverStyle={disabled ? {} : { background: hoverBg }}>
      <Icon name={icon} size={15} color={c} spin={icon === "loader-2"} />{label}
    </Interactive>
  );
}

/* ---------------- per-agent 插件配置（仅 Skill / MCP；模型与提示词在创建/编辑向导里管）---------------- */
function AgentDetail({ instance }: { instance: AgentInstance }) {
  const [tab, setTab] = useState<Tab>("skill");
  const instanceId = instance.instance_id;
  return (
    <>
      <div style={{ flex: "0 0 auto", background: "#fff", borderBottom: `1px solid ${color.border}`, padding: "0 24px", display: "flex", gap: 6 }}>
        {([["skill", "Skill 配置", "puzzle"], ["mcp", "MCP 配置", "plug"]] as const).map(([k, label, icon]) => {
          const active = tab === k;
          return (
            <div key={k} onClick={() => setTab(k)} style={{ display: "flex", alignItems: "center", gap: 6, padding: "13px 4px", margin: "0 8px", cursor: "pointer", fontSize: 13.5, fontWeight: active ? 700 : 500, color: active ? color.brand : color.textNav, borderBottom: `2px solid ${active ? color.brand : "transparent"}` }}>
              <Icon name={icon} size={16} />{label}
            </div>
          );
        })}
      </div>
      <PluginPane key={tab} kind={tab} instanceId={instanceId} />
    </>
  );
}

/** Skill 配置 / MCP 配置（原型 PluginsPage 布局）：工具栏（搜索+添加）+ 左树（系统自带/用户自定义，
 * 带已绑定标记与自定义项删除）+ 右详情（版本/来源/状态/说明 + 绑定/解绑当前 Agent）。 */
function PluginPane({ kind, instanceId }: { kind: "skill" | "mcp"; instanceId: string }) {
  const isSkill = kind === "skill";
  const [search, setSearch] = useState("");
  const [bound, setBound] = useState<AssetRow[]>([]);
  const [lib, setLib] = useState<AssetRow[]>([]);
  const [versions, setVersions] = useState<ConfigVersionRow[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dialog, setDialog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");  // 页内成功横幅（上传/注册成功提示，几秒后自动消失）
  const [groupOpen, setGroupOpen] = useState<{ sys: boolean; mine: boolean }>({ sys: true, mine: true });

  const reload = () => {
    api.getBoundSkills(instanceId).then((rows) => setBound(rows.filter((r) => r.kind === kind)));
    (isSkill ? api.getSkillLibrary() : api.getMcpLibrary()).then((rows) => {
      setLib(rows);
      setSelectedId((cur) => cur && rows.some((r) => r.id === cur) ? cur : rows[0]?.id ?? null);
    });
    api.getConfigVersions(instanceId).then(setVersions);
  };
  useEffect(reload, [instanceId, kind]);

  const run = (p: Promise<unknown>) => {
    setBusy(true); setMsg("");  // 新动作先清旧成功提示
    p.then(reload).catch((e) => alert((e as Error).message)).finally(() => setBusy(false));
  };

  const boundByAsset = new Map(bound.map((b) => [b.assetId, b]));
  const q = search.trim().toLowerCase();
  const filtered = lib.filter((r) => !q || r.name.toLowerCase().includes(q));
  const mine = filtered.filter((r) => r.meta.includes("我的"));
  const sys = filtered.filter((r) => !r.meta.includes("我的"));
  const selected = lib.find((r) => r.id === selectedId) ?? null;
  const selBinding = selected ? boundByAsset.get(selected.id) : undefined;
  const noun = isSkill ? "Skill" : "MCP";

  const treeRow = (r: AssetRow) => {
    const on = selectedId === r.id;
    const isSystem = r.sourceType ? r.sourceType === "platform" : !r.meta.includes("我的");
    // skill：运行时无条件纳入可执行集 → 恒「已装配」；mcp：看是否绑定
    const attached = isSkill || boundByAsset.has(r.id);
    const deletable = !isSystem;
    return (
      <Interactive key={r.id} onClick={() => setSelectedId(r.id)}
        baseStyle={{ display: "flex", alignItems: "center", gap: 7, padding: "7px 10px", margin: "1px 0", borderRadius: radius.md, cursor: "pointer", background: on ? color.brandTintBg : "transparent" }}
        hoverStyle={on ? {} : { background: "#f5f6f8" }}>
        <Icon name={isSkill ? "file-code" : "plug"} size={14} color={on ? color.brand : color.textSubtle} />
        <span style={{ flex: 1, minWidth: 0, fontSize: 12.5, fontWeight: on ? 650 : 500, color: on ? color.brand : color.textBody, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
        <span style={{ fontSize: 11, color: color.textFaint, flex: "0 0 auto", fontVariantNumeric: "tabular-nums" }}>{r.version}</span>
        {attached ? <span title={isSkill ? "已自动装配到本 Agent" : "已绑定当前 Agent"} style={{ width: 7, height: 7, borderRadius: "50%", background: "#22a06b", flex: "0 0 auto" }} /> : null}
        {deletable ? (
          <Icon name="trash" size={13} color={color.textFaint} title="删除"
            onClick={() => { if (confirm(`删除「${r.name}」？`)) run(api.deleteAsset(kind, r.id)); }} />
        ) : null}
      </Interactive>
    );
  };

  const group = (key: "sys" | "mine", label: string, rows: AssetRow[]) => (
    <div>
      <div onClick={() => setGroupOpen((g) => ({ ...g, [key]: !g[key] }))}
        style={{ display: "flex", alignItems: "center", gap: 6, padding: "7px 6px", cursor: "pointer", fontSize: 12.5, fontWeight: 700, color: color.textStrong }}>
        <Icon name={groupOpen[key] ? "chevron-down" : "chevron-right"} size={14} color={color.textSubtle} />
        <Icon name="folder-open" size={14} color={color.brand} />{label}
        <span style={{ fontWeight: 500, color: color.textSubtle }}>（{rows.length}）</span>
      </div>
      {groupOpen[key] ? <div style={{ paddingLeft: 14 }}>{rows.map(treeRow)}
        {rows.length === 0 ? <div style={{ padding: "6px 10px", fontSize: 12, color: color.textFaint }}>无</div> : null}
      </div> : null}
    </div>
  );

  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", background: color.surfaceAlt }}>
      {/* 工具栏 */}
      <div style={{ flex: "0 0 auto", display: "flex", alignItems: "center", gap: 12, padding: "12px 24px", background: "#fff", borderBottom: `1px solid ${color.border}` }}>
        <div style={{ width: 300 }}><TextInput value={search} onChange={setSearch} placeholder={`按 ${noun} 名称搜索…`} icon="search" /></div>
        <div style={{ flex: 1 }} />
        <Button variant="secondary" icon="refresh" disabled={busy} onClick={() => run(api.reconcileAssets())}>同步资产</Button>
        <Button variant="secondary" icon={isSkill ? "upload" : "plug"} onClick={() => setDialog(true)}>{isSkill ? "上传 Skill" : "注册 HTTP MCP"}</Button>
      </div>
      {msg ? (
        <div style={{ flex: "0 0 auto", padding: "8px 24px", background: "#e8f7ef", color: color.goodText, fontSize: 12.5, fontWeight: 600, borderBottom: `1px solid ${color.border}` }}>{msg}</div>
      ) : null}

      {/* 左树 + 右详情 */}
      <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
        <div style={{ width: 250, flex: "0 0 250px", background: "#fff", borderRight: `1px solid ${color.border}`, overflowY: "auto", padding: "10px 8px" }}>
          {group("sys", "系统自带", sys)}
          {group("mine", "用户自定义", mine)}
        </div>
        <div style={{ flex: 1, minWidth: 0, overflowY: "auto", padding: "24px 30px 40px" }}>
          {selected ? (
            <div style={{ maxWidth: 720 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
                <h3 style={{ fontSize: 17, fontWeight: 700, margin: 0 }}>{selected.name}</h3>
                <Pill tone={isSkill ? "neutral" : "warning"}>{noun}</Pill>
                <Pill tone={selected.statusTone}>{selected.status}</Pill>
              </div>
              <div style={{ fontSize: 12, color: color.textSubtle, marginBottom: 18 }}>
                版本 {selected.version} · {selected.meta}
              </div>
              <SectionLabel>说明</SectionLabel>
              <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "13px 15px", fontSize: 12.5, color: color.textBody, lineHeight: 1.7, marginBottom: 18 }}>
                {isSkill
                  ? "在你的隔离沙箱容器内受控执行的技能包（对话里可用 / 名称直接触发）。管理员配置的系统技能与你上传的技能都会自动装配到本 Agent，无需手动绑定。"
                  : "经 Tool Gateway 受控调用的 HTTP MCP 服务（scope 校验 / 审批门 / 审计留痕）。其工具在运行时动态装配给 Agent。"}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                {isSkill ? (
                  // skill 自动装配（运行时无条件纳入），只读展示，无绑定/解绑动作
                  <Pill tone="good">已自动装配到本 Agent</Pill>
                ) : selBinding ? (
                  <>
                    <Pill tone="good">已绑定当前 Agent</Pill>
                    <Button variant="secondary" disabled={busy} onClick={() => run(api.unbindAsset(selBinding.id))}>解绑</Button>
                  </>
                ) : (
                  <Button disabled={busy} onClick={() => run(api.bindAsset(instanceId, selected))}>绑定到当前 Agent</Button>
                )}
              </div>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "60%", color: color.textFaint, gap: 10 }}>
              <Icon name={isSkill ? "file-code" : "plug"} size={34} color={color.textFaint} />
              <span style={{ fontSize: 13 }}>左侧选择一个 {noun} 查看详情</span>
            </div>
          )}

          <div style={{ maxWidth: 720, marginTop: 30 }}>
            <ConfigVersionsBlock versions={versions} />
          </div>
        </div>
      </div>

      {dialog ? (
        <AssetDialog
          kind={kind}
          busy={busy}
          onClose={() => setDialog(false)}
          onSubmit={(p) => {
            const done = p.kind === "skill"
              ? api.uploadSkill(p.file).then((r) => { setDialog(false); setMsg(`Skill 已上传：${r.skill_key}`); setTimeout(() => setMsg(""), 4000); })
              : api.registerMcp(p.name, p.endpoint).then(() => { setDialog(false); setMsg("HTTP MCP 已注册"); setTimeout(() => setMsg(""), 4000); });
            run(done);
          }}
        />
      ) : null}
    </div>
  );
}

/** 配置版本历史（Skill/MCP 两个 pane 底部共用）。 */
function ConfigVersionsBlock({ versions }: { versions: ConfigVersionRow[] }) {
  if (versions.length === 0) return null;
  return (
    <>
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
        绑定/解绑会生成新的 active 配置版本（历史版本不改写）。运行中的 Run 不重建会话，下一次工具边界按最新配置生效。
      </div>
    </>
  );
}

/** AssetDialog 提交载荷：skill = ZIP 包上传（29.3 §2.1）；mcp = HTTP MCP 注册。 */
type AssetSubmit =
  | { kind: "skill"; file: File }
  | { kind: "mcp"; name: string; endpoint: string };

const _MAX_SKILL_ZIP = 50 * 1024 * 1024;  // 29.3 §2.1：ZIP ≤ 50MB

/** 上传 Skill（真 ZIP 上传，29.3 §2.1）/ 注册 HTTP MCP 弹窗（30.5：V1 仅 HTTP MCP）。 */
function AssetDialog({ kind, busy, onClose, onSubmit }: {
  kind: "skill" | "mcp"; busy: boolean; onClose: () => void; onSubmit: (p: AssetSubmit) => void;
}) {
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("https://");
  const [file, setFile] = useState<File | null>(null);
  const [fileErr, setFileErr] = useState("");

  const pickFile = (f: File | null) => {
    setFileErr("");
    if (f && !/\.zip$/i.test(f.name)) { setFileErr("请选择 .zip 包"); setFile(null); return; }
    if (f && f.size > _MAX_SKILL_ZIP) { setFileErr("ZIP 超过 50MB 上限"); setFile(null); return; }
    setFile(f);
  };
  const ok = kind === "skill"
    ? Boolean(file)
    : name.trim().length > 0 && endpoint.trim().length > 8;
  const submit = () => {
    if (!ok || busy) return;
    if (kind === "skill" && file) {
      onSubmit({ kind: "skill", file });
    } else {
      onSubmit({ kind: "mcp", name: name.trim(), endpoint: endpoint.trim() });
    }
  };
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 60, background: "rgba(20,24,31,.42)", display: "flex", alignItems: "center", justifyContent: "center", animation: "omFade .16s ease" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 440, background: "#fff", borderRadius: radius.modal, padding: "22px 22px 18px", animation: "omPop .2s ease" }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 14 }}>{kind === "skill" ? "上传 Skill" : "注册 HTTP MCP"}</div>
        {kind === "skill" ? (
          <>
            <SectionLabel>Skill 包（.zip，含 SKILL.md）</SectionLabel>
            <label style={{ display: "flex", alignItems: "center", gap: 10, border: `1px dashed ${color.borderInput}`, borderRadius: radius.md, padding: "12px 14px", cursor: "pointer", background: color.neutralBg }}>
              <Icon name="upload" size={16} color={color.brand} />
              <span style={{ fontSize: 12.5, color: file ? color.textStrong : color.textSubtle, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {file ? `${file.name}（${(file.size / 1024).toFixed(0)} KB）` : "点击选择 ZIP 文件"}
              </span>
              <input type="file" accept=".zip,application/zip" style={{ display: "none" }}
                onChange={(e) => pickFile(e.target.files?.[0] ?? null)} />
            </label>
            {fileErr ? <div style={{ fontSize: 12, color: color.dangerText, marginTop: 6 }}>{fileErr}</div> : null}
          </>
        ) : (
          <>
            <SectionLabel>名称</SectionLabel>
            <TextInput value={name} onChange={setName} placeholder="例：CMDB 查询 MCP" />
            <div style={{ height: 12 }} />
            <SectionLabel>Endpoint（仅 HTTP）</SectionLabel>
            <TextInput value={endpoint} onChange={setEndpoint} placeholder="https://mcp.example.com/mcp" mono />
          </>
        )}
        <div style={{ marginTop: 12, fontSize: 11.5, color: color.textSubtle, lineHeight: 1.6 }}>
          {kind === "skill" ? "ZIP 内须含 SKILL.md（其 name 字段即 skill_id）；上传经 SkillHub 校验并入库，绑定到 main 后在沙箱执行（B8）。" : "用户 MCP 不透传 Cookie、不注入平台 header，范围自担（28.2）。"}
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
          <Button variant="secondary" onClick={onClose} disabled={busy}>取消</Button>
          <Button icon={busy ? "loader-2" : undefined} disabled={!ok || busy} onClick={submit}>{busy ? "上传中…" : (kind === "skill" ? "上传" : "注册")}</Button>
        </div>
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10, color: color.textStrong }}>{children}</div>;
}
