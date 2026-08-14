import { useEffect, useMemo, useRef, useState } from "react";
import { color, font, radius, shadow } from "../theme/tokens";
import type { Tone } from "../theme/tokens";
import { Button, Dot, Icon, Interactive, Modal, OverlayHeader, Pill, TextInput, Toggle } from "../ui";
import { api } from "../lib/api";
import { isAbortError } from "../lib/api/singleFlight";
import type { Skill } from "../lib/api/types";
import { alertsApi, subscribeAlerts } from "./api";
import type {
  AlertEventRow,
  AlertEventStatus,
  AlertRule,
  AlertRuleBatchAction,
  AlertRulesConfig,
  AlertRuleTemplatesPayload,
  AlertSeverity,
} from "./types";

const SEVERITY_LABEL: Record<AlertSeverity, string> = { fatal: "致命", critical: "严重", warning: "普通", info: "提示" };
const SEVERITY_TONE: Record<AlertSeverity, Tone> = { fatal: "danger", critical: "warning", warning: "neutral", info: "neutral" };
const SEVERITY_ORDER: AlertSeverity[] = ["fatal", "critical", "warning", "info"];
/** 第二步预览的告警状态徽标（AlertsPage 有同款映射但未导出，就地三行不跨文件掏）。 */
const EVENT_STATUS_META: Record<AlertEventStatus, { label: string; tone: Tone }> = {
  unassigned: { label: "未分派", tone: "neutral" },
  assigned: { label: "已分派", tone: "warning" },
  closed: { label: "已关闭", tone: "good" },
};
/** payload 未到位时类型下拉的兜底档（与模板三类一致）。 */
const FALLBACK_CATEGORIES = ["MySQL", "PostgreSQL", "Docker"];

/** 表格网格列：☑ | 规则名称 | 策略类型 | 告警级别 | 提示词 | 操作 | 启用 */
const GRID_COLS = "30px minmax(150px, 1.1fr) 104px 158px minmax(170px, 1.5fr) 96px 56px";

/** 多选/单选共用的 chip（编辑器的 类型/级别 两组）。 */
const PickChip = ({ label, active, onToggle }: { label: string; active: boolean; onToggle: () => void }) => (
  <span
    onClick={onToggle}
    style={{
      display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600,
      padding: "5px 12px", borderRadius: radius.pill, cursor: "pointer",
      border: `1px solid ${active ? color.brand : color.border}`,
      background: active ? color.brandTintBg : "#fff",
      color: active ? color.brand : color.textNav,
    }}
  >
    {active ? <Icon name="check" size={12} color={color.brand} /> : null}
    {label}
  </span>
);

/** Agent 设置第三 tab「告警接管」：总开关 + 策略表（搜索/类型筛选/批量操作）+ 新建/编辑弹窗。
 *  对齐 PluginPane 的交互律：动作即时生效 + 重拉，无「保存」按钮。 */
export function AlertRulesPane({ instanceId }: { instanceId: string }) {
  const [granted, setGranted] = useState<boolean | null>(null);  // null=探询中（2026-08-09 算力白名单）
  const [cfg, setCfg] = useState<AlertRulesConfig | null>(null);
  const [payload, setPayload] = useState<AlertRuleTemplatesPayload | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState<{ open: boolean; rule: AlertRule | null }>({ open: false, rule: null });
  const [confirmDelId, setConfirmDelId] = useState<string | null>(null); // 删除二次确认：首点变红「确认删除」
  const [search, setSearch] = useState("");
  const [catFilter, setCatFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchOpen, setBatchOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    alertsApi.getAccess().then((a) => { if (alive) setGranted(a.granted); })
      .catch(() => { if (alive) setGranted(true); });  // 探询失败不拦（写路径后端仍有闸）
    const load = () => {
      alertsApi.getRules(instanceId).then((next) => {
        if (!alive) return;
        setCfg(next);
        setErr("");
        // 规则重拉后修剪选中集：被删的 id 不残留（批量动作本身也会显式清空）
        setSelected((prev) => new Set([...prev].filter((id) => next.rules.some((r) => r.rule_id === id))));
      }).catch((e) => {
        if (alive && !isAbortError(e)) setErr((e as Error).message || "加载失败");
      });
    };
    load();
    alertsApi.getRuleTemplates().then((p) => { if (alive) setPayload(p); }).catch(() => { /* 模板取失败只影响弹窗 */ });
    const unsubscribe = subscribeAlerts(load);
    return () => {
      alive = false;
      unsubscribe();
    };
  }, [instanceId]);

  const run = (p: Promise<void>) => {
    setBusy(true);
    p.catch((e) => alert((e as Error).message)).finally(() => setBusy(false));
  };

  const rules = cfg?.rules ?? [];
  const enabledCount = rules.filter((r) => r.enabled).length;

  const categories = useMemo(() => {
    if (!payload) return FALLBACK_CATEGORIES;
    const seen: string[] = [];
    for (const t of payload.templates) if (!seen.includes(t.category)) seen.push(t.category);
    return seen;
  }, [payload]);

  // 本地模糊搜索：名称 / 类型 / 级别（中文标签也可搜）
  const q = search.trim().toLowerCase();
  const visible = rules.filter((r) => {
    if (catFilter && !r.categories.includes(catFilter)) return false;
    if (!q) return true;
    const hay = `${r.name} ${r.categories.join(" ")} ${r.severities.map((s) => SEVERITY_LABEL[s]).join(" ")}`.toLowerCase();
    return hay.includes(q);
  });

  const allVisibleSelected = visible.length > 0 && visible.every((r) => selected.has(r.rule_id));
  const toggleSelect = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const toggleSelectAll = () =>
    setSelected(allVisibleSelected ? new Set() : new Set(visible.map((r) => r.rule_id)));

  const doBatch = (action: AlertRuleBatchAction) => {
    setBatchOpen(false);
    const ids = [...selected];
    if (!ids.length) return;
    // 完成后清空选中；invalidateAlerts 广播触发重拉
    run(alertsApi.batchRules(ids, action).then(() => setSelected(new Set())));
  };

  const closeEditor = () => setEditor({ open: false, rule: null });

  // 未开通：整页替换成引导空态（算力资源有限按需开放；后端写路径 403 同文案兜底）
  if (granted === false) {
    return (
      <div style={{ flex: 1, minHeight: 0, display: "flex", alignItems: "center", justifyContent: "center", background: color.surfaceAlt }}>
        <div style={{ textAlign: "center", maxWidth: 440, padding: 24 }} data-testid="alerts-not-granted">
          <div style={{ width: 56, height: 56, borderRadius: 16, background: "#fff", border: `1px solid ${color.border}`, display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 14 }}>
            <Icon name="lock" size={26} color={color.textFaint} />
          </div>
          <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6 }}>告警接管功能未开通</div>
          <div style={{ fontSize: 12.5, color: color.textMuted, lineHeight: 1.7 }}>
            算力资源有限，该功能按管理员白名单开放。<br />如需使用，请联系平台管理员为你开通。
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={{ flex: 1, minHeight: 0, overflowY: "auto", background: color.surfaceAlt, padding: "22px 30px 40px" }}>
      <div style={{ maxWidth: 980, margin: "0 auto", display: "flex", flexDirection: "column", gap: 14 }}>
        {err ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8, background: "#fdecec", border: "1px solid #f5c2c0", borderRadius: radius.lg, padding: "9px 13px", fontSize: 12, color: color.dangerText }}>
            <Icon name="alert-triangle" size={14} color={color.dangerText} />{err}
          </div>
        ) : null}

        {/* 策略区（实例总开关随订阅下线 2026-08-15：规则启停/批量即整体暂停） */}
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {/* 工具栏一行：搜索 + 类型 + 统计 + 批量 + 添加 */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <div style={{ position: "relative", width: 216 }}>
              <TextInput value={search} onChange={setSearch} icon="search" placeholder="搜索策略名称、类型或级别" style={{ paddingRight: 30, height: 34, fontSize: 12.5 }} />
              {search ? (
                <span
                  data-testid="alerts-rules-search-clear"
                  onClick={() => setSearch("")}
                  style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", cursor: "pointer", display: "inline-flex" }}
                >
                  <Icon name="x" size={14} color={color.textSubtle} />
                </span>
              ) : null}
            </div>
            <div style={{ position: "relative" }}>
              <select
                value={catFilter}
                onChange={(e) => setCatFilter(e.target.value)}
                style={{ appearance: "none", WebkitAppearance: "none", height: 34, minWidth: 112, border: `1px solid ${catFilter ? color.brand : color.borderInput}`, background: catFilter ? color.brandTintBg : "#fff", borderRadius: radius.md, padding: "0 28px 0 12px", fontSize: 12.5, color: catFilter ? color.brand : color.textNav, fontWeight: catFilter ? 600 : 400, cursor: "pointer", outline: "none" }}
              >
                <option value="">全部类型</option>
                {categories.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
              <Icon name="chevron-down" size={14} color={color.textSubtle} style={{ position: "absolute", right: 9, top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }} />
            </div>
            <span style={{ fontSize: 12.5, color: color.textSubtle, whiteSpace: "nowrap" }}>共 {rules.length} 条策略 · 已启用 {enabledCount} 条</span>
            <div style={{ flex: 1 }} />
            {selected.size > 0 ? (
              <div style={{ position: "relative" }}>
                <Button variant="secondary" icon="stack-2" onClick={() => setBatchOpen((o) => !o)}>
                  批量操作（{selected.size}）
                </Button>
                {batchOpen ? (
                  <>
                    <div onClick={() => setBatchOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 55 }} />
                    <div data-testid="alerts-batch-menu" style={{ position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 56, background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.lg, boxShadow: shadow.menu, minWidth: 148, padding: 4 }}>
                      <Interactive
                        as="div"
                        onClick={() => doBatch("enable")}
                        baseStyle={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", fontSize: 12.5, fontWeight: 600, cursor: "pointer", borderRadius: radius.md, color: color.textNav }}
                        hoverStyle={{ background: color.pageBg }}
                      >
                        <Icon name="circle-check" size={14} color={color.textSubtle} />批量启用
                      </Interactive>
                      <Interactive
                        as="div"
                        onClick={() => doBatch("disable")}
                        baseStyle={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", fontSize: 12.5, fontWeight: 600, cursor: "pointer", borderRadius: radius.md, color: color.textNav }}
                        hoverStyle={{ background: color.pageBg }}
                      >
                        <Icon name="circle-off" size={14} color={color.textSubtle} />批量禁用
                      </Interactive>
                      {/* 删除项红色警示样式 */}
                      <Interactive
                        as="div"
                        onClick={() => doBatch("delete")}
                        baseStyle={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", fontSize: 12.5, fontWeight: 700, cursor: "pointer", borderRadius: radius.md, color: color.dangerText }}
                        hoverStyle={{ background: "#fdf2f1" }}
                      >
                        <Icon name="trash" size={14} color={color.danger} />批量删除
                      </Interactive>
                    </div>
                  </>
                ) : null}
              </div>
            ) : null}
            <Button icon="plus" disabled={!payload} onClick={() => setEditor({ open: true, rule: null })}>添加告警策略</Button>
          </div>

          {rules.length === 0 ? (
            <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, display: "flex", flexDirection: "column", alignItems: "center", gap: 10, padding: "56px 0", color: color.textSubtle }}>
              <Icon name="shield-exclamation" size={34} color={color.textFaint} />
              <div style={{ fontSize: 13.5, fontWeight: 700, color: color.textStrong }}>暂无告警策略</div>
              <div style={{ fontSize: 12 }}>添加策略后，命中的告警会自动排队并创建诊断会话</div>
              <Button icon="plus" disabled={!payload} onClick={() => setEditor({ open: true, rule: null })}>添加告警策略</Button>
            </div>
          ) : (
            <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, overflow: "hidden" }}>
              <div style={{ display: "grid", gridTemplateColumns: GRID_COLS, gap: 10, padding: "9px 16px", fontSize: 11.5, fontWeight: 700, color: color.textSubtle, background: color.surfaceAlt, borderBottom: `1px solid ${color.border}`, alignItems: "center" }}>
                <input
                  type="checkbox"
                  data-testid="alerts-rule-select-all"
                  title="全选当前列表"
                  checked={allVisibleSelected}
                  onChange={toggleSelectAll}
                  style={{ width: 15, height: 15, accentColor: color.brand, cursor: "pointer", margin: 0 }}
                />
                <span>规则名称</span><span>策略类型</span><span>告警级别</span><span>提示词</span><span>操作</span><span>启用</span>
              </div>
              {visible.length === 0 ? (
                <div style={{ padding: "34px 0", textAlign: "center", fontSize: 12.5, color: color.textSubtle }}>未找到匹配的策略</div>
              ) : (
                visible.map((r, i) => (
                  <Interactive
                    key={r.rule_id}
                    as="div"
                    data-testid="alerts-rule-row"
                    data-enabled={r.enabled ? "true" : "false"}
                    baseStyle={{ display: "grid", gridTemplateColumns: GRID_COLS, gap: 10, padding: "11px 16px", alignItems: "center", fontSize: 12.5, borderTop: i ? `1px solid ${color.borderFaint}` : "none", background: "transparent" }}
                    hoverStyle={{ background: color.surfaceTint }}
                  >
                    <input
                      type="checkbox"
                      data-testid="alerts-rule-checkbox"
                      checked={selected.has(r.rule_id)}
                      onChange={() => toggleSelect(r.rule_id)}
                      style={{ width: 15, height: 15, accentColor: color.brand, cursor: "pointer", margin: 0 }}
                    />
                    <span style={{ fontWeight: 600, color: color.textStrong, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={r.name}>
                      {r.name}
                    </span>
                    <span style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                      {r.categories.map((c) => <Pill key={c} tone="neutral">{c}</Pill>)}
                    </span>
                    <span style={{ display: "inline-flex", gap: 4, flexWrap: "wrap" }}>
                      {SEVERITY_ORDER.filter((s) => r.severities.includes(s)).map((s) => (
                        <Pill key={s} tone={SEVERITY_TONE[s]}>{SEVERITY_LABEL[s]}</Pill>
                      ))}
                    </span>
                    {/* 提示词单行省略，悬浮见全文 */}
                    <span style={{ color: color.textMuted, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={r.prompt}>
                      {r.prompt}
                    </span>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 10, whiteSpace: "nowrap" }}>
                      <span
                        onClick={() => { if (payload) setEditor({ open: true, rule: r }); }}
                        style={{ fontSize: 12, fontWeight: 600, color: color.brand, cursor: "pointer" }}
                      >
                        编辑
                      </span>
                      {confirmDelId === r.rule_id ? (
                        <span
                          onClick={() => { if (!busy) { setConfirmDelId(null); run(alertsApi.deleteRule(r.rule_id)); } }}
                          style={{ fontSize: 12, fontWeight: 700, color: "#fff", background: color.danger, borderRadius: radius.md, padding: "4px 8px", textAlign: "center", cursor: "pointer", whiteSpace: "nowrap" }}
                        >
                          确认删除
                        </span>
                      ) : (
                        <span
                          onClick={() => setConfirmDelId(r.rule_id)}
                          style={{ fontSize: 12, fontWeight: 600, color: color.textNav, cursor: "pointer" }}
                        >
                          删除
                        </span>
                      )}
                    </span>
                    <Toggle on={r.enabled} onChange={(v) => run(alertsApi.toggleRule(r.rule_id, v))} />
                  </Interactive>
                ))
              )}
            </div>
          )}
        </div>
      </div>

      {editor.open && payload ? (
        <RuleEditor
          instanceId={instanceId}
          payload={payload}
          rule={editor.rule}
          busy={busy}
          onClose={closeEditor}
          onSubmit={(draft) => {
            const p = editor.rule
              ? alertsApi.updateRule(editor.rule.rule_id, draft)
              : alertsApi.createRule(instanceId, { ...draft, enabled: true });
            run(p.then(closeEditor));
          }}
        />
      ) : null}
    </div>
  );
}

/* ---------------------------- 新建/编辑策略弹窗（两步向导） ---------------------------- */

/** 编辑器出参：不含 strategies——两步向导起不再勾选监控策略，新建落空数组=该类型
 *  全部策略（matcher 空数组短路）；编辑 patch 不带该字段=后端不改，存量勾选子集保留。
 *  categories 多选（2026-08-07 拍板）：任一类型命中即匹配。 */
interface RuleDraft {
  name: string;
  categories: string[];
  severities: AlertSeverity[];
  prompt: string;
}

function RuleEditor({ instanceId, payload, rule, busy, onClose, onSubmit }: {
  instanceId: string;
  payload: AlertRuleTemplatesPayload;
  rule: AlertRule | null; // null = 新建
  busy: boolean;
  onClose: () => void;
  onSubmit: (draft: RuleDraft) => void;
}) {
  const categories = useMemo(() => {
    const seen: string[] = [];
    for (const t of payload.templates) if (!seen.includes(t.category)) seen.push(t.category);
    return seen;
  }, [payload.templates]);

  const [name, setName] = useState(rule?.name ?? "");
  // 类型多选：编辑回填规则已存清单；新建默认选第一类（与旧单选的初始态一致）
  const [catSel, setCatSel] = useState<string[]>(() =>
    rule ? [...rule.categories] : categories[0] ? [categories[0]] : []);
  const [sevSel, setSevSel] = useState<AlertSeverity[]>(rule ? [...rule.severities] : ["fatal", "critical"]);
  // 新建预填默认提示词；编辑预填 rule.prompt（都可改）
  const [prompt, setPrompt] = useState(rule ? rule.prompt : payload.default_prompt);
  // 两步向导：1=基本信息（名称/类型/级别/提示词），2=命中告警预览（确认才保存）
  const [step, setStep] = useState<1 | 2>(1);
  const [windowDays, setWindowDays] = useState<3 | 7>(7);  // 原型默认「近7天告警」

  // 第二步预览：主路径=平台历史接口（2026-08-09 切内网 alarm_list，真全量历史——
  // 未命中规则/未接管的告警也可见），平台不可用时后端自动降级本地落库（source 区分，
  // 前端提示数据来源）。多类别 CSV 一次下传（不再按类别并发合并）。
  // 只在第二步拉取（第一步零请求），窗口/条件变化 latest-wins 防连点竞态。
  const [preview, setPreview] =
    useState<{ rows: AlertEventRow[]; total: number; source: "platform" | "local_fallback" } | null>(null);
  useEffect(() => {
    if (step !== 2) return;
    const ctl = new AbortController();
    setPreview(null);
    alertsApi.historyPreview({ instanceId, categories: catSel, severity: sevSel,
                               sinceDays: windowDays, pageSize: 20, signal: ctl.signal })
      .then((page) => setPreview({ rows: page.items, total: page.total, source: page.source }))
      .catch((err) => { if (!isAbortError(err)) setPreview({ rows: [], total: 0, source: "platform" }); });
    return () => ctl.abort();
  }, [step, windowDays, instanceId, catSel, sevSel]);

  const toggleSev = (s: AlertSeverity) =>
    setSevSel((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));
  const toggleCat = (c: string) =>
    setCatSel((cur) => (cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]));

  // ---- 提示词 "/" 选 Skill：光标处呼出该用户可用 skill，选中插入 "/name "。 ----
  // 数据源与聊天输入框同一接口（getAvailableSkills，带 KeyedSingleFlightCache）；
  // 后端派发侧从规则提示词提取首个 /token 作 skill_hint（dispatcher._skill_hint_from_prompt）。
  const [skills, setSkills] = useState<Skill[]>([]);
  const [slash, setSlash] = useState<{ start: number; query: string } | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    let alive = true;
    api.getAvailableSkills(instanceId).then((s) => { if (alive) setSkills(s); })
      .catch((e) => { if (!isAbortError(e)) console.warn("[OpenOps][alerts] Skill 菜单读取失败", e); });
    return () => { alive = false; };
  }, [instanceId]);

  // 光标前最近 token 检测：行首或空白后的 /xxx（「、」= 中文 IME 下的 / 键，沿 CopilotSkillSlash 先例）
  const detectSlash = () => {
    const ta = taRef.current;
    if (!ta) { setSlash(null); return; }
    const caret = ta.selectionStart ?? 0;
    const m = /(?:^|[\s　])([/、][^\s　]*)$/.exec(ta.value.slice(0, caret));
    setSlash(m ? { start: caret - m[1].length, query: m[1].slice(1).toLowerCase() } : null);
  };
  const pickSkill = (s: Skill) => {
    const ta = taRef.current;
    if (!ta || !slash) return;
    const caret = ta.selectionStart ?? 0;
    const next = prompt.slice(0, slash.start) + s.name + " " + prompt.slice(caret);
    setPrompt(next);
    setSlash(null);
    const pos = slash.start + s.name.length + 1;
    requestAnimationFrame(() => { ta.focus(); ta.setSelectionRange(pos, pos); });  // 受控更新后回置光标
  };
  const slashFiltered = slash
    ? skills.filter((s) => !slash.query || s.name.slice(1).toLowerCase().includes(slash.query))
    : [];

  const canNext = name.trim().length > 0 && catSel.length > 0 && sevSel.length > 0;  // 命中 0 条也允许建规（规则先于告警存在是合法场景）

  const submit = () => {
    if (busy) return;
    onSubmit({
      name: name.trim(),
      categories: categories.filter((c) => catSel.includes(c)),  // 按模板顺序回写，顺序稳定可比对
      severities: SEVERITY_ORDER.filter((s) => sevSel.includes(s)),
      prompt: prompt.trim(),
    });
  };

  const fieldTitle: React.CSSProperties = { fontSize: 12.5, fontWeight: 700, marginBottom: 8 };
  const th: React.CSSProperties = { fontSize: 11.5, fontWeight: 700, color: color.textSubtle, padding: "8px 10px", background: color.surfaceAlt, whiteSpace: "nowrap" };
  const td: React.CSSProperties = { fontSize: 12, padding: "8px 10px", borderTop: `1px solid ${color.borderFaint}`, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
  const previewGrid = "150px 84px minmax(84px, 120px) minmax(84px, 130px) minmax(90px, 130px) 56px 64px minmax(130px, 1fr)";

  return (
    <Modal open onClose={onClose} maxWidth={step === 1 ? 640 : 860}>
      <div data-testid="alerts-rule-editor" style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
        <OverlayHeader
          title={rule ? "编辑告警策略" : "添加告警策略"}
          sub={step === 1 ? "第 1 步 / 共 2 步 · 基本信息" : "第 2 步 / 共 2 步 · 命中告警预览，确认后保存"}
          onClose={onClose}
        />
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
          {step === 1 ? (
            <>
              <div>
                <div style={fieldTitle}>规则名称 <span style={{ color: color.danger }}>*</span></div>
                <TextInput value={name} onChange={setName} placeholder="规则名称（必填）" />
              </div>

              <div>
                <div style={fieldTitle}>策略类型 <span style={{ fontWeight: 400, fontSize: 11.5, color: color.textSubtle }}>可多选，任一类型命中即接管</span></div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {categories.map((c) => (
                    <PickChip key={c} label={c} active={catSel.includes(c)} onToggle={() => toggleCat(c)} />
                  ))}
                </div>
              </div>

              <div>
                <div style={fieldTitle}>告警级别</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {payload.severities.map((s) => (
                    <PickChip key={s} label={SEVERITY_LABEL[s]} active={sevSel.includes(s)} onToggle={() => toggleSev(s)} />
                  ))}
                </div>
              </div>

              <div>
                <div style={fieldTitle}>提示词 <span style={{ color: color.danger }}>*</span> <span style={{ fontWeight: 400, fontSize: 11.5, color: color.textSubtle }}>输入 “/” 可选择你的 Skill</span></div>
                <div style={{ position: "relative" }}>
                  <textarea
                    ref={taRef}
                    value={prompt}
                    onChange={(e) => { setPrompt(e.target.value); detectSlash(); }}
                    onKeyUp={detectSlash}
                    onClick={detectSlash}
                    onBlur={() => setSlash(null)}
                    onKeyDown={(e) => {
                      // 菜单开着时 Esc 只关菜单，不透传（防一键连关弹窗）
                      if (e.key === "Escape" && slash) { e.stopPropagation(); setSlash(null); }
                    }}
                    rows={5}
                    placeholder="诊断提示词（预填默认五步法口径，可按场景调整；输入 / 选择自定义 Skill）"
                    style={{ width: "100%", boxSizing: "border-box", border: `1px solid ${color.borderInput}`, borderRadius: radius.md, padding: "9px 11px", fontSize: 12.5, lineHeight: 1.7, fontFamily: font.sans, color: color.ink, background: "#fff", resize: "vertical", outline: "none" }}
                  />
                  {slash ? (
                    <div data-testid="alerts-skill-menu" style={{ position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0, zIndex: 2, background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.lg, boxShadow: shadow.menu, overflow: "hidden" }}>
                      {skills.length === 0 ? (
                        <div style={{ padding: "10px 12px", fontSize: 12, color: color.textSubtle }}>
                          暂无可用 Skill——可能未同步/未装配，或你不是该 Agent 的属主
                        </div>
                      ) : slashFiltered.length === 0 ? null : (
                        slashFiltered.slice(0, 8).map((s) => (
                          <div
                            key={s.skill_id}
                            onMouseDown={(e) => { e.preventDefault(); pickSkill(s); }}
                            style={{ display: "flex", alignItems: "baseline", gap: 10, padding: "8px 12px", fontSize: 12.5, cursor: "pointer" }}
                            onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = color.surfaceTint; }}
                            onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = "#fff"; }}
                          >
                            <span style={{ fontFamily: "ui-monospace, monospace", fontWeight: 600, color: color.brandStrong, whiteSpace: "nowrap" }}>{s.name}</span>
                            <span style={{ flex: 1, minWidth: 0, fontSize: 11.5, color: color.textSubtle, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{s.desc}</span>
                          </div>
                        ))
                      )}
                    </div>
                  ) : null}
                </div>
              </div>
            </>
          ) : (
            <div data-testid="alerts-rule-preview">
              {/* 工具行：时间窗下拉 + 计数（对齐原型「近7天告警 · 共 N 条告警」） */}
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
                <span style={{ fontSize: 12.5, color: color.textNav }}>这些告警符合您的筛选条件，如果缺少某些告警，请返回上一步并修改筛选条件。</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
                <select
                  data-testid="alerts-rule-window"
                  value={windowDays}
                  onChange={(e) => setWindowDays(Number(e.target.value) as 3 | 7)}
                  style={{ height: 32, border: `1px solid ${color.borderInput}`, background: "#fff", borderRadius: radius.md, padding: "0 10px", fontSize: 12.5, color: color.textStrong, cursor: "pointer", outline: "none" }}
                >
                  <option value={7}>近7天告警</option>
                  <option value={3}>近3天告警</option>
                </select>
                {preview ? (
                  <span style={{ fontSize: 12.5, color: color.textNav }}>共 <b>{preview.total}</b> 条告警</span>
                ) : null}
                {preview?.source === "local_fallback" ? (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, color: color.textSubtle }}>
                    <Icon name="cloud-off" size={13} color={color.textFaint} />
                    平台历史接口暂不可用，以下为本地缓存告警
                  </span>
                ) : null}
              </div>
              <div style={{ border: `1px solid ${color.border}`, borderRadius: radius.lg, overflow: "hidden" }}>
                <div style={{ display: "grid", gridTemplateColumns: previewGrid, gap: 8, padding: "0 2px" }}>
                  <span style={th}>告警编号</span><span style={th}>告警类型</span><span style={th}>告警对象</span>
                  <span style={th}>APPID</span><span style={th}>企业ID</span><span style={th}>告警等级</span><span style={th}>告警状态</span><span style={th}>告警描述</span>
                </div>
                {preview === null ? (
                  <div style={{ padding: "12px", fontSize: 12, color: color.textSubtle }}>加载中…</div>
                ) : preview.rows.length === 0 ? (
                  <div style={{ padding: "12px", fontSize: 12, color: color.textSubtle }}>近 {windowDays} 天无匹配告警——不影响保存，规则生效后命中的新告警会自动接管。</div>
                ) : (
                  preview.rows.map((ev) => (
                    <div key={ev.alert_no} data-testid="alerts-rule-preview-row" style={{ display: "grid", gridTemplateColumns: previewGrid, gap: 8, alignItems: "center", padding: "0 2px" }}>
                      {ev.detail_url ? (
                        <a href={ev.detail_url} target="_blank" rel="noopener noreferrer" style={{ ...td, color: color.brand, fontWeight: 600, textDecoration: "none" }} title={ev.alert_no}>{ev.alert_no}</a>
                      ) : (
                        <span style={{ ...td, fontWeight: 600 }} title={ev.alert_no}>{ev.alert_no}</span>
                      )}
                      <span style={td}>{ev.category}</span>
                      <span style={td} title={ev.alert_object}>{ev.alert_object}</span>
                      <span style={{ ...td, fontFamily: "ui-monospace, monospace", fontSize: 11, color: color.textSubtle }} title={ev.appid}>{ev.appid}</span>
                      <span style={{ ...td, fontFamily: "ui-monospace, monospace", fontSize: 11, color: color.textSubtle }} title={ev.enterprise_id || ""}>{ev.enterprise_id || "—"}</span>
                      <span style={{ ...td, overflow: "visible" }}>
                        <Pill tone={SEVERITY_TONE[ev.severity] ?? "neutral"}>{SEVERITY_LABEL[ev.severity] ?? ev.severity}</Pill>
                      </span>
                      <span style={{ ...td, overflow: "visible" }}>
                        <Pill tone={EVENT_STATUS_META[ev.alert_status]?.tone ?? "neutral"}>{EVENT_STATUS_META[ev.alert_status]?.label ?? ev.alert_status}</Pill>
                      </span>
                      <span style={{ ...td, color: color.textSubtle }} title={ev.description || ev.title}>{ev.description || ev.title}</span>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </div>
        <div style={{ flex: "0 0 auto", borderTop: `1px solid ${color.border}`, padding: "12px 20px", display: "flex", alignItems: "center", gap: 10 }}>
          {step === 1 ? (
            <>
              {catSel.length === 0 || sevSel.length === 0 ? (
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: color.warningText }}>
                  <Dot tone="warning" />{catSel.length === 0 ? "至少选择一个策略类型" : "至少选择一个告警级别"}
                </span>
              ) : null}
              <div style={{ flex: 1 }} />
              <Button variant="secondary" onClick={onClose} disabled={busy}>取消</Button>
              <Button disabled={!canNext || busy} onClick={() => setStep(2)}>下一步</Button>
            </>
          ) : (
            <>
              <Button variant="secondary" icon="arrow-left" onClick={() => setStep(1)} disabled={busy}>上一步</Button>
              <div style={{ flex: 1 }} />
              <Button variant="secondary" onClick={onClose} disabled={busy}>取消</Button>
              <Button icon={busy ? "loader-2" : rule ? "device-floppy" : "check"} disabled={busy} onClick={submit}>确认</Button>
            </>
          )}
        </div>
      </div>
    </Modal>
  );
}

export default AlertRulesPane;
