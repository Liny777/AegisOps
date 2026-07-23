import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";

// 全局 reset（box-sizing: border-box 等）必须与真实应用一致：时间线步卡 head 是
// width:100% + padding，content-box 下会假性溢出 24px（fixture 伪阳性）。
import "../../src/theme/global.css";
import { DiagnosisTimeline } from "../../src/workbench/DiagnosisTimeline";
import type { RcaCardData } from "../../src/lib/api/types";
import type { RecoveryState } from "../../src/lib/rca/recovery";

// 敌意长 token（复刻 init-overflow 惯例）：无空格 Latin 长串 / 超长 URL / 带标签指标串 /
// 超长工具名——模拟模型上报的最坏字段。加固后应：header 标题走省略号截断，其余叶子文本
// 就地折行（overflow-wrap:anywhere），任何元素都不越出所在列容器。
const longRun = "X".repeat(200);
const longUrl = `https://grafana.internal.example.invalid/d/${"a".repeat(160)}?orgId=1&var-cluster=prod-payment-core`;
const longMetric = `redis_connected_clients{instance="10.23.40.11:6379",pod="${"payment-core-".repeat(18)}svc-a"}`;
const longTool = `mcp.k8s.rollout_restart_deployment_${"payment_core_".repeat(12)}v2`;

/** 进行中板（步 4 active）：展开态覆盖 currentQ/why、facts/unknowns、证据源 badge、假设行。 */
const hostileInProgress: RcaCardData = {
  title: `支付延迟突增-${longRun}`,
  phaseLabel: "诊断中",
  time: "10:11",
  revision: 4,
  status: "in_progress",
  tiles: [
    { label: "症状", value: `下单 P99 180ms→1.4s ${longMetric}` },
    { label: "时间窗", value: "10:02 起 · 持续 9min" },
    { label: "影响面", value: `APP-A 支付下单 ${longRun}` },
    { label: "当前阶段", value: "诊断 · 验证 H1" },
  ],
  steps: [
    { num: 1, label: "范围", state: "done", summary: `范围锁定支付核心域，参考 ${longUrl}` },
    { num: 2, label: "证据", state: "done", summary: `指标确认连接打满 ${longMetric}` },
    { num: 3, label: "假设", state: "done", summary: `生成三个假设 ${longRun}` },
    { num: 4, label: "验证", state: "active", summary: `正在核对连接持有时长 ${longRun}` },
    { num: 5, label: "结论", state: "waiting" },
  ],
  currentQ: `Redis 连接饱和是慢查询导致，还是连接泄漏导致？${longRun}`,
  why: `两者的恢复动作不同，详见 ${longUrl}`,
  facts: [
    { text: `svc-payment-api → Redis active 连接打满 ${longMetric}` },
    { text: "同期无发布、无配置变更" },
  ],
  unknowns: [{ text: `连接是否泄漏，观测面板 ${longUrl}` }],
  sources: [
    { name: `Prometheus-${longRun}`, status: "done", tone: "good" },
    { name: "Loki", status: "running", tone: "warning" },
  ],
  hypotheses: [
    { text: `H1 Redis 连接泄漏（svc-a 未释放）${longRun}`, tag: "支持", tagTone: "good", conf: 0.72 },
    { text: "H2 下游慢查询占用连接", tag: "部分支持", tagTone: "warning", conf: 0.41 },
  ],
  actions: [
    { tier: "立即", text: `重启 ${longTool} 释放连接`, confirm: true, impact: "3 实例", status: "待确认", statusTone: "warning" },
  ],
  conclusion: "",
};

/** 闭环板（步 5 自动展开）：覆盖最终结论虚线框与「下一步行动」ActionRows 的长 token。 */
const hostileConcluded: RcaCardData = {
  ...hostileInProgress,
  phaseLabel: "诊断完成",
  revision: 5,
  status: "concluded",
  steps: hostileInProgress.steps.map((step) => ({
    ...step,
    state: "done" as const,
    summary: step.summary ?? `根因确认 ${longRun}`,
  })),
  conclusion: `根因 H1（连接泄漏）已确认：${longRun}，报告见 ${longUrl}`,
  actions: [
    { tier: "立即", text: `已重启 ${longTool}`, impact: "3 实例", status: "已执行", statusTone: "good" },
    { tier: "长期", text: "补充连接池饱和告警", impact: "预案", status: "建议", statusTone: "neutral" },
  ],
};

/** 换板后的稀疏新板（revision 从 1 重计 → 触发过渡提示 + overrides 重置）。 */
const sparseNewBoard: RcaCardData = {
  title: "新线索:对账超时",
  phaseLabel: "诊断中",
  time: "10:20",
  revision: 1,
  status: "in_progress",
  tiles: [],
  steps: [
    { num: 1, label: "范围", state: "done", summary: "新一轮范围锁定对账域" },
    { num: 2, label: "证据", state: "active" },
    { num: 3, label: "假设", state: "waiting" },
    { num: 4, label: "验证", state: "waiting" },
    { num: 5, label: "结论", state: "waiting" },
  ],
  currentQ: "",
  why: "",
  facts: [],
  unknowns: [],
  sources: [],
  hypotheses: [],
  actions: [],
  conclusion: "",
};

/** 长工具名恢复状态：待审批 + 已执行两条，覆盖第六节点收起摘要与展开明细的溢出面。 */
const hostileRecovery: RecoveryState = {
  phase: "pending",
  actions: [
    {
      approvalRequestId: "appr-long-1",
      tool: longTool,
      label: `恢复动作待批准：重启 ${longRun}`,
      phase: "pending",
      requiredAt: "2026-07-23T02:00:00Z",
      updatedAt: "2026-07-23T02:00:00Z",
    },
    {
      approvalRequestId: "appr-long-2",
      tool: longTool,
      phase: "executed",
      updatedAt: "2026-07-23T02:03:00Z",
    },
  ],
  counts: { total: 2, pending: 1, approved: 0, executing: 0, executed: 1, rejected: 0, timeout: 0, failed: 0 },
};

/** 三列复刻活动面板容器（container-type: inline-size，容器查询按列宽降列）：
 *  420=面板下限 / 520=容器查询断点上邻 / 760=面板上限。三列共享同一份板状态，
 *  「切换新板」一次驱动全部列（幕13 断言取 first）。 */
function Fixture() {
  const [board, setBoard] = useState<RcaCardData>(hostileInProgress);
  return (
    <main style={{ boxSizing: "border-box", minHeight: "100vh", padding: 24, background: "#f7f8fa" }}>
      <button type="button" data-testid="switch-board" onClick={() => setBoard(sparseNewBoard)}>
        切换新板
      </button>
      {/* 纵向堆叠三列：页面自身不得出现横向滚动（与 init-overflow 的整页断言口径一致） */}
      <div style={{ display: "flex", flexDirection: "column", gap: 32, marginTop: 16 }}>
        {[420, 520, 760].map((width) => (
          <section
            key={width}
            data-testid={`fixture-col-${width}`}
            style={{ width, containerType: "inline-size", display: "flex", flexDirection: "column", gap: 24 }}
          >
            <DiagnosisTimeline rca={board} live recovery={hostileRecovery} />
            <DiagnosisTimeline rca={hostileConcluded} recovery={hostileRecovery} />
          </section>
        ))}
      </div>
    </main>
  );
}

document.body.style.margin = "0";
document.body.style.background = "#f7f8fa";
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Fixture />
  </StrictMode>,
);
