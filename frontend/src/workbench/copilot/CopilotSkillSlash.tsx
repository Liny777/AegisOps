// 「/」技能菜单（Part B re-home，参考 openOps-Dev strategy-a 的 DOM 叠加方案）。
//
// 不替换 CopilotChat 的 composer：监听其 textarea，输入以「/」开头时浮出技能菜单，
// 选中把 "/<skill_key> " 写回（原生 setter + input 事件让受控状态更新）。
// 技能来自与执行门禁同源的装配集 api.getAvailableSkills（skill_key 即调用名）。
import { useEffect, useRef, useState } from "react";
import { color, radius, shadow } from "../../theme/tokens";
import { api } from "../../lib/api";
import type { Skill } from "../../lib/api/types";

const NATIVE_SETTER = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;

function composerTextarea(): HTMLTextAreaElement | null {
  return document.querySelector<HTMLTextAreaElement>(".copilot-chat-panel textarea");
}

export function CopilotSkillSlash({ instanceId }: { instanceId: string }) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [anchor, setAnchor] = useState<{ left: number; bottom: number; width: number } | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    let dead = false;
    if (!instanceId) return;
    api.getAvailableSkills(instanceId)
      .then((list) => { if (!dead) setSkills(list); })
      // 不再静默吞错（内网实测坑：403 非属主/未同步 → 列表空 → 「/」死寂无从定位）——留控制台线索
      .catch((e) => console.warn("[openops] available-skills 加载失败（「/」菜单将显示空态提示）:", e));
    return () => { dead = true; };
  }, [instanceId]);

  useEffect(() => {
    // 监听不再以 skills 非空为前提：列表空/加载失败时「/」也要有可见反馈（空态提示），而非毫无反应
    let raf = 0;
    const onInput = () => {
      const ta = taRef.current;
      if (!ta) return;
      const v = ta.value;
      // 「、」= 中文输入法下的 / 键（内网常见：IME 开着按 / 出「、」→ 菜单永不触发）
      if (v.startsWith("/") || v.startsWith("、")) {
        setQuery(v.slice(1).trim().toLowerCase());
        const r = ta.getBoundingClientRect();
        setAnchor({ left: r.left, bottom: window.innerHeight - r.top + 6, width: r.width });
        setOpen(true);
      } else {
        setOpen(false);
      }
    };
    const attach = () => {
      const ta = composerTextarea();
      if (ta && ta !== taRef.current) {
        taRef.current?.removeEventListener("input", onInput);
        taRef.current = ta;
        ta.addEventListener("input", onInput);
      }
      raf = requestAnimationFrame(attach);  // CopilotChat 重挂 textarea 时重绑
    };
    raf = requestAnimationFrame(attach);
    return () => {
      cancelAnimationFrame(raf);
      taRef.current?.removeEventListener("input", onInput);
      // 必须清空：否则 effect 重跑时 attach 的 `ta !== taRef.current` 恒 false → 监听永不重挂
      //（skills.length 依赖时代的隐性坑——加载完成触发 cleanup+重跑即失聪）
      taRef.current = null;
    };
    // onInput 只用 setState 不读 skills：挂载一次即可，与 skills 加载时序解耦
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!open || !anchor) return null;
  const filtered = skills.filter((s) => !query || s.name.toLowerCase().includes(query));
  // 列表本身为空（未装配/加载失败/403 非属主）：给可见空态而非无反应——用户至少知道菜单在、数据不在
  if (!skills.length) {
    return (
      <div style={{ position: "fixed", left: anchor.left, bottom: anchor.bottom, width: Math.min(anchor.width, 420), background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, boxShadow: shadow.menu, zIndex: 60, padding: "10px 12px", fontSize: 12, color: color.textSubtle }}>
        暂无可用 Skill——可能未同步/未装配，或你不是该 Agent 的属主（控制台有具体原因）。
      </div>
    );
  }
  if (!filtered.length) return null;

  const pick = (s: Skill) => {
    const ta = taRef.current;
    if (!ta || !NATIVE_SETTER) return;
    // s.name 已含前导 "/"（getAvailableSkills/mock 约定，同 workbench Composer）——不得再加，
    // 否则写成 "//skill"，模型只认 "/<skill>" 开头 → 不会触发 run_platform_skill（skill 不执行）
    NATIVE_SETTER.call(ta, `${s.name} `);
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    ta.focus();
    setOpen(false);
  };

  return (
    <div style={{ position: "fixed", left: anchor.left, bottom: anchor.bottom, width: Math.min(anchor.width, 420), background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, boxShadow: shadow.menu, zIndex: 60, overflow: "hidden" }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: color.textLabel, padding: "8px 12px 4px" }}>选择 Skill</div>
      {filtered.slice(0, 8).map((s) => (
        <div key={s.skill_id} onMouseDown={(e) => { e.preventDefault(); pick(s); }}
          style={{ padding: "8px 12px", cursor: "pointer", display: "flex", gap: 8, alignItems: "baseline" }}
          onMouseEnter={(e) => ((e.currentTarget.style.background = "#f5f8ff"))}
          onMouseLeave={(e) => ((e.currentTarget.style.background = "transparent"))}>
          <span style={{ fontFamily: "ui-monospace, monospace", fontSize: 12.5, fontWeight: 650, color: color.brand }}>{s.name}</span>
          <span style={{ fontSize: 12, color: color.textMuted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{s.desc}</span>
        </div>
      ))}
    </div>
  );
}
