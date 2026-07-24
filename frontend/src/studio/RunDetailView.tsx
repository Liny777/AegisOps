import { useCallback, useEffect, useState } from "react";
import { color, radius, font } from "../theme/tokens";
import { Dot, Pill, SegRadio } from "../ui";
import type { StudioMessage, StudioRunDetail } from "./types";
import { StudioAgentCard, fmtMs, fmtTokens } from "./StudioAgentCard";
import { StudioTranscript } from "./StudioTranscript";

export const RUN_TONE: Record<string, "good" | "warning" | "neutral"> = { active: "good", closed: "neutral" };

export function fmtTime(v: string | null): string {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(d);
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.lg, padding: "9px 14px", minWidth: 88 }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, color: color.textSubtle, marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 700, color: color.textStrong, fontFamily: font.mono }}>{value}</div>
    </div>
  );
}

/** run 详情（rollup 统计 + 对话内容/调用明细切换；active 时 3s 轮询实时刷新）。
 *  fetch 注入：管理员版传 adminStudioRunDetail/adminStudioRunMessages，用户回放版传
 *  replayRunDetail/replayRunMessages（owner-only，LLM 输入服务端置空）。渲染层两版共用。 */
export function RunDetailView({ runId, onErr, fetchDetail, fetchMessages }: {
  runId: string;
  onErr: (e: string) => void;
  fetchDetail: (runId: string) => Promise<StudioRunDetail>;
  fetchMessages: (runId: string) => Promise<StudioMessage[]>;
}) {
  const [data, setData] = useState<StudioRunDetail | null>(null);
  const [messages, setMessages] = useState<StudioMessage[]>([]);
  // 默认对话内容——回溯用户问题最直接的材料；调用明细一键切换
  const [view, setView] = useState<"chat" | "calls">("chat");

  const load = useCallback(() => {
    fetchDetail(runId)
      .then(setData)
      .catch((e) => onErr((e as Error).message || "加载失败"));
    fetchMessages(runId)
      .then(setMessages)
      .catch(() => setMessages([]));  // 对话取失败不遮详情（详情侧已有错误横幅）
  }, [runId, onErr, fetchDetail, fetchMessages]);

  useEffect(() => { onErr(""); setData(null); setMessages([]); load(); }, [load, onErr]);
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
