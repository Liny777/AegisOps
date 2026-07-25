import { color, radius } from "../theme/tokens";
import { Icon } from "../ui";
import type { StudioMessage } from "./types";

/** 管理员只读对话记录（照 Workbench 的 UserBubble/BotBubble 样板，纯文本 pre-wrap，
 *  不接 CopilotKit）。全文无截断；源数据无时间戳，故不显示时间。 */
export function StudioTranscript({ messages }: { messages: StudioMessage[] }) {
  if (!messages.length) {
    return (
      <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "16px 18px", fontSize: 12.5, color: color.textSubtle }}>
        该会话暂无持久化对话记录 —— mock runtime 不落对话，或运行早于对话持久化。
      </div>
    );
  }
  return (
    <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "18px 20px", display: "flex", flexDirection: "column", gap: 14 }}>
      {messages.map((m) =>
        m.role === "user" ? (
          <div key={m.id} style={{ display: "flex", justifyContent: "flex-end" }}>
            <div style={{ maxWidth: "80%", background: color.brand, color: "#fff", padding: "9px 13px", borderRadius: "14px 14px 4px 14px", whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.6, wordBreak: "break-word" }}>
              {m.content}
            </div>
          </div>
        ) : (
          <div key={m.id} style={{ display: "flex", gap: 11 }}>
            <div style={{ width: 30, height: 30, borderRadius: radius.md, background: color.brandTintBg, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 30px" }}>
              <Icon name="robot" size={17} color={color.brand} />
            </div>
            <div style={{ maxWidth: "85%", background: "#fff", border: `1px solid ${color.border}`, color: color.textStrong, padding: "10px 14px", borderRadius: "4px 14px 14px 14px", whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.65, wordBreak: "break-word" }}>
              {m.content}
            </div>
          </div>
        ),
      )}
    </div>
  );
}
