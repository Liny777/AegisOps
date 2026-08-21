// 接管结果卡（原型图3）：四档终态回显。级别一律展示规则**全集**（merged 尤其要
// before→after 对照，防用户误以为规则只剩新并入的级别）。
import { color, radius } from "../../theme/tokens";
import { SEVERITY_LABEL, SEVERITY_ORDER } from "../../alerts/constants";
import type { AlertSeverity } from "../../alerts/types";
import type { TakeoverVm } from "./useAlertTakeover";
import "./AlertTakeover.css";

const sevText = (list: readonly AlertSeverity[]): string =>
  SEVERITY_ORDER.filter((s) => list.includes(s)).map((s) => SEVERITY_LABEL[s]).join("、");

const TAIL = "后续当匹配的告警发生时，Agent 将自动接管进行 5 步诊断分析。";

function CardShell({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="oa-takeover-result"
      data-testid="alert-takeover-result"
      style={{ border: `1px solid ${color.border}`, borderRadius: radius.xl, background: "#fff", boxShadow: "0 1px 3px rgba(20,24,31,.05)", animation: "omPop .25s ease" }}
    >
      {children}
    </div>
  );
}

export function TakeoverResultCard({ vm }: { vm: TakeoverVm }) {
  if (vm.phase === "done_not_granted") {
    return (
      <CardShell>
        {/* 文案对齐 AlertRulesPane 未开通空态口径 */}
        <div style={{ fontSize: 13, color: color.textBody, lineHeight: 1.7 }}>
          告警接管功能未开通：算力资源有限按管理员白名单开放，请联系平台管理员开通。
        </div>
      </CardShell>
    );
  }

  const result = vm.result;
  if (!result) return null;
  const rule = result.rule;
  const rowLabel: React.CSSProperties = { fontSize: 12, color: color.textSubtle, whiteSpace: "nowrap" };
  const rowValue: React.CSSProperties = { fontSize: 12.5, color: color.textStrong, fontWeight: 500, minWidth: 0, overflowWrap: "anywhere" };

  if (vm.phase === "done_created") {
    // 提示词是否被改过：与默认 prompt 比对（defaults 与 ctx 同生命周期，done 态仍在）
    const customized = vm.defaults ? rule.prompt.trim() !== vm.defaults.prompt.trim() : true;
    return (
      <CardShell>
        <div style={{ fontSize: 13.5, fontWeight: 700, color: color.textStrong }}>✅ 告警接管策略已创建成功!</div>
        <div className="oa-takeover-result-rows">
          <span style={rowLabel}>策略名称</span>
          <span style={rowValue}>
            {rule.name}
            {result.renamed ? (
              <span style={{ marginLeft: 6, fontSize: 11.5, color: color.textSubtle, fontWeight: 400 }}>已自动改名（原名被占用）</span>
            ) : null}
          </span>
          <span style={rowLabel}>告警类型</span>
          <span style={rowValue}>{rule.categories.join("、")}</span>
          <span style={rowLabel}>告警级别</span>
          <span style={rowValue}>{sevText(rule.severities)}</span>
          <span style={rowLabel}>提示词</span>
          <span style={rowValue}>{customized ? "已自定义" : "默认五步诊断"}</span>
        </div>
        <div style={{ fontSize: 12, color: color.textSubtle }}>{TAIL}</div>
      </CardShell>
    );
  }

  if (vm.phase === "done_merged") {
    const detail = result.merge_detail;
    return (
      <CardShell>
        <div style={{ fontSize: 13.5, fontWeight: 700, color: color.textStrong }}>✅ 已并入现有规则「{rule.name}」</div>
        <div style={{ fontSize: 12.5, color: color.textBody }}>
          告警级别：{detail
            ? `${sevText(detail.severities_before)} → ${sevText(detail.severities_after)}`
            : sevText(rule.severities)}
          （并入后规则覆盖的全部级别）
        </div>
        <div style={{ fontSize: 12, color: color.textSubtle }}>{TAIL}</div>
      </CardShell>
    );
  }

  // done_already
  return (
    <CardShell>
      <div style={{ fontSize: 13, fontWeight: 600, color: color.textStrong }}>
        已有规则「{rule.name}」覆盖该类型与级别，无需重复创建。
      </div>
      {result.prompt_ignored ? (
        <div style={{ fontSize: 12, color: color.textSubtle }}>如需自定义提示词，请到 设置→告警接管 编辑该规则。</div>
      ) : null}
    </CardShell>
  );
}
