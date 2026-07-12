import { color, radius } from "../theme/tokens";
import { toneColor } from "../theme/tokens";
import { Icon } from "../ui";
import type { ActivityGroup } from "../lib/api/types";

// E3：角色视觉映射（老项目 roleIcons 口径翻译；未知角色兜底 robot）
const ROLE_VISUALS: Record<string, string> = {
  inspect: "radar",
  diagnose: "stethoscope",
  recover: "first-aid-kit",
};
// 老项目 statusVisual 三态：running=琥珀 spin / done=teal / failed=红
const GROUP_STATUS = {
  running: { icon: "loader-2", colorHex: "#EF9F27", text: "运行中", spin: true },
  done: { icon: "circle-check", colorHex: "#1D9E75", text: "已汇报", spin: false },
  failed: { icon: "alert-triangle", colorHex: "#E24B4A", text: "异常/超时", spin: false },
} as const;

function GroupHeader({ g }: { g: ActivityGroup }) {
  if (!g.roleKey) {
    return <div style={{ fontSize: 10.5, fontWeight: 700, color: color.textLabel, letterSpacing: 0.4, marginBottom: 8 }}>{g.label}</div>;
  }
  const sv = g.status ? GROUP_STATUS[g.status] : null;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8, padding: "4px 8px", background: color.surface, border: `1px solid ${color.border}`, borderRadius: radius.sm }}>
      <Icon name={ROLE_VISUALS[g.roleKey] ?? "robot"} size={13} color={color.brand} />
      <span style={{ fontSize: 11, fontWeight: 700, color: color.textStrong, letterSpacing: 0.3, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{g.label}</span>
      {sv ? (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 10, fontWeight: 700, color: sv.colorHex }}>
          <Icon name={sv.icon} size={11} color={sv.colorHex} spin={sv.spin} />
          {sv.text}
        </span>
      ) : null}
    </div>
  );
}

/** 右侧活动时间线：工具 / 子 Agent 动作，进行中置顶。 */
export function ActivityRail({ groups }: { groups: ActivityGroup[] }) {
  return (
    <aside style={{ width: 320, flex: "0 0 320px", borderLeft: `1px solid ${color.border}`, background: color.surfaceAlt, display: "flex", flexDirection: "column" }}>
      <div style={{ flex: "0 0 auto", height: 44, display: "flex", alignItems: "center", gap: 8, padding: "0 16px", borderBottom: `1px solid ${color.border}` }}>
        <Icon name="timeline-event" size={16} color={color.brand} />
        <span style={{ fontSize: 13, fontWeight: 700 }}>活动 · 调查时间线</span>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "14px 16px" }}>
        {groups.map((g, gi) => (
          <div key={gi} style={{ marginBottom: 14 }}>
            <GroupHeader g={g} />
            {g.items.map((n, ni) => {
              const tc = toneColor[n.tone];
              const lastInGroup = ni === g.items.length - 1;
              return (
                <div key={n.id} style={{ display: "grid", gridTemplateColumns: "20px 1fr", gap: 9, paddingBottom: 12 }}>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
                    <div style={{ width: 20, height: 20, borderRadius: "50%", background: tc.dot, display: "flex", alignItems: "center", justifyContent: "center", flex: "0 0 20px" }}>
                      <Icon name={n.icon} size={11} color="#fff" spin={n.running} />
                    </div>
                    {!lastInGroup || gi < groups.length - 1 ? <div style={{ width: 2, flex: 1, marginTop: 4, background: color.border }} /> : null}
                  </div>
                  <div style={{ minWidth: 0, paddingBottom: 2 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span style={{ fontSize: 12.5, fontWeight: 600, color: color.textStrong }}>{n.title}</span>
                      {n.running ? <Icon name="loader-2" size={12} color={color.brand} spin /> : null}
                    </div>
                    {n.tool ? <div style={{ fontSize: 11, color: "#6b7280", fontFamily: "ui-monospace, monospace", marginTop: 2 }}>{n.tool}</div> : null}
                    <div style={{ fontSize: 11, color: color.textSubtle, marginTop: 2, lineHeight: 1.4 }}>{n.detail}</div>
                    <div style={{ fontSize: 10.5, color: color.textFaint, marginTop: 2 }}>{n.time}</div>
                  </div>
                </div>
              );
            })}
          </div>
        ))}
        <div style={{ fontSize: 11.5, color: color.brand, fontWeight: 600, cursor: "pointer", textAlign: "center", padding: 6 }}>显示更早 3 条</div>
      </div>
    </aside>
  );
}
