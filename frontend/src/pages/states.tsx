import { useNavigate } from "react-router-dom";
import { useApp } from "../lib/appState";
import { peekAutoQuestion } from "../lib/autosend";
import { color, radius } from "../theme/tokens";
import { Icon, Button } from "../ui";

function Centered({ icon, title, desc, action, extra }: { icon: string; title: string; desc: string; action?: React.ReactNode; extra?: React.ReactNode }) {
  return (
    <div style={{ height: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: color.pageBg }}>
      <div style={{ textAlign: "center", maxWidth: 420, padding: 24 }}>
        <div style={{ width: 56, height: 56, borderRadius: 16, background: "#fff", border: `1px solid ${color.border}`, display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 16 }}>
          <Icon name={icon} size={28} color={color.textSubtle} />
        </div>
        <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 8 }}>{title}</div>
        <div style={{ fontSize: 13.5, color: color.textMuted, lineHeight: 1.7, marginBottom: 18 }}>{desc}</div>
        {extra}
        {action}
      </div>
    </div>
  );
}

/** 外链 ?q= 带入问题的保留提示（未开通/未初始化场景复用）：问题在本标签页会话内保留，
 *  条件满足（开通/初始化完成）进入对话时自动发送。 */
export function PendingQuestionNotice() {
  const q = peekAutoQuestion();
  if (!q) return null;
  const preview = q.length > 60 ? q.slice(0, 60) + "…" : q;
  return (
    <div style={{ textAlign: "left", background: color.brandTintBg, border: `1px solid ${color.brandTintBorder}`, borderRadius: radius.md, padding: "10px 12px", marginBottom: 18, display: "flex", gap: 8 }}>
      <Icon name="message-2" size={15} color={color.brand} />
      <div style={{ fontSize: 12.5, color: color.textBody, lineHeight: 1.6, minWidth: 0 }}>
        <div style={{ fontWeight: 600, marginBottom: 2 }}>你带来的问题已保留</div>
        <div style={{ color: color.textSubtle, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={q}>{preview}</div>
      </div>
    </div>
  );
}

export function NotWhitelisted() {
  const { me, refresh } = useApp();
  const who = me ? `${me.display_name}（${me.user_id}）` : "当前账号";
  return (
    <Centered
      icon="lock-access"
      title="尚未开通 运维Agent"
      desc={`${who} 还不在 运维Agent 白名单内。请把账号发给平台管理员申请开通；开通后点击下方按钮重新进入。`}
      extra={<PendingQuestionNotice />}
      action={<Button variant="secondary" icon="refresh" onClick={refresh}>已开通？重新检查</Button>}
    />
  );
}

export function Forbidden() {
  const nav = useNavigate();
  return (
    <Centered
      icon="shield-x"
      title="403 · 无权访问"
      desc="该页面仅平台管理员可访问。"
      action={<Button onClick={() => nav("/")}>返回工作台</Button>}
    />
  );
}

export function Loading() {
  return (
    <div style={{ height: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: color.pageBg }}>
      <Icon name="loader-2" size={26} color={color.brand} spin />
    </div>
  );
}
