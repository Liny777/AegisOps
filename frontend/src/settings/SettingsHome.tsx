import { useNavigate } from "react-router-dom";
import { color, radius } from "../theme/tokens";
import { Icon, Interactive, Pill } from "../ui";

/** 设置页（/settings）：二级菜单骨架。原「Agent 配置」内容已迁到侧栏「插件」；
 * 此处承载与单 Agent 无关的用户级配置，首个二级菜单「OModel」V1 先禁用占位
 * （oModel 连接/数据范围当前由平台统一配置，个人配置面后续版本开放）。 */
export function SettingsHome() {
  const nav = useNavigate();

  return (
    <>
      <header style={{ flex: "0 0 auto", height: 56, borderBottom: `1px solid ${color.border}`, background: "#fff", display: "flex", alignItems: "center", padding: "0 24px", gap: 12 }}>
        <Interactive as="button" onClick={() => nav(-1)}
          baseStyle={{ border: `1px solid ${color.border}`, background: "#fff", cursor: "pointer", width: 32, height: 32, borderRadius: radius.md, display: "inline-flex", alignItems: "center", justifyContent: "center", color: "#697283" }}
          hoverStyle={{ background: color.pageBg }}>
          <Icon name="arrow-left" size={17} />
        </Interactive>
        <div style={{ fontSize: 15, fontWeight: 700 }}>设置</div>
        <span style={{ fontSize: 12, color: color.textSubtle }}>用户级配置</span>
      </header>

      <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
        {/* 二级菜单 */}
        <div style={{ flex: "0 0 250px", borderRight: `1px solid ${color.border}`, background: "#fff", padding: "14px 10px", display: "flex", flexDirection: "column", gap: 2 }}>
          <div
            title="即将上线"
            style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", borderRadius: radius.lg, cursor: "not-allowed", fontSize: 13.5, fontWeight: 500, color: color.textFaint }}
          >
            <Icon name="topology-star-3" size={18} />
            <span style={{ flex: 1 }}>OModel</span>
            <Icon name="lock" size={13} color={color.textFainter} />
          </div>
        </div>

        {/* 空态：唯一二级项禁用，右侧给预告 */}
        <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div style={{ textAlign: "center", maxWidth: 420, padding: 24 }}>
            <div style={{ width: 56, height: 56, borderRadius: 16, background: color.pageBg, display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 14 }}>
              <Icon name="topology-star-3" size={28} color={color.textFaint} />
            </div>
            <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6, display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}>
              OModel 配置 <Pill tone="neutral">即将上线</Pill>
            </div>
            <div style={{ fontSize: 12.5, color: color.textMuted, lineHeight: 1.7 }}>
              按用户配置 oModel 连接与数据范围。当前版本由平台统一配置，无需手动设置。
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
