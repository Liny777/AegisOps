import { color, radius } from "../theme/tokens";
import { toneColor } from "../theme/tokens";
import { Icon } from "../ui";
import type { ActivityGroup } from "../lib/api/types";

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
            <div style={{ fontSize: 10.5, fontWeight: 700, color: color.textLabel, letterSpacing: 0.4, marginBottom: 8 }}>{g.label}</div>
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
