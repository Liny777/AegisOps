import { useNavigate } from "react-router-dom";
import { color } from "../theme/tokens";
import { Icon, Button } from "../ui";

function Centered({ icon, title, desc, action }: { icon: string; title: string; desc: string; action?: React.ReactNode }) {
  return (
    <div style={{ height: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: color.pageBg }}>
      <div style={{ textAlign: "center", maxWidth: 420, padding: 24 }}>
        <div style={{ width: 56, height: 56, borderRadius: 16, background: "#fff", border: `1px solid ${color.border}`, display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 16 }}>
          <Icon name={icon} size={28} color={color.textSubtle} />
        </div>
        <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 8 }}>{title}</div>
        <div style={{ fontSize: 13.5, color: color.textMuted, lineHeight: 1.7, marginBottom: 18 }}>{desc}</div>
        {action}
      </div>
    </div>
  );
}

export function NotWhitelisted() {
  return (
    <Centered
      icon="lock-access"
      title="尚未开通 OpenOps"
      desc="你的账号还不在 OpenOps 试点白名单内。请联系平台管理员申请开通后再进入。"
      action={<Button variant="secondary" icon="mail">联系管理员</Button>}
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
