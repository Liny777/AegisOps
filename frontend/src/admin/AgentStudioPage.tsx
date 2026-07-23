import { useCallback, useEffect, useMemo, useState } from "react";
import { color, radius, font } from "../theme/tokens";
import { Icon, Dot, Pill, Pagination, SegRadio } from "../ui";
import { api } from "../lib/api";
import type { AdminTableData, StudioMessage, StudioRunDetail, StudioRunsPage } from "../lib/api/types";
import { StudioAgentCard, fmtMs, fmtTokens } from "./StudioAgentCard";
import { StudioTranscript } from "./StudioTranscript";

const PAGE_SIZE = 20;

const RUN_TONE: Record<string, "good" | "warning" | "neutral"> = { active: "good", closed: "neutral" };

function fmtTime(v: string | null): string {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(d);
}

/** Agent Studio（管理员回溯复盘）：用户 → run → 调用明细 三级钻取，drill 状态自含（照模板管理 tplDrill）。 */
export function AgentStudioPage() {
  const [userDrill, setUserDrill] = useState<{ id: string; name: string } | null>(null);
  const [runDrill, setRunDrill] = useState<{ id: string; title: string } | null>(null);
  const [err, setErr] = useState("");

  const crumbs = [
    { label: "Agent Studio", onClick: userDrill ? () => { setUserDrill(null); setRunDrill(null); } : undefined },
    ...(userDrill ? [{ label: `${userDrill.name || userDrill.id} · 运行记录`, onClick: runDrill ? () => setRunDrill(null) : undefined }] : []),
    ...(runDrill ? [{ label: runDrill.title || "调用明细", onClick: undefined }] : []),
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, fontWeight: 600 }}>
        {crumbs.map((c, i) => (
          <span key={i} style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
            {i > 0 ? <Icon name="chevron-right" size={14} color={color.textFaint} /> : null}
            <span onClick={c.onClick} style={{ cursor: c.onClick ? "pointer" : "default", color: c.onClick ? color.brand : color.textStrong, fontWeight: i === crumbs.length - 1 ? 700 : 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 360 }}>{c.label}</span>
          </span>
        ))}
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 11, color: color.textSubtle, fontWeight: 400 }}>本页含用户会话原文，仅平台管理员可见</span>
      </div>
      {err ? (
        <div style={{ display: "flex", alignItems: "center", gap: 8, background: "#fdecec", border: "1px solid #f5c2c0", borderRadius: radius.lg, padding: "9px 13px", fontSize: 12, color: color.dangerText }}>
          <Icon name="alert-triangle" size={14} color={color.dangerText} />{err}
        </div>
      ) : null}
      {!userDrill ? (
        <UserPicker onErr={setErr} onPick={(id, name) => setUserDrill({ id, name })} />
      ) : !runDrill ? (
        <RunList userId={userDrill.id} onErr={setErr} onPick={(id, title) => setRunDrill({ id, title })} />
      ) : (
        <RunDetailView runId={runDrill.id} onErr={setErr} />
      )}
    </div>
  );
}

/** 级 1：用户列表（复用 /admin/users 的服务端分页 + 搜索；不渲染白名单操作列）。 */
function UserPicker({ onPick, onErr }: { onPick: (id: string, name: string) => void; onErr: (e: string) => void }) {
  const [table, setTable] = useState<AdminTableData | null>(null);
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");

  useEffect(() => {
    onErr("");
    api.getAdminTable("users", { page, pageSize: PAGE_SIZE, q: q.trim() || undefined })
      .then(setTable)
      .catch((e) => { setTable(null); onErr((e as Error).message || "加载失败"); });
  }, [page, q, onErr]);
  useEffect(() => { setPage(1); }, [q]);

  // users 表前 5 列 = user_id / 展示名 / role / 白名单 / 最近登录；操作列裁掉，换成「查看运行」
  const cols = useMemo(() => (table ? [...table.cols.slice(0, 5), { label: "调用记录", width: "96px" }] : []), [table]);
  const gridCols = cols.map((c) => c.width ?? "1fr").join(" ");

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <input
          placeholder="搜索 user_id / 展示名"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ width: 240, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: "6px 10px", fontSize: 12, outline: "none", background: "#fff" }}
        />
        <span style={{ fontSize: 11.5, color: color.textSubtle }}>选择用户查看其全部会话的 Agent 调用明细（含已删除会话）</span>
      </div>
      {table ? (
        <>
          <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, overflow: "hidden" }}>
            <div style={{ display: "grid", gridTemplateColumns: gridCols, gap: 12, padding: "11px 16px", background: color.surfaceAlt, borderBottom: `1px solid ${color.border}`, fontSize: 11.5, fontWeight: 700, color: color.textSubtle }}>
              {cols.map((c, i) => <div key={i}>{c.label}</div>)}
            </div>
            {table.rows.map((r, ri) => (
              <div key={r.id} style={{ display: "grid", gridTemplateColumns: gridCols, gap: 12, padding: "12px 16px", alignItems: "center", borderTop: ri ? `1px solid ${color.borderFaint}` : "none" }}>
                {r.cells.slice(0, 5).map((cell, ci) => (
                  <div key={ci} style={{ minWidth: 0 }}>
                    {cell.kind === "badge" ? (
                      <Pill tone={cell.tone ?? "neutral"}>{cell.text}</Pill>
                    ) : (
                      <span style={{ fontSize: 12.5, color: color.textStrong, fontFamily: cell.mono ? font.mono : undefined, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", display: "block" }}>{cell.text}</span>
                    )}
                  </div>
                ))}
                <span onClick={() => onPick(r.id, String(r.cells[1]?.text || r.cells[0]?.text || r.id))}
                  style={{ fontSize: 12, color: color.brand, fontWeight: 600, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}>
                  查看运行<Icon name="chevron-right" size={13} color={color.brand} />
                </span>
              </div>
            ))}
          </div>
          {table.total !== undefined ? (
            <Pagination page={table.page ?? page} pageSize={table.pageSize ?? PAGE_SIZE} total={table.total} onPage={setPage} />
          ) : null}
        </>
      ) : null}
    </>
  );
}

/** 级 2：该用户 run 列表（含软删，带 span 聚合指标）。 */
function RunList({ userId, onPick, onErr }: {
  userId: string; onPick: (id: string, title: string) => void; onErr: (e: string) => void;
}) {
  const [data, setData] = useState<StudioRunsPage | null>(null);
  const [page, setPage] = useState(1);

  useEffect(() => {
    onErr("");
    api.adminStudioRuns(userId, { page, pageSize: PAGE_SIZE })
      .then(setData)
      .catch((e) => { setData(null); onErr((e as Error).message || "加载失败"); });
  }, [userId, page, onErr]);

  const gridCols = "1.6fr 0.7fr 0.9fr 0.55fr 0.55fr 0.55fr 0.7fr 0.8fr";
  const head = ["会话", "状态", "开始时间", "Agents", "LLM", "工具", "tokens", "总耗时"];
  return data ? (
    <>
      <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, overflow: "hidden" }}>
        <div style={{ display: "grid", gridTemplateColumns: gridCols, gap: 12, padding: "11px 16px", background: color.surfaceAlt, borderBottom: `1px solid ${color.border}`, fontSize: 11.5, fontWeight: 700, color: color.textSubtle }}>
          {head.map((h) => <div key={h}>{h}</div>)}
        </div>
        {data.items.length === 0 ? (
          <div style={{ padding: "14px 16px", fontSize: 12, color: color.textSubtle }}>该用户暂无会话。</div>
        ) : data.items.map((r, ri) => (
          <div key={r.agent_run_id} onClick={() => onPick(r.agent_run_id, r.run_title || "未命名会话")}
            style={{ display: "grid", gridTemplateColumns: gridCols, gap: 12, padding: "12px 16px", alignItems: "center", borderTop: ri ? `1px solid ${color.borderFaint}` : "none", cursor: "pointer" }}>
            <div style={{ minWidth: 0, display: "flex", alignItems: "center", gap: 7 }}>
              <span style={{ fontSize: 12.5, fontWeight: 600, color: color.textStrong, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.run_title || "未命名会话"}</span>
              {r.deleted ? <Pill tone="danger">已删除</Pill> : null}
            </div>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11.5, fontWeight: 600, color: color.textNav }}>
              <Dot tone={RUN_TONE[r.run_status] ?? "neutral"} />{r.run_status || "—"}
            </span>
            <span style={{ fontSize: 12, color: color.textNav }}>{fmtTime(r.started_at)}</span>
            <span style={{ fontSize: 12.5 }}>{r.stats.agents}</span>
            <span style={{ fontSize: 12.5 }}>{r.stats.llm_calls}</span>
            <span style={{ fontSize: 12.5 }}>{r.stats.tool_calls}</span>
            <span style={{ fontSize: 12.5, fontFamily: font.mono }}>{fmtTokens(r.stats.total_tokens)}</span>
            <span style={{ fontSize: 12, color: color.textNav, display: "flex", alignItems: "center", gap: 6, justifyContent: "space-between" }}>
              {fmtMs(r.stats.latency_ms)}<Icon name="chevron-right" size={13} color={color.textFaint} />
            </span>
          </div>
        ))}
      </div>
      <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />
    </>
  ) : null;
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.lg, padding: "9px 14px", minWidth: 88 }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, color: color.textSubtle, marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 700, color: color.textStrong, fontFamily: font.mono }}>{value}</div>
    </div>
  );
}

/** 级 3：run 详情（rollup 统计 + 对话内容/调用明细切换；active 时 3s 轮询实时刷新）。 */
function RunDetailView({ runId, onErr }: { runId: string; onErr: (e: string) => void }) {
  const [data, setData] = useState<StudioRunDetail | null>(null);
  const [messages, setMessages] = useState<StudioMessage[]>([]);
  // 默认对话内容——管理员回溯用户问题最直接的材料；调用明细一键切换
  const [view, setView] = useState<"chat" | "calls">("chat");

  const load = useCallback(() => {
    api.adminStudioRunDetail(runId)
      .then(setData)
      .catch((e) => onErr((e as Error).message || "加载失败"));
    api.adminStudioRunMessages(runId)
      .then(setMessages)
      .catch(() => setMessages([]));  // 对话取失败不遮详情（详情侧已有错误横幅）
  }, [runId, onErr]);

  useEffect(() => { onErr(""); load(); }, [load, onErr]);
  useEffect(() => {
    if (data?.run.run_status !== "active") return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [data?.run.run_status, load]);

  if (!data) return null;
  const { run, rollup } = data;
  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color: color.textNav }}>
          <Dot tone={RUN_TONE[run.run_status] ?? "neutral"} />{run.run_status}
        </span>
        {run.deleted ? <Pill tone="danger">用户已删除该会话</Pill> : null}
        <span style={{ fontSize: 11.5, color: color.textSubtle }}>{fmtTime(run.started_at)} — {fmtTime(run.ended_at)}</span>
        <span style={{ fontSize: 11, color: color.textFaint, fontFamily: font.mono }}>{run.agent_run_id}</span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 11.5, color: color.textSubtle, fontFamily: font.mono }}>{run.user_id}</span>
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <Stat label="Agents" value={rollup.agents ?? 0} />
        <Stat label="LLM 调用" value={rollup.llm_calls ?? 0} />
        <Stat label="工具调用" value={rollup.tool_calls ?? 0} />
        <Stat label="输入 token" value={fmtTokens(rollup.input_tokens ?? 0)} />
        <Stat label="输出 token" value={fmtTokens(rollup.output_tokens ?? 0)} />
        <Stat label="总耗时" value={fmtMs(rollup.total_latency_ms ?? 0)} />
        <Stat label="模型" value={<span style={{ fontSize: 12 }}>{(rollup.models ?? []).join(" / ") || "—"}</span>} />
      </div>
      <SegRadio<"chat" | "calls">
        value={view}
        onChange={setView}
        options={[{ label: "对话内容", value: "chat" }, { label: "调用明细", value: "calls" }]}
      />
      {view === "chat" ? (
        <StudioTranscript messages={messages} />
      ) : data.agents.length === 0 ? (
        <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "16px 18px", fontSize: 12.5, color: color.textSubtle }}>
          该会话暂无调用记录 —— 可能创建后未发起任务、运行早于 Agent Studio 启用，或记录已过保留期。
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {data.agents.map((a) => <StudioAgentCard key={`${a.session_id}|${a.role}`} agent={a} />)}
        </div>
      )}
    </>
  );
}
