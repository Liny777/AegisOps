import { Suspense } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useApp } from "../lib/appState";
import { color, radius } from "../theme/tokens";
import { Icon, Interactive } from "../ui";
import { AlertRulesPane } from "../alerts/entry";  // 懒组件（entry 只 lazy，不拖重代码）
import { MyModelsPane } from "./MyModelsPane";

/** 设置页（/settings/:section?）：二级菜单骨架，侧栏底部「设置」入口直达。
 * 菜单两项（2026-08-07 对齐原型；2026-08-10 OModel 迁出为侧栏一级页 /omodel）：
 * - 告警接管配置（默认落点）：AlertRulesPane，作用于当前 Agent（跟随侧栏选择器）。
 *   原挂在插件页 ?tab=alerts，用户拍板迁入设置；旧深链由 SettingsPage 重定向兼容。
 * - 我的模型（/settings/models）：自带模型的看/改/删（创建仍在初始化向导的高级选项里）。
 * - 通知配置：占位锁定（V1 未开放，对齐「知识/自动化」的置灰风格）。
 * 旧链接 /settings（无 section，原 OModel 落点）一律落告警接管配置。 */
export function SettingsHome() {
  const nav = useNavigate();
  const { section } = useParams();
  const { agents, currentAgentId, alertGranted } = useApp();
  const currentAgent = agents.find((a) => a.instance_id === currentAgentId);
  const onModels = section === "models";

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
        {/* 二级菜单：告警接管配置（常驻选中）/ 通知配置（占位锁定） */}
        <div style={{ flex: "0 0 250px", borderRight: `1px solid ${color.border}`, background: "#fff", padding: "14px 10px", display: "flex", flexDirection: "column", gap: 2 }}>
          {alertGranted ? (
            <Interactive as="div" onClick={onModels ? () => nav("/settings") : undefined}
              baseStyle={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", borderRadius: radius.lg, cursor: onModels ? "pointer" : "default", fontSize: 13.5, fontWeight: onModels ? 500 : 600, color: onModels ? color.textNav : color.brand, background: onModels ? "transparent" : color.brandTintBg }}
              hoverStyle={onModels ? { background: color.pageBg } : {}}>
              <Icon name="shield-bolt" size={18} />
              <span style={{ flex: 1 }}>告警接管配置</span>
            </Interactive>
          ) : (
            /* 白名单未开通：锁定置灰（样式同「通知配置」占位；主区由 AlertRulesPane 空态兜底） */
            <div title="未开通：算力资源有限按白名单开放，请联系平台管理员" style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", borderRadius: radius.lg, cursor: "not-allowed", fontSize: 13.5, fontWeight: 500, color: color.textFaint }}>
              <Icon name="shield-bolt" size={18} />
              <span style={{ flex: 1 }}>告警接管配置</span>
              <Icon name="lock" size={13} />
            </div>
          )}
          <Interactive as="div" onClick={onModels ? undefined : () => nav("/settings/models")}
            baseStyle={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", borderRadius: radius.lg, cursor: onModels ? "default" : "pointer", fontSize: 13.5, fontWeight: onModels ? 600 : 500, color: onModels ? color.brand : color.textNav, background: onModels ? color.brandTintBg : "transparent" }}
            hoverStyle={onModels ? {} : { background: color.pageBg }}>
            <Icon name="sparkles" size={18} />
            <span style={{ flex: 1 }}>我的模型</span>
          </Interactive>
          <div title="V1 未开放" style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 11px", borderRadius: radius.lg, cursor: "not-allowed", fontSize: 13.5, fontWeight: 500, color: color.textFaint }}>
            <Icon name="bell" size={18} />
            <span style={{ flex: 1 }}>通知配置</span>
            <Icon name="lock" size={13} />
          </div>
        </div>

        {/* 主区：/settings/models → 我的模型；其余（含无 section 的旧链接）→ 告警接管配置 */}
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          {onModels ? <MyModelsPane /> : currentAgent ? (
            <>
              <div style={{ flex: "0 0 auto", display: "flex", alignItems: "center", gap: 8, padding: "10px 16px", borderBottom: `1px solid ${color.border}`, background: "#fff" }}>
                <span title="规则作用于当前 Agent；在侧栏切换 Agent 即切换配置对象" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color: color.textNav, background: color.neutralBg, border: `1px solid ${color.border}`, padding: "4px 10px", borderRadius: radius.pill }}>
                  <Icon name="robot" size={14} color={color.brand} />{currentAgent.name}
                </span>
                <span style={{ fontSize: 11.5, color: color.textSubtle }}>命中策略的告警将由该 Agent 自动接管诊断</span>
              </div>
              <Suspense fallback={null}>
                <AlertRulesPane instanceId={currentAgentId} />
              </Suspense>
            </>
          ) : (
            <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <div style={{ textAlign: "center", maxWidth: 460, padding: 24 }}>
                <div style={{ width: 56, height: 56, borderRadius: 16, background: color.pageBg, display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 14 }}>
                  <Icon name="robot" size={28} color={color.textFaint} />
                </div>
                <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6 }}>还没有 Agent</div>
                <div style={{ fontSize: 12.5, color: color.textMuted, lineHeight: 1.7 }}>先在初始化向导创建 Agent，再回到这里配置它的告警接管策略。</div>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
