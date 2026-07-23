import type { CSSProperties } from "react";
import { color, font } from "../../theme/tokens";

/** 品牌渐变的两个 stop（SVG defs 用不了 tokens 的 brandGrad CSS 字符串）：
 *  浅端与 tokens.brandGrad 的 #4f9dff 保持同源，深端直接取 color.brand。改品牌色时同步这里的浅端。 */
const GRAD_FROM = "#4f9dff";
const GRAD_TO = color.brand;

/* ------------------------------------------------------------------ */
/* 多 Agent 编排图：主 Agent 居中（品牌渐变），虚线放射到 9 个专业子 Agent */
/* ------------------------------------------------------------------ */

interface Node {
  x: number;
  y: number;
  label: string;
}

const CX = 360;
const CY = 212;

const subNodes: Node[] = [
  { x: 128, y: 62, label: "变更" },
  { x: 283, y: 46, label: "日志" },
  { x: 438, y: 46, label: "指标" },
  { x: 593, y: 62, label: "告警" },
  { x: 64, y: 212, label: "APM" },
  { x: 620, y: 212, label: "Grafana 看板" },
  { x: 168, y: 362, label: "数据库" },
  { x: 360, y: 380, label: "Redis" },
  { x: 552, y: 362, label: "MQS" },
];

export function OrchestrationDiagram({ style }: { style?: CSSProperties }) {
  return (
    <svg
      viewBox="0 0 720 424"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="主 Agent 派发子 Agent 并行调查的编排示意图"
      style={{ width: "100%", height: "auto", display: "block", ...style }}
    >
      <defs>
        <linearGradient id="introBrandGrad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={GRAD_FROM} />
          <stop offset="1" stopColor={GRAD_TO} />
        </linearGradient>
        <radialGradient id="introHalo" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor={color.brand} stopOpacity="0.10" />
          <stop offset="1" stopColor={color.brand} stopOpacity="0" />
        </radialGradient>
      </defs>

      {/* 主节点光晕 */}
      <circle cx={CX} cy={CY} r={150} fill="url(#introHalo)" />

      {/* 放射虚线 */}
      {subNodes.map((n) => (
        <line
          key={`l-${n.label}`}
          x1={CX}
          y1={CY}
          x2={n.x}
          y2={n.y}
          stroke={color.brandTintBorder}
          strokeWidth={1.6}
          strokeDasharray="4 5"
        />
      ))}

      {/* 子 Agent 节点（白底胶囊） */}
      {subNodes.map((n) => {
        const w = Math.max(76, n.label.length * 13 + 40);
        return (
          <g key={n.label}>
            <rect
              x={n.x - w / 2}
              y={n.y - 21}
              width={w}
              height={42}
              rx={12}
              fill="#fff"
              stroke={color.border}
              strokeWidth={1.2}
            />
            <circle cx={n.x - w / 2 + 18} cy={n.y} r={3.5} fill={color.brand} />
            <text
              x={n.x - w / 2 + 30}
              y={n.y + 4.5}
              fontFamily={font.sans}
              fontSize={13.5}
              fontWeight={600}
              fill={color.textStrong}
            >
              {n.label}
            </text>
          </g>
        );
      })}

      {/* 主 Agent 节点（品牌渐变） */}
      <g>
        <rect x={CX - 88} y={CY - 40} width={176} height={80} rx={16} fill="url(#introBrandGrad)" />
        <text x={CX} y={CY - 6} textAnchor="middle" fontFamily={font.sans} fontSize={17} fontWeight={800} fill="#fff">
          主 Agent
        </text>
        <text
          x={CX}
          y={CY + 18}
          textAnchor="middle"
          fontFamily={font.sans}
          fontSize={11.5}
          fontWeight={500}
          fill="rgba(255,255,255,.85)"
        >
          理解意图 · 派发调查 · 汇总结论
        </text>
      </g>
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* 定界五步法：横向 stepper（范围 → 证据 → 假设 → 验证 → 结论）           */
/* ------------------------------------------------------------------ */

const stepLabels = ["范围", "证据", "假设", "验证", "结论"];

export function FiveStepFlow({ style }: { style?: CSSProperties }) {
  const W = 760;
  const pillW = 116;
  const pillH = 56;
  const gap = (W - pillW * 5) / 4;
  return (
    <svg
      viewBox={`0 0 ${W} 92`}
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="定界五步法：范围、证据、假设、验证、结论"
      style={{ width: "100%", height: "auto", display: "block", ...style }}
    >
      <defs>
        <linearGradient id="introStepGrad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={GRAD_FROM} />
          <stop offset="1" stopColor={GRAD_TO} />
        </linearGradient>
      </defs>
      {stepLabels.map((label, i) => {
        const x = i * (pillW + gap);
        const y = 18;
        const last = i === stepLabels.length - 1;
        return (
          <g key={label}>
            <rect
              x={x}
              y={y}
              width={pillW}
              height={pillH}
              rx={14}
              fill={last ? "url(#introStepGrad)" : "#fff"}
              stroke={last ? "none" : color.border}
              strokeWidth={1.2}
            />
            <circle cx={x + 26} cy={y + pillH / 2} r={11} fill={last ? "rgba(255,255,255,.22)" : color.brandTintBg} />
            <text
              x={x + 26}
              y={y + pillH / 2 + 4}
              textAnchor="middle"
              fontFamily={font.sans}
              fontSize={11.5}
              fontWeight={800}
              fill={last ? "#fff" : color.brand}
            >
              {i + 1}
            </text>
            <text
              x={x + 46}
              y={y + pillH / 2 + 5}
              fontFamily={font.sans}
              fontSize={14.5}
              fontWeight={700}
              fill={last ? "#fff" : color.textStrong}
            >
              {label}
            </text>
            {!last ? (
              <path
                d={`M ${x + pillW + 8} ${y + pillH / 2} L ${x + pillW + gap - 8} ${y + pillH / 2}`}
                stroke={color.brandTintBorder}
                strokeWidth={1.8}
                markerEnd="none"
              />
            ) : null}
            {!last ? (
              <path
                d={`M ${x + pillW + gap - 14} ${y + pillH / 2 - 4.5} L ${x + pillW + gap - 8} ${y + pillH / 2} L ${
                  x + pillW + gap - 14
                } ${y + pillH / 2 + 4.5}`}
                fill="none"
                stroke={color.brandTintBorder}
                strokeWidth={1.8}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}
