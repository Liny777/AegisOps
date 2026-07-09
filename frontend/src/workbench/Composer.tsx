import { useMemo, useState } from "react";
import { color, radius, shadow } from "../theme/tokens";
import { Icon, Interactive } from "../ui";
import type { ModelOption, Skill } from "../lib/api/types";

/** SkillAwareComposer：/ 选 Skill + 会话级模型切换 + 发送。@ 提及 V1 不做。 */
export function Composer({
  skills,
  models,
  currentModel,
  onSend,
  onSelectModel,
}: {
  skills: Skill[];
  models: ModelOption[];
  currentModel: string;
  onSend?: (text: string) => void;
  onSelectModel?: (m: ModelOption) => void;
}) {
  const [text, setText] = useState("");
  const [slashOpen, setSlashOpen] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [model, setModel] = useState(currentModel);

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

        {/* model menu */}
        {modelOpen ? (
          <Menu left={96} width={280} title="选择模型（本次会话临时）">
            {models.map((mo) => (
              <Interactive
                key={mo.llm_config_id}
                onClick={() => {
                  if (!mo.available) return;
                  setModel(mo.label.replace(/（.*/, ""));
                  onSelectModel?.(mo);
                  setModelOpen(false);
                }}
                baseStyle={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 13px", cursor: mo.available ? "pointer" : "not-allowed", opacity: mo.available ? 1 : 0.55 }}
                hoverStyle={mo.available ? { background: "#f5f8ff" } : {}}
              >
                <Icon name="cpu" size={15} color={mo.available ? color.brand : color.textFaint} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: mo.available ? color.textStrong : color.textFaint }}>{mo.label}</div>
                  <div style={{ fontSize: 11, color: color.textSubtle }}>{mo.reason ?? mo.note}</div>
                </div>
                {mo.current ? <Icon name="check" size={15} color={color.brand} /> : null}
              </Interactive>
            ))}
          </Menu>
        ) : null}

        <div style={{ background: "#fff", border: `1px solid ${color.borderInput}`, borderRadius: radius.xxl, boxShadow: shadow.card, padding: "10px 12px" }}>
          <input
            value={text}
            onChange={(e) => { setText(e.target.value); setSlashOpen(/^\/(\S*)$/.test(e.target.value)); }}
            onKeyDown={(e) => { if (e.key === "Enter") send(); }}
            placeholder="描述你的排障任务，输入 / 选择 Skill…（原型演示）"
            style={{ width: "100%", border: "none", outline: "none", fontSize: 14, padding: "4px 2px 8px", background: "transparent", color: color.ink }}
          />
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Interactive as="button" onClick={() => { setSlashOpen((v) => !v); setModelOpen(false); }}
              baseStyle={{ border: `1px solid ${color.border}`, background: "#fff", cursor: "pointer", color: color.textNav, fontSize: 12, fontWeight: 600, padding: "5px 10px", borderRadius: radius.md, display: "inline-flex", alignItems: "center", gap: 5 }}
              hoverStyle={{ background: color.brandTintBg, borderColor: color.brandTintBorder }}>
              <Icon name="slash" size={14} />Skill
            </Interactive>
            <Interactive as="button" onClick={() => { setModelOpen((v) => !v); setSlashOpen(false); }}
              baseStyle={{ border: `1px solid ${color.border}`, background: "#fff", cursor: "pointer", color: color.textNav, fontSize: 12, fontWeight: 600, padding: "5px 10px", borderRadius: radius.md, display: "inline-flex", alignItems: "center", gap: 5, whiteSpace: "nowrap" }}
              hoverStyle={{ background: color.brandTintBg, borderColor: color.brandTintBorder }}>
              <Icon name="cpu" size={14} />模型：{model}<Icon name="chevron-down" size={13} />
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
