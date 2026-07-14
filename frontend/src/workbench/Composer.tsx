import { useEffect, useMemo, useState } from "react";
import { color, radius, shadow } from "../theme/tokens";
import { Icon, Interactive } from "../ui";
import type { Skill } from "../lib/api/types";

/** 审批模式（32 号规范）：请求批准 = 始终询问；替我审批 = 仅对检查到的风险操作请求审批。 */
export type ApprovalMode = "ask" | "auto";
const APPROVAL_OPTS: { key: ApprovalMode; label: string; desc: string }[] = [
  { key: "ask", label: "请求批准", desc: "编辑文件、创建资源始终询问" },
  { key: "auto", label: "替我审批", desc: "仅对检查到的风险操作请求审批" },
];

/** SkillAwareComposer：/ 选 Skill + 审批模式（32 号）+ 发送。@ 提及 V1 不做。
 *  模型只在初始化向导配置，会话内不提供切换（去掉原「模型：xxx」选择器）。 */
export function Composer({
  skills,
  approvalMode = "ask",
  onSend,
  onApprovalMode,
}: {
  skills: Skill[];
  approvalMode?: ApprovalMode;
  onSend?: (text: string) => void;
  onApprovalMode?: (m: ApprovalMode) => void;
}) {
  const [text, setText] = useState("");
  const [slashOpen, setSlashOpen] = useState(false);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [approval, setApproval] = useState<ApprovalMode>(approvalMode);

  useEffect(() => {
    setApproval(approvalMode);
  }, [approvalMode]);

  const approvalLabel = APPROVAL_OPTS.find((o) => o.key === approval)?.label ?? "请求批准";

  const isSlash = /^\/(\S*)$/.test(text);
  const filteredSkills = useMemo(() => {
    const m = text.match(/^\/(\S*)$/);
    if (!m) return skills;
    return skills.filter((s) => s.name.slice(1).startsWith(m[1]));
  }, [text, skills]);

  const send = () => {
    if (!text.trim()) return;
    onSend?.(text.trim());
    setText("");
    setSlashOpen(false);
  };

  return (
    <div style={{ flex: "0 0 auto", padding: "12px 24px 18px", background: color.pageBg }}>
      <div style={{ maxWidth: 760, margin: "0 auto", position: "relative" }}>
        {/* slash skill menu */}
        {slashOpen || (isSlash && filteredSkills.length) ? (
          <Menu left={0} width={300} title="可用 Skill（当前 Run 已绑定）">
            {filteredSkills.map((sk) => (
              <Interactive
                key={sk.skill_id}
                onClick={() => { setText(sk.name + " "); setSlashOpen(false); }}
                baseStyle={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 13px", cursor: "pointer" }}
                hoverStyle={{ background: "#f5f8ff" }}
              >
                <Icon name="slash" size={15} color={color.brand} />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, fontFamily: "ui-monospace, monospace", color: color.textStrong }}>{sk.name}</div>
                  <div style={{ fontSize: 11, color: color.textSubtle }}>{sk.desc}</div>
                </div>
              </Interactive>
            ))}
          </Menu>
        ) : null}

        {/* approval-mode menu (32 号) */}
        {approvalOpen ? (
          <Menu left={88} width={272} title="审批模式（本次会话）">
            {APPROVAL_OPTS.map((o) => {
              const on = o.key === approval;
              return (
                <Interactive
                  key={o.key}
                  onClick={() => { setApproval(o.key); onApprovalMode?.(o.key); setApprovalOpen(false); }}
                  baseStyle={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 13px", cursor: "pointer" }}
                  hoverStyle={{ background: "#f5f8ff" }}
                >
                  <Icon name={o.key === "ask" ? "shield-check" : "shield-bolt"} size={15} color={color.brand} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: color.textStrong }}>{o.label}</div>
                    <div style={{ fontSize: 11, color: color.textSubtle }}>{o.desc}</div>
                  </div>
                  {on ? <Icon name="check" size={15} color={color.brand} /> : null}
                </Interactive>
              );
            })}
          </Menu>
        ) : null}

        <div style={{ background: "#fff", border: `1px solid ${color.borderInput}`, borderRadius: radius.xxl, boxShadow: shadow.card, padding: "10px 12px" }}>
          <input
            value={text}
            onChange={(e) => { setText(e.target.value); setSlashOpen(/^\/(\S*)$/.test(e.target.value)); }}
            onKeyDown={(e) => { if (e.key === "Enter") send(); }}
            placeholder="描述你的排障任务，输入 / 选择 Skill…"
            style={{ width: "100%", border: "none", outline: "none", fontSize: 14, padding: "4px 2px 8px", background: "transparent", color: color.ink }}
          />
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Interactive as="button" onClick={() => { setSlashOpen((v) => !v); setApprovalOpen(false); }}
              baseStyle={{ border: `1px solid ${color.border}`, background: "#fff", cursor: "pointer", color: color.textNav, fontSize: 12, fontWeight: 600, padding: "5px 10px", borderRadius: radius.md, display: "inline-flex", alignItems: "center", gap: 5 }}
              hoverStyle={{ background: color.brandTintBg, borderColor: color.brandTintBorder }}>
              <Icon name="slash" size={14} />Skill
            </Interactive>
            {/* 审批模式选择器（32 号）：32px 高、999px 圆角、蓝字 */}
            <Interactive as="button" onClick={() => { setApprovalOpen((v) => !v); setSlashOpen(false); }}
              baseStyle={{ height: 32, border: `1px solid ${color.brandTintBorder}`, background: "#fff", cursor: "pointer", color: color.brand, fontSize: 12, fontWeight: 600, padding: "0 12px", borderRadius: radius.pill, display: "inline-flex", alignItems: "center", gap: 5, whiteSpace: "nowrap" }}
              hoverStyle={{ background: color.brandTintBg }}>
              <Icon name="shield-check" size={14} color={color.brand} />{approvalLabel}<Icon name="chevron-down" size={13} color={color.brand} />
            </Interactive>
            <div style={{ flex: 1 }} />
            <Interactive as="button" onClick={send}
              baseStyle={{ border: "none", cursor: "pointer", background: color.brand, color: "#fff", width: 36, height: 36, borderRadius: radius.lg, display: "inline-flex", alignItems: "center", justifyContent: "center" }}
              hoverStyle={{ background: color.brandStrong }}>
              <Icon name="send" size={17} />
            </Interactive>
          </div>
        </div>
        <div style={{ textAlign: "center", fontSize: 11, color: color.textLabel, marginTop: 8 }}>AI 可能出错，请核对关键操作和生产风险。</div>
      </div>
    </div>
  );
}

function Menu({ left, width, title, children }: { left: number; width: number; title: string; children: React.ReactNode }) {
  return (
    <div style={{ position: "absolute", bottom: "calc(100% + 8px)", left, width, background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, boxShadow: shadow.popover, overflow: "hidden", zIndex: 20, animation: "omPop .14s ease" }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: color.textLabel, padding: "9px 13px 5px" }}>{title}</div>
      {children}
    </div>
  );
}
