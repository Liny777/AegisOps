import assert from "node:assert/strict";
import test from "node:test";

import { resolveChartRender } from "./renderState";
import { visualizationSchema } from "./schema";

const lineChart = {
  chart_type: "line" as const,
  title: "支付接口 P99",
  description: "最近三十分钟",
  unit: "ms",
  series: [
    { name: "当前", data: [{ label: "10:00", value: 180 }, { label: "10:05", value: 920 }] },
    { name: "基线", data: [{ label: "10:00", value: 160 }, { label: "10:05", value: 170 }] },
  ],
};

test("accepts the canonical line and bar contract", () => {
  assert.equal(visualizationSchema.safeParse(lineChart).success, true);
  assert.equal(visualizationSchema.safeParse({ ...lineChart, chart_type: "bar" }).success, true);
});

test("requires identical ordered labels for every cartesian series", () => {
  const invalid = structuredClone(lineChart);
  invalid.series[1].data[1].label = "10:10";
  assert.equal(visualizationSchema.safeParse(invalid).success, false);
});

test("accepts one non-negative pie series", () => {
  const pie = {
    chart_type: "pie",
    title: "错误类型占比",
    series: [{ name: "请求数", data: [{ label: "超时", value: 12 }, { label: "限流", value: 3 }] }],
  };
  assert.equal(visualizationSchema.safeParse(pie).success, true);
  assert.equal(visualizationSchema.safeParse({ ...pie, series: [...pie.series, pie.series[0]] }).success, false);
  assert.equal(visualizationSchema.safeParse({ ...pie, series: [{ ...pie.series[0], data: [{ label: "超时", value: -1 }] }] }).success, false);
  assert.equal(visualizationSchema.safeParse({ ...pie, series: [{ ...pie.series[0], data: [{ label: "超时", value: 0 }] }] }).success, false);
});

test("rejects HTML, arbitrary style, functions and oversized inputs", () => {
  assert.equal(visualizationSchema.safeParse({ ...lineChart, title: "<img src=x>" }).success, false);
  assert.equal(visualizationSchema.safeParse({ ...lineChart, title: "安全\u202e标题" }).success, false);
  assert.equal(visualizationSchema.safeParse({ ...lineChart, style: { color: "red" } }).success, false);
  assert.equal(visualizationSchema.safeParse({ ...lineChart, formatter: () => "unsafe" }).success, false);
  assert.equal(visualizationSchema.safeParse({
    ...lineChart,
    series: Array.from({ length: 7 }, (_, index) => ({ name: `S${index}`, data: lineChart.series[0].data })),
  }).success, false);
  assert.equal(visualizationSchema.safeParse({
    ...lineChart,
    series: [{ name: "S", data: Array.from({ length: 61 }, (_, index) => ({ label: String(index), value: index })) }],
  }).success, false);
});

test("renderer keeps partial tool arguments pending and only reports terminal invalid data", () => {
  assert.deepEqual(resolveChartRender("inProgress", { chart_type: "line" }), { kind: "pending" });
  assert.deepEqual(resolveChartRender("executing", { chart_type: "line" }), { kind: "pending" });
  assert.deepEqual(resolveChartRender("complete", { chart_type: "line" }), { kind: "invalid" });
  assert.equal(resolveChartRender("executing", lineChart).kind, "ready");
});
