import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { color, font, radius } from "../theme/tokens";
import type { Tone } from "../theme/tokens";
import { Button, Icon, Interactive, Pagination, Pill, TextInput } from "../ui";
import { useApp } from "../lib/appState";
import { isAbortError } from "../lib/api/singleFlight";
import { alertsApi, subscribeAlerts } from "./api";
import type {
  AlertEventRow,
  AlertEventsPage,
  AlertEventStatus,
  AlertSeverity,
  AlertTakeoverStatus,
} from "./types";

const PAGE_SIZE = 20;
/** 告警类型筛选的固定档（与模板三类一致；开放枚举的其余类型走「全部」）。 */
const CATEGORY_OPTIONS = ["MySQL", "PostgreSQL", "Docker"];

const fmtTime = (v: string | null): string => {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(d);
};

/** 等级四档 → 徽标（提示为 subtle 淡显）。 */
const SEVERITY_META: Record<AlertSeverity, { label: string; tone: Tone; subtle?: boolean }> = {
  fatal: { label: "致命", tone: "danger" },
  critical: { label: "严重", tone: "warning" },
  warning: { label: "普通", tone: "neutral" },
  info: { label: "提示", tone: "neutral", subtle: true },
};

/** 告警平台侧状态三态 → 徽标。 */
const EVENT_STATUS_META: Record<AlertEventStatus, { label: string; tone: Tone }> = {
  unassigned: { label: "未分派", tone: "neutral" },
  assigned: { label: "已分派", tone: "warning" },
  closed: { label: "已关闭", tone: "good" },
};

const SeverityPill = ({ severity }: { severity: AlertSeverity }) => {
  const m = SEVERITY_META[severity];
  return <Pill tone={m.tone} style={m.subtle ? { opacity: 0.6 } : undefined}>{m.label}</Pill>;
};

/** Agent 接管状态：shield（蓝）= 已完成/处理中；shield-off（灰）= 未接管。 */
const TakeoverCell = ({ takeover }: { takeover: AlertTakeoverStatus }) => {
  const meta = {
    done: { icon: "shield", c: color.brand, label: "已完成" },
    processing: { icon: "shield", c: color.brand, label: "处理中" },
    none: { icon: "shield-off", c: color.textFaint, label: "未接管" },
  }[takeover];
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap", color: takeover === "none" ? color.textSubtle : color.textBody, fontWeight: 600 }}>
      <Icon name={meta.icon} size={15} color={meta.c} />
      {meta.label}
    </span>
  );
};

/** 接管结果：已恢复/失败/已升级；未出结果显「—」。 */
const ResultCell = ({ it }: { it: AlertEventRow }) => {
  if (it.agent_result === "recovered") return <Pill tone="good" icon="check">已恢复</Pill>;
  if (it.agent_result === "failed") return <Pill tone="danger">失败</Pill>;
  if (it.agent_result === "escalated") return <Pill tone="warning" icon="arrow-up-right">已升级</Pill>;
  return <span style={{ color: color.textFaint }}>—</span>;
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

/** 告警清单（侧栏一级导航）：事件视角全宽表格页 = 56px header + 四下拉筛选条 + 十五列表格 + 分页。
 *  v2 起不再有行内详情抽屉/忽略/重试——事件详情归告警平台外链，处理过程看诊断会话。 */
export function AlertsPage() {
  const nav = useNavigate();
  const { me, agents, currentAgentId } = useApp();
  const isAdmin = me?.role === "platform_admin";

  // 清单跟随侧栏「选择 Agent」（不设本页的 Agent 筛选下拉）：每个 Agent 只看自己的
  // 接管清单（自己接管的 + 尚未被接管的）。切 Agent 由侧栏统一完成，本页自动重拉。
  const instanceId = currentAgentId;
  const [statusF, setStatusF] = useState<"" | AlertEventStatus>("");
  const [sevF, setSevF] = useState<"" | AlertSeverity>("");
  const [takeF, setTakeF] = useState<"" | AlertTakeoverStatus>("");
  const [catF, setCatF] = useState<string>("");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");            // 防抖后的搜索词（real 档下沉服务端，mock 档本地过滤）
  const [userIdInput, setUserIdInput] = useState(""); // 管理员「按用户查看」
  const [userId, setUserId] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<AlertEventsPage | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    const t = setTimeout(() => { setQ(search.trim()); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    const t = setTimeout(() => { setUserId(userIdInput.trim()); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [userIdInput]);

  // 挂载即拉 + 15s 轮询 + 写操作失效广播重拉；筛选/翻页切换用 AbortController latest-wins
  useEffect(() => {
    let alive = true;
    let controller: AbortController | null = null;
    const load = () => {
      controller?.abort();
      controller = new AbortController();
      const signal = controller.signal;
      const params = {
        // 管理员「按用户查看」时不能再按自己的实例过滤（目标用户的告警不属于我的 Agent）
        instanceId: userId ? undefined : instanceId || undefined,
        alertStatus: statusF || undefined,
        severity: sevF || undefined,
        takeover: takeF || undefined,
        category: catF || undefined,
        search: q || undefined,
        page,
        pageSize: PAGE_SIZE,
        signal,
      };
      // 管理员填了 user_id 才切 admin 端点；留空走普通端点
      const p = userId
        ? alertsApi.adminListEvents({ ...params, userId })
        : alertsApi.listEvents(params);
      p.then((next) => {
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
  }, [instanceId, statusF, sevF, takeF, catF, q, page, userId]);

  const activeFilters = [statusF, sevF, takeF, catF].filter(Boolean).length;
  const clearFilters = () => { setStatusF(""); setSevF(""); setTakeF(""); setCatF(""); setPage(1); };

  // 「接管规则」/空态跳转的目标实例 = 当前 Agent（清单与侧栏同源，无本页切换）
  const settingsTarget = instanceId;
  const items = data?.items ?? [];
  const agentName = agents.find((a) => a.instance_id === currentAgentId)?.name;

  return (
    <>
      <header style={{ flex: "0 0 auto", height: 56, borderBottom: `1px solid ${color.border}`, background: "#fff", display: "flex", alignItems: "center", padding: "0 24px", gap: 12 }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>告警清单</div>
        {/* 只读徽标（样式对齐 Workbench「当前使用」）：清单归属当前 Agent，切换在侧栏做 */}
        {agentName ? (
          <span title="清单跟随左侧「选择 Agent」；在侧栏切换 Agent 即切换清单" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color: color.textNav, background: color.neutralBg, border: `1px solid ${color.border}`, padding: "4px 10px", borderRadius: radius.pill, whiteSpace: "nowrap", flex: "0 0 auto" }}>
            <Icon name="robot" size={14} color={color.brand} />{agentName}
          </span>
        ) : null}
        <span style={{ fontSize: 11, color: color.textSubtle }}>命中本 Agent 订阅规则的告警自动排队诊断</span>
        {isAdmin ? (
          <div style={{ width: 190 }} title="平台管理员：填 user_id 查看该用户视角，留空看自己">
            <TextInput value={userIdInput} onChange={setUserIdInput} icon="user-search" mono placeholder="按用户查看（user_id）" style={{ height: 34, fontSize: 12 }} />
          </div>
        ) : null}
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
            ...CATEGORY_OPTIONS.map((c) => ({ label: c, value: c })),
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

        {data && items.length === 0 ? (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12, padding: "72px 0", color: color.textSubtle }}>
            <Icon name="bell-bolt" size={36} color={color.textFaint} />
            <div style={{ fontSize: 13 }}>暂无告警记录</div>
            {settingsTarget ? (
              <Button variant="secondary" icon="settings" onClick={() => nav("/settings/alerts")}>去配置接管规则</Button>
            ) : null}
          </div>
        ) : (
          <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, overflowX: "auto" }}>
            {/* overflowX auto（宿主 div）：十五列在窄视口整体横向滚动，操作列不被裁掉 */}
            <table style={{ width: "100%", minWidth: 1560, borderCollapse: "collapse", fontSize: 12.5 }}>
              <thead>
                <tr>
                  <th style={th}>告警编号</th>
                  <th style={th}>告警类型</th>
                  <th style={{ ...th, minWidth: 150 }}>告警名称</th>
                  <th style={th}>告警对象</th>
                  <th style={th}>APPID</th>
                  <th style={{ ...th, minWidth: 150 }}>告警描述</th>
                  <th style={th}>告警状态</th>
                  <th style={th}>告警等级</th>
                  <th style={th}>Agent 接管状态</th>
                  <th style={th}>接管结果</th>
                  {/* 用户评价整列占位置灰：列头一起浅灰 */}
                  <th style={{ ...th, color: color.textFaint, ...feedbackDim }}>用户评价</th>
                  <th style={th}>开始时间</th>
                  <th style={th}>结束时间</th>
                  <th style={th}>时长</th>
                  <th style={th}>操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => {
                  const clickable = it.run_clickable && !!it.run_id;
                  return (
                    <Interactive
                      key={it.alert_no}
                      as="tr"
                      data-testid="alerts-incident-row"
                      baseStyle={{ background: "transparent" }}
                      hoverStyle={{ background: color.surfaceTint }}
                    >
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
                        <span style={{ display: "block", color: color.brand, fontWeight: 700, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={it.title}>
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
                      <td style={td}><TakeoverCell takeover={it.takeover_status} /></td>
                      <td style={td}><ResultCell it={it} /></td>
                      <td style={{ ...td, ...feedbackDim }}><FeedbackCell it={it} /></td>
                      <td style={{ ...td, color: color.textNav, whiteSpace: "nowrap" }}>{fmtTime(it.started_at)}</td>
                      <td style={{ ...td, fontFamily: font.mono, fontSize: 11.5, color: color.textNav, whiteSpace: "nowrap" }}>{fmtTime(it.ended_at)}</td>
                      <td style={{ ...td, fontFamily: font.mono, color: color.textNav, whiteSpace: "nowrap" }}>{it.duration ?? "—"}</td>
                      <td style={td}>
                        {clickable ? (
                          <span
                            onClick={() => nav(`/agent-runs/${it.run_id}`)}
                            style={{ fontSize: 12, fontWeight: 600, color: color.brand, cursor: "pointer", whiteSpace: "nowrap" }}
                          >
                            查看处理会话
                          </span>
                        ) : (
                          <span
                            title={it.run_id ? "处理会话已过期清理" : "该告警未被 Agent 接管，无处理会话"}
                            style={{ fontSize: 12, fontWeight: 600, color: color.textFaint, cursor: "not-allowed", whiteSpace: "nowrap" }}
                          >
                            查看处理会话
                          </span>
                        )}
                      </td>
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
