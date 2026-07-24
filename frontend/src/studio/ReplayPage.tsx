import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { color, radius } from "../theme/tokens";
import { Icon, Dot } from "../ui";
import { api } from "../lib/api";
import { studioApi } from "./api";
import { useApp } from "../lib/appState";
import { RUN_TONE, RunDetailView } from "./RunDetailView";

// 模块级常量：给 RunDetailView 的注入 fetch 必须引用稳定（进 useCallback 依赖）。
// 用户回放端点 owner-only（后端校验），LLM 输入 messages 服务端置空（平台提示词不外露）。
const fetchReplayDetail = (runId: string) => studioApi.replayRunDetail(runId);
const fetchReplayMessages = (runId: string) => studioApi.replayRunMessages(runId);

/** 回放（对话界面侧栏入口）：用户回看**自己的**会话执行过程。
 *  左列 = 自己的会话列表（listConversations 已按登录身份过滤），右侧 = 与管理员
 *  Agent Studio 同款的 对话内容/调用明细 双视图（RunDetailView 注入用户端 fetch）。 */
export function ReplayPage() {
  const { runId } = useParams();
  const nav = useNavigate();
  const { conversations } = useApp();
  const [err, setErr] = useState("");

  return (
    <>
      <header style={{ flex: "0 0 auto", height: 56, borderBottom: `1px solid ${color.border}`, background: "#fff", display: "flex", alignItems: "center", padding: "0 24px", gap: 12 }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>回放</div>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 11, color: color.textSubtle }}>回看自己会话的 Agent 执行过程（仅本人可见）</span>
      </header>
      <div style={{ flex: 1, minHeight: 0, display: "flex", overflow: "hidden" }}>
        {/* 左列：自己的会话（不按 Agent 过滤——回放面向全部历史） */}
        <aside style={{ width: 260, flexShrink: 0, borderRight: `1px solid ${color.border}`, background: "#fff", overflowY: "auto", padding: "10px 8px" }}>
          {conversations.length === 0 ? (
            <div style={{ fontSize: 12, color: color.textSubtle, padding: "10px 8px" }}>暂无会话。</div>
          ) : conversations.map((c) => {
            const active = c.id === runId;
            return (
              <div key={c.id} onClick={() => nav(`/replay/${c.id}`)}
                style={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 10px", borderRadius: radius.md, cursor: "pointer", background: active ? color.brandTintBg : "transparent", marginBottom: 2 }}>
                <Dot tone={RUN_TONE[c.status ?? ""] ?? "neutral"} />
                <span style={{ flex: 1, minWidth: 0, fontSize: 12.5, fontWeight: active ? 700 : 500, color: active ? color.brandStrong : color.textStrong, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {c.title || "新对话"}
                </span>
                <Icon name="chevron-right" size={13} color={active ? color.brand : color.textFaint} />
              </div>
            );
          })}
        </aside>
        {/* 右侧：run 详情（对话内容 / 调用明细） */}
        <div style={{ flex: 1, minWidth: 0, overflowY: "auto", padding: 20 }}>
          <div style={{ maxWidth: 980, margin: "0 auto", display: "flex", flexDirection: "column", gap: 14 }}>
            {err ? (
              <div style={{ display: "flex", alignItems: "center", gap: 8, background: "#fdecec", border: "1px solid #f5c2c0", borderRadius: radius.lg, padding: "9px 13px", fontSize: 12, color: color.dangerText }}>
                <Icon name="alert-triangle" size={14} color={color.dangerText} />{err}
              </div>
            ) : null}
            {runId ? (
              <RunDetailView runId={runId} onErr={setErr}
                fetchDetail={fetchReplayDetail} fetchMessages={fetchReplayMessages} />
            ) : (
              <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "18px 20px", fontSize: 12.5, color: color.textSubtle }}>
                选择左侧会话，查看它的对话内容与 Agent 调用明细（子 Agent 派发、工具与 MCP 调用、模型输出、耗时与 token）。
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

export default ReplayPage;
