import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { color, font, radius } from "../theme/tokens";
import type { Tone } from "../theme/tokens";
import { Button, Icon, Interactive, Pagination, Pill, TextInput } from "../ui";
import { useApp } from "../lib/appState";
import { isAbortError } from "../lib/api/singleFlight";
import { alertsApi, subscribeAlerts } from "./api";
import { FALLBACK_CATEGORIES, SEVERITY_LABEL, SEVERITY_TONE, STATE_REASON_TEXT } from "./constants";
import type {
  AlertEventRow,
  AlertEventsPage,
  AlertEventStatus,
  AlertSeverity,
  AlertTakeoverStatus,
} from "./types";

const PAGE_SIZE = 20;

const fmtTime = (v: string | null): string => {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(d);
};

/** 告警平台侧状态三态 → 徽标。 */
const EVENT_STATUS_META: Record<AlertEventStatus, { label: string; tone: Tone }> = {
  unassigned: { label: "未分派", tone: "neutral" },
  assigned: { label: "已分派", tone: "warning" },
  closed: { label: "已关闭", tone: "good" },
};

/** 等级四档 → 徽标（提示为 subtle 淡显；文案/色调取切片共享常量）。 */
const SeverityPill = ({ severity }: { severity: AlertSeverity }) => (
  <Pill tone={SEVERITY_TONE[severity]} style={severity === "info" ? { opacity: 0.6 } : undefined}>{SEVERITY_LABEL[severity]}</Pill>
);

/** Agent 接管状态：shield（蓝）= 已完成/处理中；shield-off（灰）= 未接管。
 * 未接管细分（2026-08-15 陈旧留痕拍板）：stale_consumer_lag → 「延迟放弃」——
 * 命中了规则但消费延迟超阈值未自动处理，告知用户可自行诊断（不静默丢）。
 * 处理中细分（2026-08-19）：incident_state=queued → 「排队中」——还没绑 run，
 * 「查看处理会话」灰色属正常等待，别当成异常。 */
const TakeoverCell = ({ takeover, stateReason, incidentState }: {
  takeover: AlertTakeoverStatus; stateReason?: string | null; incidentState?: string | null;
}) => {
  const stale = takeover === "none" && stateReason === "stale_consumer_lag";
  const queued = takeover === "processing" && incidentState === "queued";
  const meta = {
    done: { icon: "shield", c: color.brand, label: "已完成" },
    processing: { icon: "shield", c: color.brand, label: queued ? "排队中" : "处理中" },
    none: { icon: "shield-off", c: color.textFaint, label: stale ? "延迟放弃" : "未接管" },
  }[takeover];
  return (
    <span title={stale ? "消费延迟超阈值，未自动处理——可到对话界面自行诊断，或联系管理员重试"
                 : queued ? "排队等待接管：并发名额满时按优先级依次出队，轮到后「查看处理会话」即可点开" : undefined}
          style={{ display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap", color: takeover === "none" ? color.textSubtle : color.textBody, fontWeight: 600 }}>
      <Icon name={meta.icon} size={15} color={meta.c} />
      {meta.label}
    </span>
  );
};

/** 接管结果：已恢复/失败/已升级；未出结果显「—」。
 * 失败附原因短文案（state_reason → 用户话术，未知码显原始 code）——此前原因只能查库/查日志。 */
const ResultCell = ({ it }: { it: AlertEventRow }) => {
  if (it.agent_result === "recovered") return <Pill tone="good" icon="check">已恢复</Pill>;
  if (it.agent_result === "failed") {
    const reason = it.state_reason ? (STATE_REASON_TEXT[it.state_reason] ?? it.state_reason) : "";
    return (
      <span title={reason || undefined} style={{ display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0, maxWidth: "100%" }}>
        <Pill tone="danger">失败</Pill>
        {reason ? (
          <span style={{ fontSize: 12, color: color.textSubtle, maxWidth: 150, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {reason}
          </span>
        ) : null}
      </span>
    );
  }
  if (it.agent_result === "escalated") return <Pill tone="warning" icon="arrow-up-right">已升级</Pill>;
  return (
    <span title="Agent 未给出恢复/升级判定，可查看处理会话了解详情" style={{ color: color.textFaint }}>—</span>
  );
};

/** 姊妹单状态短标（接管策略列多策略行的后缀）。 */
const TAKEOVER_STATE_LABEL: Record<string, string> = {
  queued: "排队中", diagnosing: "诊断中", completed: "已完成",
  failed: "失败", skipped: "已跳过", ignored: "已忽略",
};
const TAKEOVER_RESULT_LABEL: Record<string, string> = {
  recovered: "已恢复", escalated: "已升级", failed: "失败", processing: "处理中",
};

/** 接管策略：按提示词分单后一条告警可被多条策略各接管一次（2026-08-20 测试反馈：
 *  此前只显投影单的一条策略，第二次诊断在页面上无入口，被当成「只跑了一次」）。
 *  takeovers 逐条展示：策略名（可点 → 该策略自己的处理会话）+ 状态短标（多条时）；
 *  历史预览行/存量后端无 takeovers → 回落旧的单条投影口径。未接管显「—」。 */
const RuleCell = ({ it, onOpenRun }: { it: AlertEventRow; onOpenRun: (runId: string) => void }) => {
  const line: React.CSSProperties = { display: "block", maxWidth: 150, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" };
  const list = it.takeovers?.filter((t) => t.matched_rule) ?? [];
  if (!list.length) {
    if (!it.matched_rule) {
      return <span title="未命中接管规则，或存量数据无规则快照" style={{ color: color.textFaint }}>—</span>;
    }
    const total = it.matched_rule_total ?? 1;
    const text = `${it.matched_rule.name}${total > 1 ? ` 等${total}条` : ""}`;
    return <span style={{ ...line, color: color.textMuted }} title={text}>{text}</span>;
  }
  const multi = list.length > 1;
  return (
    <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {list.map((tk) => {
        const name = `${tk.matched_rule!.name}${tk.matched_rule_total > 1 ? ` 等${tk.matched_rule_total}条` : ""}`;
        const state = TAKEOVER_STATE_LABEL[tk.incident_state] ?? tk.incident_state;
        // 结果后缀只在已完结单上拼（processing 态的 result 与状态短标同义，拼上是噪音）
        const result = tk.takeover_status === "done" && tk.agent_result
          ? TAKEOVER_RESULT_LABEL[tk.agent_result] ?? tk.agent_result : "";
        const clickable = tk.run_clickable && !!tk.run_id;
        const title = `策略「${tk.matched_rule!.name}」·${state}${result && result !== state ? `·${result}` : ""}${clickable ? "，点击查看该策略的处理会话" : ""}`;
        return (
          <span key={tk.incident_id} style={line} title={title}>
            <span
              onClick={clickable ? () => onOpenRun(tk.run_id!) : undefined}
              style={clickable ? { color: color.brand, fontWeight: 600, cursor: "pointer" } : { color: color.textMuted }}
            >
              {name}
            </span>
            {multi ? <span style={{ color: color.textFaint, fontSize: 11 }}>·{state}</span> : null}
          </span>
        );
      })}
    </span>
  );
};

/** 用户评价（占位功能）：整列置灰由 td 样式统一处理，这里只管内容。 */
const FeedbackCell = ({ it }: { it: AlertEventRow }) => {
  if (!it.user_feedback) return <span>-</span>;
  const face = it.user_feedback === "positive" ? "👍" : it.user_feedback === "negative" ? "👎" : "中评";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, minWidth: 0, maxWidth: "100%" }}>
      <span>{face}</span>
      {it.feedback_note ? (
        <span style={{ fontSize: 12, maxWidth: 110, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={it.feedback_note}>
          {it.feedback_note}
        </span>
      ) : null}
    </span>
  );
};

/** 原生 select 下拉（筛选栏统一样式）。 */
const FilterSelect = ({ value, onChange, options, testid, minWidth = 118 }: {
  value: string;
  onChange: (v: string) => void;
  options: { label: string; value: string }[];
  testid: string;
  minWidth?: number;
}) => (
  <div style={{ position: "relative" }}>
    <select
      data-testid={testid}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{ appearance: "none", WebkitAppearance: "none", height: 34, minWidth, border: `1px solid ${value ? color.brand : color.borderInput}`, background: value ? color.brandTintBg : "#fff", borderRadius: radius.md, padding: "0 28px 0 12px", fontSize: 12.5, color: value ? color.brand : color.textNav, fontWeight: value ? 600 : 400, cursor: "pointer", outline: "none" }}
    >
      {options.map((o) => (
        <option key={o.value || "__all"} value={o.value}>{o.label}</option>
      ))}
    </select>
    <Icon name="chevron-down" size={14} color={color.textSubtle} style={{ position: "absolute", right: 9, top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }} />
  </div>
);

/** 带清除按钮的搜索框（切片内自用，core TextInput 无 clear 能力）。 */
const SearchBox = ({ value, onChange, placeholder, width = 260, clearTestid }: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  width?: number;
  clearTestid: string;
}) => (
  <div style={{ position: "relative", width }}>
    <TextInput value={value} onChange={onChange} icon="search" placeholder={placeholder} style={{ paddingRight: 30, height: 34, fontSize: 12.5 }} />
    {value ? (
      <span
        data-testid={clearTestid}
        onClick={() => onChange("")}
        style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", cursor: "pointer", display: "inline-flex" }}
      >
        <Icon name="x" size={14} color={color.textSubtle} />
      </span>
    ) : null}
  </div>
);

const th: React.CSSProperties = {
  textAlign: "left", fontSize: 11.5, fontWeight: 700, color: color.textSubtle,
  padding: "10px 12px", background: color.surfaceAlt, borderBottom: `1px solid ${color.border}`, whiteSpace: "nowrap",
};
const td: React.CSSProperties = { padding: "10px 12px", borderTop: `1px solid ${color.borderFaint}`, verticalAlign: "middle" };
/** 用户评价整列置灰（占位功能未开放）：td 内容 + 列头一起淡。 */
const feedbackDim: React.CSSProperties = { opacity: 0.4, filter: "grayscale(1)" };

/** 告警清单（侧栏一级导航）：事件视角全宽表格页 = 56px header + 四下拉筛选条 + 十六列表格 + 分页。
 *  v2 起不再有行内详情抽屉/忽略/重试——事件详情归告警平台外链，处理过程看诊断会话。 */
export function AlertsPage() {
  const nav = useNavigate();
  // alertGranted 直接用 appState 启动时探询的结果（2026-08-20：原本页挂载再拉一次
  // /alerts/access，纯重复请求）；默认 true 不闪锁、探询失败不锁，口径与原本页实现一致。
  const { agents, currentAgentId, alertGranted } = useApp();

  // 清单跟随侧栏「选择 Agent」（不设本页的 Agent 筛选下拉）：每个 Agent 只看自己的
  // 接管清单（自己接管的 + 尚未被接管的）。切 Agent 由侧栏统一完成，本页自动重拉。
  const instanceId = currentAgentId;
  const [statusF, setStatusF] = useState<"" | AlertEventStatus>("");
  const [sevF, setSevF] = useState<"" | AlertSeverity>("");
  const [takeF, setTakeF] = useState<"" | AlertTakeoverStatus>("");
  const [catF, setCatF] = useState<string>("");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");            // 防抖后的搜索词（real 档下沉服务端，mock 档本地过滤）
  const [page, setPage] = useState(1);
  const [data, setData] = useState<AlertEventsPage | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    const t = setTimeout(() => { setQ(search.trim()); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [search]);

  // 挂载即拉 + 15s 轮询 + 写操作失效广播重拉；筛选/翻页切换用 AbortController latest-wins
  useEffect(() => {
    let alive = true;
    let controller: AbortController | null = null;
    const load = () => {
      controller?.abort();
      controller = new AbortController();
      const signal = controller.signal;
      const params = {
        instanceId: instanceId || undefined,
        alertStatus: statusF || undefined,
        severity: sevF || undefined,
        takeover: takeF || undefined,
        category: catF || undefined,
        search: q || undefined,
        page,
        pageSize: PAGE_SIZE,
        signal,
      };
      alertsApi.listEvents(params).then((next) => {
        if (alive && !signal.aborted) { setData(next); setErr(""); }
      }).catch((error) => {
        if (alive && !isAbortError(error)) setErr((error as Error).message || "加载失败");
      });
    };
    load();
    const timer = setInterval(load, 15_000);
    const unsubscribe = subscribeAlerts(load);
    return () => {
      alive = false;
      controller?.abort();
      clearInterval(timer);
      unsubscribe();
    };
  }, [instanceId, statusF, sevF, takeF, catF, q, page]);

  const activeFilters = [statusF, sevF, takeF, catF].filter(Boolean).length;
  const clearFilters = () => { setStatusF(""); setSevF(""); setTakeF(""); setCatF(""); setPage(1); };

  // 「接管规则」/空态跳转的目标实例 = 当前 Agent（清单与侧栏同源，无本页切换）
  const settingsTarget = instanceId;
  const items = data?.items ?? [];
  const agentName = agents.find((a) => a.instance_id === currentAgentId)?.name;

  if (alertGranted === false) {
    return (
      <div style={{ flex: 1, minHeight: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
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
    <>
      <header style={{ flex: "0 0 auto", height: 56, borderBottom: `1px solid ${color.border}`, background: "#fff", display: "flex", alignItems: "center", padding: "0 24px", gap: 12 }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>告警接管清单</div>
        {/* 只读徽标（样式对齐 Workbench「当前使用」）：清单归属当前 Agent，切换在侧栏做 */}
        {agentName ? (
          <span title="清单跟随左侧「选择 Agent」；在侧栏切换 Agent 即切换清单" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color: color.textNav, background: color.neutralBg, border: `1px solid ${color.border}`, padding: "4px 10px", borderRadius: radius.pill, whiteSpace: "nowrap", flex: "0 0 auto" }}>
            <Icon name="robot" size={14} color={color.brand} />{agentName}
          </span>
        ) : null}
        <span style={{ fontSize: 11, color: color.textSubtle }}>命中本 Agent 订阅规则的告警自动排队诊断</span>
        <div style={{ flex: 1 }} />
        <Button
          variant="secondary"
          icon="shield-bolt"
          disabled={!instanceId}
          title="配置本 Agent 的告警接管规则（设置 → 告警接管配置）"
          onClick={() => nav("/settings/alerts")}
        >
          接管规则
        </Button>
      </header>

      {/* 筛选条：四个单值下拉 + 计数徽标 + 清除 + 搜索 */}
      <div style={{ flex: "0 0 auto", display: "flex", alignItems: "center", gap: 10, padding: "12px 24px", background: "#fff", borderBottom: `1px solid ${color.border}`, flexWrap: "wrap" }}>
        <FilterSelect
          testid="alerts-filter-status"
          value={statusF}
          onChange={(v) => { setStatusF(v as "" | AlertEventStatus); setPage(1); }}
          options={[
            { label: "全部状态", value: "" },
            { label: "未分派", value: "unassigned" },
            { label: "已分派", value: "assigned" },
            { label: "已关闭", value: "closed" },
          ]}
        />
        <FilterSelect
          testid="alerts-filter-severity"
          value={sevF}
          onChange={(v) => { setSevF(v as "" | AlertSeverity); setPage(1); }}
          options={[
            { label: "全部等级", value: "" },
            { label: "致命", value: "fatal" },
            { label: "严重", value: "critical" },
            { label: "普通", value: "warning" },
          ]}
        />
        <FilterSelect
          testid="alerts-filter-takeover"
          value={takeF}
          onChange={(v) => { setTakeF(v as "" | AlertTakeoverStatus); setPage(1); }}
          minWidth={132}
          options={[
            { label: "全部接管状态", value: "" },
            { label: "已完成", value: "done" },
            { label: "处理中", value: "processing" },
            { label: "未接管", value: "none" },
          ]}
        />
        <FilterSelect
          testid="alerts-filter-category"
          value={catF}
          onChange={(v) => { setCatF(v); setPage(1); }}
          options={[
            { label: "全部类型", value: "" },
            ...FALLBACK_CATEGORIES.map((c) => ({ label: c, value: c })),
          ]}
        />
        {activeFilters > 0 ? (
          <>
            <span style={{ display: "inline-flex", alignItems: "center", fontSize: 11.5, fontWeight: 700, color: color.brand, background: color.brandTintBg, border: `1px solid ${color.brandTintBorder}`, borderRadius: radius.pill, padding: "3px 10px", whiteSpace: "nowrap" }}>
              筛选 {activeFilters}
            </span>
            <span onClick={clearFilters} style={{ fontSize: 12, fontWeight: 600, color: color.textNav, cursor: "pointer", whiteSpace: "nowrap" }}>
              清除筛选
            </span>
          </>
        ) : null}
        <div style={{ flex: 1 }} />
        <SearchBox value={search} onChange={setSearch} placeholder="搜索告警编号、类型或对象" clearTestid="alerts-search-clear" />
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "18px 24px 28px" }}>
        {err ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8, background: "#fdecec", border: "1px solid #f5c2c0", borderRadius: radius.lg, padding: "9px 13px", fontSize: 12, color: color.dangerText, marginBottom: 12 }}>
            <Icon name="alert-triangle" size={14} color={color.dangerText} />{err}
          </div>
        ) : null}

        {!data && !err ? (
          /* 首次加载态（2026-08-20）：原先渲染"有表头没有行"的空表格，等待期观感差 */
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8, padding: "72px 0", color: color.textSubtle, fontSize: 13 }} data-testid="alerts-loading">
            <Icon name="loader-2" size={18} color={color.brand} spin />加载告警清单…
          </div>
        ) : data && items.length === 0 ? (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12, padding: "72px 0", color: color.textSubtle }}>
            <Icon name="bell-bolt" size={36} color={color.textFaint} />
            <div style={{ fontSize: 13 }}>暂无告警记录</div>
            {settingsTarget ? (
              <Button variant="secondary" icon="settings" onClick={() => nav("/settings/alerts")}>去配置接管规则</Button>
            ) : null}
          </div>
        ) : (
          <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, overflowX: "auto" }}>
            {/* overflowX auto（宿主 div）：十六列在窄视口整体横向滚动，操作列不被裁掉 */}
            <table style={{ width: "100%", minWidth: 1700, borderCollapse: "collapse", fontSize: 12.5 }}>
              <thead>
                <tr>
                  <th style={th}>操作</th>{/* 2026-08-15 挪首列（用户反馈：操作是高频入口） */}
                  <th style={th}>告警编号</th>
                  <th style={th}>告警类型</th>
                  <th style={{ ...th, minWidth: 150 }}>告警名称</th>
                  <th style={th}>告警对象</th>
                  <th style={th}>APPID</th>
                  <th style={{ ...th, minWidth: 150 }}>告警描述</th>
                  <th style={th}>告警状态</th>
                  <th style={th}>告警等级</th>
                  <th style={th}>Agent 接管状态</th>
                  <th style={th}>接管策略</th>
                  <th style={th}>接管结果</th>
                  {/* 用户评价整列占位置灰：列头一起浅灰 */}
                  <th style={{ ...th, color: color.textFaint, ...feedbackDim }}>用户评价</th>
                  <th style={th}>开始时间</th>
                  <th style={th}>结束时间</th>
                  <th style={th}>时长</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => {
                  // 主链接回退首个可点姊妹会话（审查实证：A 完结+B 排队时行级投影单是 B、
                  // run_id=null，旧逻辑灰显还宣称「无处理会话」——A 的会话明明存在）
                  const sessionRuns = it.takeovers?.filter((t) => t.run_clickable && t.run_id) ?? [];
                  const primaryRun = (it.run_clickable && it.run_id) ? it.run_id : sessionRuns[0]?.run_id ?? null;
                  const clickable = !!primaryRun;
                  const sessions = sessionRuns.length;
                  return (
                    <Interactive
                      key={it.alert_no}
                      as="tr"
                      data-testid="alerts-incident-row"
                      baseStyle={{ background: "transparent" }}
                      hoverStyle={{ background: color.surfaceTint }}
                    >
                      <td style={td}>
                        {clickable ? (
                          <span
                            onClick={() => nav(`/agent-runs/${primaryRun}`)}
                            title={sessions > 1 ? `本告警被 ${sessions} 条策略各接管一次；此处打开最新会话，其余会话点「接管策略」列的策略名` : undefined}
                            style={{ fontSize: 12, fontWeight: 600, color: color.brand, cursor: "pointer", whiteSpace: "nowrap" }}
                          >
                            查看处理会话{sessions > 1 ? `（${sessions}）` : ""}
                          </span>
                        ) : (
                          <span
                            title={it.run_id ? "处理会话已过期清理"
                              : it.takeover_status === "processing" ? "排队等待接管，轮到后此处即可点开处理会话"
                              : "该告警未被 Agent 接管，无处理会话"}
                            style={{ fontSize: 12, fontWeight: 600, color: color.textFaint, cursor: "not-allowed", whiteSpace: "nowrap" }}
                          >
                            查看处理会话
                          </span>
                        )}
                      </td>
                      <td style={{ ...td, whiteSpace: "nowrap" }}>
                        {it.detail_url ? (
                          <a
                            href={it.detail_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{ color: color.brand, fontWeight: 600, textDecoration: "none" }}
                            title="在告警平台打开详情"
                          >
                            {it.alert_no}
                          </a>
                        ) : (
                          <span style={{ color: color.textBody }}>{it.alert_no}</span>
                        )}
                      </td>
                      <td style={{ ...td, fontWeight: 700, whiteSpace: "nowrap", color: color.textStrong }}>{it.category}</td>
                      <td style={{ ...td, maxWidth: 220 }}>
                        <span style={{ display: "block", color: color.textStrong, fontWeight: 700, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={it.title}>
                          {it.title}
                        </span>
                      </td>
                      <td style={{ ...td, whiteSpace: "nowrap", color: color.textBody }}>{it.alert_object}</td>
                      <td style={{ ...td, maxWidth: 110 }}>
                        <span style={{ display: "block", fontFamily: font.mono, fontSize: 11, color: color.textSubtle, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={it.appid}>
                          {it.appid}
                        </span>
                      </td>
                      <td style={{ ...td, maxWidth: 200 }}>
                        <span style={{ display: "block", color: color.textMuted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={it.description}>
                          {it.description}
                        </span>
                      </td>
                      <td style={td}><Pill tone={EVENT_STATUS_META[it.alert_status].tone}>{EVENT_STATUS_META[it.alert_status].label}</Pill></td>
                      <td style={td}><SeverityPill severity={it.severity} /></td>
                      <td style={td}><TakeoverCell takeover={it.takeover_status} stateReason={it.state_reason} incidentState={it.incident_state} /></td>
                      <td style={{ ...td, maxWidth: 150 }}><RuleCell it={it} onOpenRun={(runId) => nav(`/agent-runs/${runId}`)} /></td>
                      <td style={td}><ResultCell it={it} /></td>
                      <td style={{ ...td, ...feedbackDim }}><FeedbackCell it={it} /></td>
                      <td style={{ ...td, color: color.textNav, whiteSpace: "nowrap" }}>{fmtTime(it.started_at)}</td>
                      <td style={{ ...td, fontFamily: font.mono, fontSize: 11.5, color: color.textNav, whiteSpace: "nowrap" }}>{fmtTime(it.ended_at)}</td>
                      <td style={{ ...td, fontFamily: font.mono, color: color.textNav, whiteSpace: "nowrap" }}>{it.duration ?? "—"}</td>
                    </Interactive>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <Pagination page={page} pageSize={PAGE_SIZE} total={data?.total ?? 0} onPage={setPage} />
      </div>
    </>
  );
}

export default AlertsPage;
