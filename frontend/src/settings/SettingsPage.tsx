import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { color, radius } from "../theme/tokens";
import { Icon, Interactive, Pill, Button, TextInput, Toggle } from "../ui";
import { useApp, useSyncCurrentAgent } from "../lib/appState";
import { api } from "../lib/api";
import type { AgentInstance, AssetRow, ConfigVersionRow, ModelOption } from "../lib/api/types";
import { AddCustomModelDialog } from "./AddCustomModelDialog";

type Tab = "skill" | "mcp" | "model" | "prompt";
type Filter = "all" | "on" | "off";

/** 实例配置（isSettings·新原型）：对话页点「设置」**直达当前 Agent 的配置视图**（Skill/MCP/模型/提示词），
 * 返回箭头才回「我的全部 Agent」清单管理页（新建/启停/删除入口在那）。 */
export function SettingsPage() {
  const nav = useNavigate();
  const { instanceId } = useParams();
  useSyncCurrentAgent(instanceId);  // 直链/新建实例进入：全局列表缺它则重拉
  const { agents, refresh } = useApp();
  // 初始=路由实例：设置入口带着当前 Agent 进来，直达其配置（实测诉求：不要先看所有 Agent 卡片）
  const [detailId, setDetailId] = useState<string | null>(instanceId ?? null);
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
        <span style={{ fontSize: 12, color: color.textSubtle }}>{detail ? `${detail.template} · ${detail.workspace_label}` : "管理你的全部 Agent"}</span>
        <div style={{ flex: 1 }} />
        {!detail ? <Button icon="plus" onClick={() => nav("/init")}>新建 Agent</Button> : null}
      </header>

      {detail ? <AgentDetail instance={detail} /> : <AgentListPage agents={agents} onOpen={setDetailId} onChanged={refresh} />}
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

/* ---------------- per-agent 配置（运行服务视角：Skill / MCP / 模型 / 提示词）---------------- */
function AgentDetail({ instance }: { instance: AgentInstance }) {
  const [tab, setTab] = useState<Tab>("skill");
  const instanceId = instance.instance_id;
  return (
    <>
      <div style={{ flex: "0 0 auto", background: "#fff", borderBottom: `1px solid ${color.border}`, padding: "0 24px", display: "flex", gap: 6 }}>
        {([["skill", "Skill 配置", "puzzle"], ["mcp", "MCP 配置", "plug"], ["model", "模型配置", "cpu"], ["prompt", "角色提示词", "message-2"]] as const).map(([k, label, icon]) => {
          const active = tab === k;
          return (
            <div key={k} onClick={() => setTab(k)} style={{ display: "flex", alignItems: "center", gap: 6, padding: "13px 4px", margin: "0 8px", cursor: "pointer", fontSize: 13.5, fontWeight: active ? 700 : 500, color: active ? color.brand : color.textNav, borderBottom: `2px solid ${active ? color.brand : "transparent"}` }}>
              <Icon name={icon} size={16} />{label}
            </div>
          );
        })}
      </div>
      {tab === "skill" || tab === "mcp" ? <PluginPane key={tab} kind={tab} instanceId={instanceId} />
        : tab === "prompt" ? <PromptTab instanceId={instanceId} /> : <ModelTab instance={instance} />}
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
    setBusy(true);
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
    const isBound = boundByAsset.has(r.id);
    const deletable = r.meta.includes("我的");
    return (
      <Interactive key={r.id} onClick={() => setSelectedId(r.id)}
        baseStyle={{ display: "flex", alignItems: "center", gap: 7, padding: "7px 10px", margin: "1px 0", borderRadius: radius.md, cursor: "pointer", background: on ? color.brandTintBg : "transparent" }}
        hoverStyle={on ? {} : { background: "#f5f6f8" }}>
        <Icon name={isSkill ? "file-code" : "plug"} size={14} color={on ? color.brand : color.textSubtle} />
        <span style={{ flex: 1, minWidth: 0, fontSize: 12.5, fontWeight: on ? 650 : 500, color: on ? color.brand : color.textBody, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
        {isBound ? <span title="已绑定当前 Agent" style={{ width: 7, height: 7, borderRadius: "50%", background: "#22a06b", flex: "0 0 auto" }} /> : null}
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
                  ? "在你的隔离沙箱容器内受控执行的技能包（对话里可用 / 名称直接触发）。绑定到当前 Agent 后进入其装配集。"
                  : "经 Tool Gateway 受控调用的 HTTP MCP 服务（scope 校验 / 审批门 / 审计留痕）。其工具在运行时动态装配给 Agent。"}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                {selBinding ? (
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
          onSubmit={(name, endpoint) => {
            run((isSkill ? api.uploadSkill(name) : api.registerMcp(name, endpoint)).then(() => setDialog(false)));
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

/** 上传 Skill / 注册 HTTP MCP 弹窗（30.5：V1 仅 HTTP MCP；Skill 包上传后台校验 checksum）。 */
function AssetDialog({ kind, busy, onClose, onSubmit }: {
  kind: "skill" | "mcp"; busy: boolean; onClose: () => void; onSubmit: (name: string, endpoint: string) => void;
}) {
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("https://");
  const ok = name.trim().length > 0 && (kind === "skill" || endpoint.trim().length > 8);
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 60, background: "rgba(20,24,31,.42)", display: "flex", alignItems: "center", justifyContent: "center", animation: "omFade .16s ease" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 440, background: "#fff", borderRadius: radius.modal, padding: "22px 22px 18px", animation: "omPop .2s ease" }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 14 }}>{kind === "skill" ? "上传 Skill" : "注册 HTTP MCP"}</div>
        <SectionLabel>名称</SectionLabel>
        <TextInput value={name} onChange={setName} placeholder={kind === "skill" ? "例：日志聚类分析" : "例：CMDB 查询 MCP"} />
        {kind === "mcp" ? (
          <>
            <div style={{ height: 12 }} />
            <SectionLabel>Endpoint（仅 HTTP）</SectionLabel>
            <TextInput value={endpoint} onChange={setEndpoint} placeholder="https://mcp.example.com/mcp" mono />
          </>
        ) : null}
        <div style={{ marginTop: 12, fontSize: 11.5, color: color.textSubtle, lineHeight: 1.6 }}>
          {kind === "skill" ? "V1 演示以名称建包（checksum 由后台生成）；绑定到 main 后在沙箱执行（B8）。" : "用户 MCP 不透传 Cookie、不注入平台 header，范围自担（28.2）。"}
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
          <Button variant="secondary" onClick={onClose}>取消</Button>
          <Button disabled={!ok || busy} onClick={() => ok && onSubmit(name.trim(), endpoint.trim())}>{kind === "skill" ? "上传" : "注册"}</Button>
        </div>
      </div>
    </div>
  );
}

/** 角色提示词（30.5 CFG-002：只写 main overlay 的 append；平台默认只读）。 */
function PromptTab({ instanceId }: { instanceId: string }) {
  const [text, setText] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.getMainAppend(instanceId).then((t) => { setText(t); setSaved(t); }); }, [instanceId]);
  const dirty = saved !== null && text !== saved;
  return (
    <div style={{ flex: 1, minHeight: 0, overflowY: "auto", background: color.surfaceAlt, padding: "26px 30px 40px" }}>
      <div style={{ maxWidth: 720 }}>
        <h2 style={{ fontSize: 19, fontWeight: 700, margin: "0 0 6px" }}>main Agent 角色追加</h2>
        <div style={{ fontSize: 12.5, color: color.textSubtle, marginBottom: 18 }}>
          平台模板的默认角色只读；此处内容以「追加」方式作用于 main Agent（不影响 sub Agent）。保存生成新配置版本。
        </div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="例：优先关注支付链路核心接口的 P99 与错误率；恢复动作前先给出影响面评估。"
          style={{ width: "100%", minHeight: 180, border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "12px 14px", fontSize: 13, lineHeight: 1.7, fontFamily: "inherit", background: "#fff", outline: "none", boxSizing: "border-box" }}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 12 }}>
          <Button
            disabled={!dirty || busy}
            onClick={() => {
              setBusy(true);
              api.saveMainAppend(instanceId, text).then(() => setSaved(text)).catch((e) => alert((e as Error).message)).finally(() => setBusy(false));
            }}
          >保存（生成新版本）</Button>
          {dirty ? <span style={{ fontSize: 12, color: color.textSubtle }}>有未保存的修改</span> : saved !== null ? <span style={{ fontSize: 12, color: color.textSubtle }}>已保存</span> : null}
        </div>
      </div>
    </div>
  );
}

function ModelTab({ instance }: { instance: AgentInstance }) {
  const [models, setModels] = useState<ModelOption[]>([]);
  const [picked, setPicked] = useState<string>("");
  const [dialog, setDialog] = useState(false);
  const load = (selectId?: string) =>
    api.getModelConfigs().then((m) => {
      setModels(m);
      setPicked(selectId ?? m.find((x) => x.current)?.llm_config_id ?? m[0]?.llm_config_id ?? "");
    });
  useEffect(() => { load(); }, []);
  return (
    <div style={{ flex: 1, minHeight: 0, overflowY: "auto", background: color.surfaceAlt, padding: "26px 30px 40px" }}>
      <div style={{ maxWidth: 720 }}>
        <h2 style={{ fontSize: 19, fontWeight: 700, margin: "0 0 6px" }}>模型配置</h2>
        <div style={{ fontSize: 12.5, color: color.textSubtle, marginBottom: 16 }}>当前 Agent 的模型来源与可用模型清单（查看为主）。</div>
        {/* 当前 Agent 默认模型（创建向导设定；查看为主，不做实例级修改持久化） */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "14px 16px", marginBottom: 20 }}>
          <div style={{ width: 38, height: 38, borderRadius: 10, background: color.brandTintBg, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 38px" }}>
            <Icon name="cpu" size={20} color={color.brand} />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 12, color: color.textSubtle }}>当前 Agent 默认模型</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: color.textStrong, marginTop: 2 }}>{instance.model ?? "平台默认模型"}</div>
          </div>
          <span style={{ fontSize: 11.5, color: color.textSubtle }}>创建向导设定 · 对话中可临时切换（会话级）</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {models.map((m) => {
            const on = picked === m.llm_config_id;
            return (
              <Interactive key={m.llm_config_id} onClick={() => m.available && setPicked(m.llm_config_id)}
                baseStyle={{ display: "flex", alignItems: "center", gap: 12, border: `1px solid ${on ? color.brand : color.border}`, background: on ? color.brandTintBg : "#fff", borderRadius: radius.xl, padding: "13px 15px", cursor: m.available ? "pointer" : "not-allowed", opacity: m.available ? 1 : 0.6 }}
                hoverStyle={on || !m.available ? {} : { borderColor: color.brandTintBorder }}>
                <Icon name="cpu" size={18} color={on ? color.brand : color.textSubtle} />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13.5, fontWeight: 600, color: color.textStrong }}>{m.label}</div>
                  <div style={{ fontSize: 11.5, color: color.textSubtle, marginTop: 2 }}>{m.reason ?? m.note}</div>
                </div>
                <div style={{ width: 18, height: 18, borderRadius: "50%", border: `2px solid ${on ? color.brand : "#cfd3da"}`, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  {on ? <div style={{ width: 8, height: 8, borderRadius: "50%", background: color.brand }} /> : null}
                </div>
              </Interactive>
            );
          })}
        </div>
        <button onClick={() => setDialog(true)} style={{ marginTop: 12, border: `1px dashed #c9cdd6`, background: "#fff", cursor: "pointer", color: color.brand, fontSize: 12.5, fontWeight: 600, padding: "9px 15px", borderRadius: radius.lg, display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Icon name="plus" size={15} color={color.brand} />添加自定义模型（OpenAI 兼容）
        </button>
        <div style={{ marginTop: 16, padding: "11px 13px", borderRadius: radius.lg, background: color.brandTintBg, border: `1px solid rgba(22,131,255,.18)`, fontSize: 12, color: color.brandStrong, lineHeight: 1.6 }}>
          Secret 明文仅在创建时提交，保存后只显示脱敏指纹；探测失败的模型不能设为默认。对话输入框里的模型切换是会话级临时选择，不会修改这里的默认配置。
        </div>
      </div>
      <AddCustomModelDialog open={dialog} onClose={() => setDialog(false)} onCreated={(id) => load(id)} />
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10, color: color.textStrong }}>{children}</div>;
}
