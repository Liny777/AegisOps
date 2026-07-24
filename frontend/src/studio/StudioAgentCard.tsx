import { useState } from "react";
import { color, radius, font } from "../theme/tokens";
import { Icon, Pill } from "../ui";
import type { StudioAgentCardData, StudioCall, StudioLlmCall, StudioToolCall } from "./types";
import { roleVisual } from "../workbench/activity/visuals";

export function fmtTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : String(n);
}
export function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}
function fmtClock(v: string | null): string {
  if (!v) return "";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(d);
}
/** span 原文常是序列化 JSON：能 parse 就美化缩进，不能就原样展示。 */
function prettyMaybeJson(s: string): string {
  const t = s.trim();
  if (!t.startsWith("{") && !t.startsWith("[")) return s;
  try {
    return JSON.stringify(JSON.parse(t), null, 2);
  } catch {
    return s;
  }
}

const DELEGATION_TONE: Record<string, "good" | "warning" | "danger" | "neutral"> = {
  completed: "good", running: "warning", timeout: "danger", failed_no_report: "danger", cancelled: "neutral",
};

function Chip({ icon, children, title }: { icon?: string; children: React.ReactNode; title?: string }) {
  return (
    <span title={title} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, fontWeight: 600, color: color.textNav, background: color.neutralBg, border: `1px solid ${color.border}`, padding: "2px 8px", borderRadius: radius.pill, whiteSpace: "nowrap" }}>
      {icon ? <Icon name={icon} size={12} color={color.textMuted} /> : null}
      {children}
    </span>
  );
}

function RawText({ text }: { text: string }) {
  return (
    <pre style={{ margin: 0, padding: "10px 12px", background: color.surfaceAlt, border: `1px solid ${color.borderFaint}`, borderRadius: radius.md, fontSize: 11.5, lineHeight: 1.55, fontFamily: font.mono, whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 320, overflowY: "auto", color: color.textBody }}>
      {prettyMaybeJson(text)}
    </pre>
  );
}

/** 折叠区块（交接内容 / agent 层输入输出 / 调用详情共用）。 */
function Fold({ title, icon, defaultOpen = false, children }: {
  title: React.ReactNode; icon: string; defaultOpen?: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ border: `1px solid ${color.borderFaint}`, borderRadius: radius.lg, overflow: "hidden" }}>
      <div onClick={() => setOpen(!open)} style={{ display: "flex", alignItems: "center", gap: 7, padding: "8px 12px", cursor: "pointer", background: color.surfaceAlt, fontSize: 12, fontWeight: 600, color: color.textNav, userSelect: "none" }}>
        <Icon name={icon} size={13} color={color.textMuted} />
        <span style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 8 }}>{title}</span>
        <Icon name={open ? "chevron-up" : "chevron-down"} size={13} color={color.textFaint} />
      </div>
      {open ? <div style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: 8 }}>{children}</div> : null}
    </div>
  );
}

function Labeled({ label, text }: { label: string; text: string }) {
  return (
    <div>
      <div style={{ fontSize: 11, fontWeight: 700, color: color.textSubtle, marginBottom: 4 }}>{label}</div>
      {text ? <RawText text={text} /> : <div style={{ fontSize: 11.5, color: color.textFaint }}>（空）</div>}
    </div>
  );
}

function CallRow({ call }: { call: StudioCall }) {
  const [open, setOpen] = useState(false);
  const isErr = call.status === "ERROR";
  const isLlm = call.kind === "llm";
  const llm = call as StudioLlmCall;
  const tool = call as StudioToolCall;
  return (
    <li style={{ borderTop: `1px solid ${color.borderFaint}` }}>
      <div onClick={() => setOpen(!open)} style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 14px", cursor: "pointer", userSelect: "none" }}>
        <Icon name={isLlm ? "sparkles" : "tool"} size={14} color={isErr ? color.danger : isLlm ? "#7c3aed" : color.brand} />
        <span style={{ fontSize: 12.5, fontWeight: 600, color: isErr ? color.dangerText : color.textStrong, fontFamily: font.mono }}>
          {isLlm ? llm.model || "LLM" : tool.tool_name || "工具"}
        </span>
        {isErr ? <Pill tone="danger">ERROR</Pill> : null}
        <span style={{ flex: 1, minWidth: 0, fontSize: 11, color: color.textSubtle, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {isLlm
            ? `↑${fmtTokens(llm.input_tokens)} ↓${fmtTokens(llm.output_tokens)}${llm.finish_reason ? ` · ${llm.finish_reason}` : ""}`
            : tool.tool_args.slice(0, 80)}
        </span>
        <span style={{ fontSize: 11, color: color.textMuted, whiteSpace: "nowrap" }}>{fmtMs(call.latency_ms)}</span>
        <span style={{ fontSize: 10.5, color: color.textFaint, whiteSpace: "nowrap" }}>{fmtClock(call.started_at)}</span>
        <Icon name={open ? "chevron-up" : "chevron-down"} size={13} color={color.textFaint} />
      </div>
      {open ? (
        <div style={{ padding: "0 14px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
          {isLlm ? (
            <>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <Chip icon="cpu">{llm.model}{llm.provider ? ` · ${llm.provider}` : ""}</Chip>
                <Chip icon="arrows-exchange" title="输入 / 输出 / 缓存命中 token">
                  ↑{fmtTokens(llm.input_tokens)} ↓{fmtTokens(llm.output_tokens)}{llm.cache_input_tokens ? ` · 缓存 ${fmtTokens(llm.cache_input_tokens)}` : ""}
                </Chip>
                <Chip icon="clock">{fmtMs(llm.latency_ms)}</Chip>
                {llm.finish_reason ? <Chip icon="flag">{llm.finish_reason}</Chip> : null}
              </div>
              {/* 输入 messages 有值才渲染：用户回放端点会置空（平台提示词不外露），管理员版全量 */}
              {llm.input ? <Labeled label="输入 messages" text={llm.input} /> : null}
              <Labeled label="模型输出" text={llm.output} />
            </>
          ) : (
            <>
              <Labeled label="入参" text={tool.tool_args} />
              <Labeled label="结果" text={tool.tool_result} />
            </>
          )}
        </div>
      ) : null}
    </li>
  );
}

/** 一个 Agent 实例（main / 每次子派发各一张卡）：指标 chips + 交接 + agent 层 IO + 逐条调用。 */
export function StudioAgentCard({ agent }: { agent: StudioAgentCardData }) {
  const rv = roleVisual(agent.role);
  const t = agent.totals;
  const ho = agent.handover;
  return (
    <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 14px", borderBottom: `1px solid ${color.borderFaint}` }}>
        <span style={{ width: 30, height: 30, borderRadius: radius.md, background: color.brandTintBg, display: "inline-flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
          <Icon name={rv.icon} size={16} color={rv.color} />
        </span>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 13.5, fontWeight: 700, color: color.textStrong }}>{agent.agent_name || `sre-${agent.role}` || "Agent"}</span>
            {agent.is_main ? <Pill tone="good">主 Agent</Pill> : <Pill tone="neutral">子 · {agent.role}</Pill>}
          </div>
          <div style={{ fontSize: 10.5, color: color.textFaint, fontFamily: font.mono, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{agent.session_id}</div>
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
          <Chip icon="sparkles">{t.llm_calls} LLM</Chip>
          <Chip icon="tool">{t.tool_calls} 工具</Chip>
          <Chip icon="arrows-exchange" title="输入 / 输出 token 合计">↑{fmtTokens(t.input_tokens)} ↓{fmtTokens(t.output_tokens)}</Chip>
          <Chip icon="clock">{fmtMs(t.latency_ms)}</Chip>
        </div>
      </div>

      {(ho || agent.agent_io) ? (
        <div style={{ padding: "10px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
          {ho ? (
            <Fold icon="transfer" defaultOpen title={
              <>
                <span>主↔子交接</span>
                <Pill tone={DELEGATION_TONE[ho.delegation_status] ?? "neutral"}>{ho.delegation_status}</Pill>
                {ho.dispatch_batch_no != null ? <span style={{ fontSize: 10.5, color: color.textFaint }}>批次 {ho.dispatch_batch_no}</span> : null}
              </>
            }>
              <Labeled label="派发指令（main → 子）" text={ho.task_text} />
              <Labeled label={`子 Agent 汇报（子 → main）${ho.had_final_report ? "" : " · 无最终汇报"}`} text={ho.report_text} />
            </Fold>
          ) : null}
          {agent.agent_io ? (
            <Fold icon="message-2" title={<span>Agent 层输入 / 输出{agent.is_main ? "（用户问题 → 最终答复）" : ""}</span>}>
              <Labeled label="输入" text={agent.agent_io.input} />
              <Labeled label="输出" text={agent.agent_io.output} />
            </Fold>
          ) : null}
        </div>
      ) : null}

      {agent.calls.length ? (
        <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {agent.calls.map((c, i) => <CallRow key={i} call={c} />)}
        </ol>
      ) : (
        <div style={{ padding: "10px 14px", fontSize: 11.5, color: color.textSubtle }}>暂无调用记录（可能仍在执行，或早于 Studio 启用）。</div>
      )}
    </div>
  );
}
