import assert from "node:assert/strict";
import test from "node:test";

import { CHART_HEIGHT, CHART_WIDTH, barRects, linePath, linePoints, numericRange, pieSlices } from "./geometry";
import type { CartesianVisualizationSpec, PieVisualizationSpec } from "./schema";

const line: CartesianVisualizationSpec = {
  chart_type: "line",
  title: "延迟",
  unit: "ms",
  series: [{ name: "P99", data: [{ label: "a", value: 12 }, { label: "b", value: 18 }, { label: "c", value: 9 }] }],
};

test("line geometry is finite and stays inside the view box", () => {
  const points = linePoints(line)[0];
  assert.equal(points.length, 3);
  assert.match(linePath(points), /^M/);
  for (const point of points) {
    assert.equal(Number.isFinite(point.x) && Number.isFinite(point.y), true);
    assert.equal(point.x >= 0 && point.x <= CHART_WIDTH, true);
    assert.equal(point.y >= 0 && point.y <= CHART_HEIGHT, true);
  }
});

test("bar geometry supports positive and negative values", () => {
  const bar: CartesianVisualizationSpec = {
    ...line,
    chart_type: "bar",
    series: [{ name: "变化", data: [{ label: "a", value: -2 }, { label: "b", value: 5 }] }],
  };
  const result = barRects(bar);
  assert.equal(result.bars.length, 2);
  assert.equal(result.range.min < 0 && result.range.max > 0, true);
  assert.equal(result.bars.every((item) => item.width > 0 && item.height > 0), true);
});

test("pie geometry covers a full circle without NaN", () => {
  const pie: PieVisualizationSpec = {
    chart_type: "pie",
    title: "占比",
    series: [{ name: "数量", data: [{ label: "A", value: 3 }, { label: "B", value: 1 }] }],
  };
  const slices = pieSlices(pie);
  assert.equal(slices.length, 2);
  assert.equal(Math.abs(slices[slices.length - 1].endAngle - slices[0].startAngle - Math.PI * 2) < 1e-8, true);
  assert.equal(slices.every((slice) => !slice.path.includes("NaN")), true);

  const one = pieSlices({ ...pie, series: [{ name: "数量", data: [{ label: "A", value: 1 }] }] });
  assert.equal(one.length, 1);
  assert.equal(one[0].path.includes(" A"), true);
});

test("numeric range expands a flat series", () => {
  const range = numericRange([5, 5]);
  assert.equal(range.min < 5 && range.max > 5, true);
});
