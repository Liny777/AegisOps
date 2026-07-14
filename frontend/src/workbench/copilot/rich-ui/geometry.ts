import type { CartesianVisualizationSpec, PieVisualizationSpec } from "./schema";

export const CHART_WIDTH = 680;
export const CHART_HEIGHT = 284;
export const PLOT = { left: 54, right: 18, top: 18, bottom: 48 } as const;

export interface ChartPoint {
  x: number;
  y: number;
  value: number;
  labelIndex: number;
}

export interface BarRect extends ChartPoint {
  width: number;
  height: number;
  seriesIndex: number;
}

export interface PieSlice {
  startAngle: number;
  endAngle: number;
  value: number;
  labelIndex: number;
  path: string;
}

export interface NumericRange {
  min: number;
  max: number;
}

const plotWidth = CHART_WIDTH - PLOT.left - PLOT.right;
const plotHeight = CHART_HEIGHT - PLOT.top - PLOT.bottom;

export function numericRange(values: number[], includeZero = false): NumericRange {
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (includeZero) {
    min = Math.min(0, min);
    max = Math.max(0, max);
  }
  if (min === max) {
    const padding = Math.abs(min) * 0.1 || 1;
    min -= padding;
    max += padding;
  } else {
    const padding = (max - min) * 0.08;
    min -= padding;
    max += padding;
  }
  return { min, max };
}

export function yForValue(value: number, range: NumericRange): number {
  const ratio = (value - range.min) / (range.max - range.min);
  return PLOT.top + (1 - ratio) * plotHeight;
}

export function linePoints(spec: CartesianVisualizationSpec): ChartPoint[][] {
  const values = spec.series.flatMap((series) => series.data.map((point) => point.value));
  const range = numericRange(values);
  const denominator = Math.max(1, spec.series[0].data.length - 1);
  return spec.series.map((series) =>
    series.data.map((point, labelIndex) => ({
      x: PLOT.left + (labelIndex / denominator) * plotWidth,
      y: yForValue(point.value, range),
      value: point.value,
      labelIndex,
    })),
  );
}

export function linePath(points: ChartPoint[]): string {
  return points.map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ");
}

export function barRects(spec: CartesianVisualizationSpec): { bars: BarRect[]; range: NumericRange; zeroY: number } {
  const values = spec.series.flatMap((series) => series.data.map((point) => point.value));
  const range = numericRange(values, true);
  const zeroY = yForValue(0, range);
  const labels = spec.series[0].data;
  const groupWidth = plotWidth / labels.length;
  const innerWidth = Math.min(groupWidth * 0.72, 42);
  const width = Math.max(2, innerWidth / spec.series.length - 2);
  const bars: BarRect[] = [];

  labels.forEach((_point, labelIndex) => {
    const groupStart = PLOT.left + labelIndex * groupWidth + (groupWidth - innerWidth) / 2;
    spec.series.forEach((series, seriesIndex) => {
      const value = series.data[labelIndex].value;
      const valueY = yForValue(value, range);
      bars.push({
        x: groupStart + seriesIndex * (width + 2),
        y: Math.min(valueY, zeroY),
        width,
        height: Math.max(1, Math.abs(zeroY - valueY)),
        value,
        labelIndex,
        seriesIndex,
      });
    });
  });
  return { bars, range, zeroY };
}

function polar(cx: number, cy: number, radius: number, angle: number): { x: number; y: number } {
  return { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) };
}

export function pieSlices(spec: PieVisualizationSpec, cx = 142, cy = 142, radius = 94): PieSlice[] {
  const values = spec.series[0].data.map((point) => point.value);
  const total = values.reduce((sum, value) => sum + value, 0);
  let cursor = -Math.PI / 2;
  return values.map((value, labelIndex) => {
    const startAngle = cursor;
    const span = (value / total) * Math.PI * 2;
    const endAngle = cursor + span;
    cursor = endAngle;
    if (span >= Math.PI * 2 - 1e-8) {
      return {
        startAngle,
        endAngle,
        value,
        labelIndex,
        path: `M${cx},${cy - radius} A${radius},${radius} 0 1 1 ${cx},${cy + radius} A${radius},${radius} 0 1 1 ${cx},${cy - radius} Z`,
      };
    }
    const start = polar(cx, cy, radius, startAngle);
    const end = polar(cx, cy, radius, endAngle);
    const largeArc = span > Math.PI ? 1 : 0;
    return {
      startAngle,
      endAngle,
      value,
      labelIndex,
      path: `M${cx},${cy} L${start.x.toFixed(2)},${start.y.toFixed(2)} A${radius},${radius} 0 ${largeArc} 1 ${end.x.toFixed(2)},${end.y.toFixed(2)} Z`,
    };
  });
}
