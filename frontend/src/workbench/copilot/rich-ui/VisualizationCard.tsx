import { Icon } from "../../../ui/Icon";
import { color, font, radius } from "../../../theme/tokens";
import {
  CHART_HEIGHT,
  CHART_WIDTH,
  PLOT,
  barRects,
  linePath,
  linePoints,
  numericRange,
  pieSlices,
  yForValue,
} from "./geometry";
import type { CartesianVisualizationSpec, PieVisualizationSpec, VisualizationSpec } from "./schema";
import "./VisualizationCard.css";

// 与契约允许的 12 个饼图扇区一一对应，避免后半段重复颜色造成图例歧义。
const PALETTE = [
  "#1683ff", "#1fa463", "#c18a20", "#8b5cf6", "#e05d44", "#1f9dac",
  "#4f67d8", "#78a92d", "#e47b28", "#c6579a", "#2a9fc4", "#667085",
] as const;
const KIND_LABEL = { line: "折线图", bar: "柱状图", pie: "饼图" } as const;

function formatValue(value: number, unit?: string): string {
  const abs = Math.abs(value);
  const formatted = new Intl.NumberFormat("zh-CN", {
    notation: abs >= 1_000_000 ? "compact" : "standard",
    maximumFractionDigits: abs < 10 ? 2 : abs < 100 ? 1 : 0,
  }).format(value);
  return unit ? `${formatted} ${unit}` : formatted;
}

function cartesianLabels(spec: CartesianVisualizationSpec): string[] {
  return spec.series[0].data.map((point) => point.label);
}

function CartesianChart({ spec }: { spec: CartesianVisualizationSpec }) {
  const labels = cartesianLabels(spec);
  const values = spec.series.flatMap((series) => series.data.map((point) => point.value));
  const range = numericRange(values, spec.chart_type === "bar");
  const ticks = Array.from({ length: 5 }, (_, index) => {
    const value = range.max - ((range.max - range.min) * index) / 4;
    return { value, y: yForValue(value, range) };
  });
  const labelStep = Math.max(1, Math.ceil(labels.length / 8));
  const plotWidth = CHART_WIDTH - PLOT.left - PLOT.right;
  const visibleLabel = (index: number) => index === 0 || index === labels.length - 1 || index % labelStep === 0;
  const lines = spec.chart_type === "line" ? linePoints(spec) : [];
  const bars = spec.chart_type === "bar" ? barRects(spec) : null;

  return (
    <div className="ocv-chart-scroll">
      <svg
        className="ocv-cartesian-svg"
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        role="img"
        aria-label={`${spec.title}，${KIND_LABEL[spec.chart_type]}`}
      >
        <title>{spec.title}</title>
        {spec.description ? <desc>{spec.description}</desc> : null}
        {ticks.map((tick, index) => (
          <g key={index}>
            <line
              x1={PLOT.left}
              x2={CHART_WIDTH - PLOT.right}
              y1={tick.y}
              y2={tick.y}
              stroke="#edf0f4"
              strokeWidth="1"
            />
            <text x={PLOT.left - 8} y={tick.y + 4} textAnchor="end" className="ocv-axis-text">
              {formatValue(tick.value)}
            </text>
          </g>
        ))}
        {labels.map((label, index) => {
          if (!visibleLabel(index)) return null;
          const x = spec.chart_type === "line"
            ? PLOT.left + (index / Math.max(1, labels.length - 1)) * plotWidth
            : PLOT.left + ((index + 0.5) / labels.length) * plotWidth;
          return (
            <text key={`${index}-${label}`} x={x} y={CHART_HEIGHT - 22} textAnchor="middle" className="ocv-axis-text">
              {label.length > 10 ? `${label.slice(0, 9)}…` : label}
            </text>
          );
        })}
        {bars?.bars.map((bar, index) => (
          <rect
            key={`${bar.seriesIndex}-${bar.labelIndex}`}
            x={bar.x}
            y={bar.y}
            width={bar.width}
            height={bar.height}
            rx="2"
            fill={PALETTE[bar.seriesIndex % PALETTE.length]}
          >
            <title>{`${spec.series[bar.seriesIndex].name} · ${labels[bar.labelIndex]}：${formatValue(bar.value, spec.unit)}`}</title>
          </rect>
        ))}
        {lines.map((points, seriesIndex) => (
          <g key={seriesIndex}>
            <path
              d={linePath(points)}
              fill="none"
              stroke={PALETTE[seriesIndex % PALETTE.length]}
              strokeWidth="2.25"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            {points.length <= 20 ? points.map((point) => (
              <circle
                key={point.labelIndex}
                cx={point.x}
                cy={point.y}
                r="3.25"
                fill="#fff"
                stroke={PALETTE[seriesIndex % PALETTE.length]}
                strokeWidth="2"
              >
                <title>{`${spec.series[seriesIndex].name} · ${labels[point.labelIndex]}：${formatValue(point.value, spec.unit)}`}</title>
              </circle>
            )) : null}
          </g>
        ))}
      </svg>
    </div>
  );
}

function PieChart({ spec }: { spec: PieVisualizationSpec }) {
  const data = spec.series[0].data;
  const slices = pieSlices(spec);
  const total = data.reduce((sum, point) => sum + point.value, 0);
  return (
    <div className="ocv-pie-layout">
      <svg viewBox="0 0 284 284" role="img" aria-label={`${spec.title}，饼图`} className="ocv-pie-svg">
        <title>{spec.title}</title>
        {spec.description ? <desc>{spec.description}</desc> : null}
        {slices.map((slice) => (
          <path key={slice.labelIndex} d={slice.path} fill={PALETTE[slice.labelIndex % PALETTE.length]} stroke="#fff" strokeWidth="2">
            <title>{`${data[slice.labelIndex].label}：${formatValue(slice.value, spec.unit)}`}</title>
          </path>
        ))}
        <circle cx="142" cy="142" r="48" fill="#fff" />
        <text x="142" y="138" textAnchor="middle" className="ocv-pie-total-label">合计</text>
        <text x="142" y="160" textAnchor="middle" className="ocv-pie-total-value">{formatValue(total, spec.unit)}</text>
      </svg>
      <div className="ocv-pie-legend" aria-label="图例">
        {data.map((point, index) => (
          <div className="ocv-pie-legend-row" key={`${index}-${point.label}`}>
            <span className="ocv-legend-dot" style={{ background: PALETTE[index % PALETTE.length] }} />
            <span className="ocv-legend-label" title={point.label}>{point.label}</span>
            <span className="ocv-legend-value">{total > 0 ? `${((point.value / total) * 100).toFixed(1)}%` : "0%"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ChartLegend({ spec }: { spec: VisualizationSpec }) {
  if (spec.chart_type === "pie") return null;
  return (
    <div className="ocv-series-legend" aria-label="图例">
      {spec.series.map((series, index) => (
        <span key={`${index}-${series.name}`}>
          <i style={{ background: PALETTE[index % PALETTE.length] }} />
          {series.name}
        </span>
      ))}
    </div>
  );
}

function DataTable({ spec }: { spec: VisualizationSpec }) {
  const labels = spec.series[0].data.map((point) => point.label);
  return (
    <details className="ocv-data-details">
      <summary>查看数据</summary>
      <div className="ocv-table-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">分类</th>
              {spec.series.map((series, index) => <th scope="col" key={`${index}-${series.name}`}>{series.name}</th>)}
            </tr>
          </thead>
          <tbody>
            {labels.map((label, labelIndex) => (
              <tr key={`${labelIndex}-${label}`}>
                <th scope="row">{label}</th>
                {spec.series.map((series, seriesIndex) => (
                  <td key={seriesIndex}>{formatValue(series.data[labelIndex].value, spec.unit)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

export function VisualizationCard({ spec }: { spec: VisualizationSpec }) {
  return (
    <section
      className="ocv-card"
      aria-label={`图表：${spec.title}`}
      style={{
        borderColor: color.border,
        borderRadius: radius.lg,
        background: color.surface,
        color: color.textBody,
        fontFamily: font.sans,
      }}
    >
      <header className="ocv-card-header">
        <span className="ocv-card-icon" style={{ background: color.brandTintBg, color: color.brand }}>
          <Icon name={spec.chart_type === "pie" ? "chart-pie" : spec.chart_type === "bar" ? "chart-bar" : "chart-line"} size={16} />
        </span>
        <div className="ocv-card-titles">
          <h3>{spec.title}</h3>
          {spec.description ? <p>{spec.description}</p> : null}
        </div>
        <span className="ocv-kind-chip" style={{ background: color.pageBg, borderColor: color.border, color: color.textMuted }}>
          {KIND_LABEL[spec.chart_type]}
        </span>
      </header>
      <ChartLegend spec={spec} />
      {spec.chart_type === "pie" ? <PieChart spec={spec} /> : <CartesianChart spec={spec} />}
      <DataTable spec={spec} />
    </section>
  );
}
