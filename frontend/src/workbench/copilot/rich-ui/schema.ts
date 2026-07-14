import { z } from "zod";

/**
 * Controlled visualization contract.
 *
 * Keep this deliberately smaller than a chart library's option object. The
 * model may provide data and short labels only; layout, palette and all SVG
 * attributes remain application-owned. Unknown keys are rejected so HTML,
 * event handlers, functions and arbitrary style objects never reach the view.
 */
export const MAX_SERIES = 6;
export const MAX_CARTESIAN_POINTS = 60;
export const MAX_PIE_POINTS = 12;
export const MAX_TOTAL_POINTS = 240;
export const MAX_ABS_VALUE = 1_000_000_000_000_000;

const UNSAFE_UNICODE_CATEGORY = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}]/u;

function containsUnsafeCharacters(value: string): boolean {
  if (value.includes("<") || value.includes(">")) return true;
  return [...value].some((character) =>
    character !== "\t" && character !== "\r" && character !== "\n" && UNSAFE_UNICODE_CATEGORY.test(character));
}

function safeLabel(max: number, field: string) {
  return z
    .string()
    .refine((value) => !containsUnsafeCharacters(value), `${field}不能包含 HTML、控制字符或格式字符`)
    .transform((value) => value.replace(/\s+/g, " ").trim())
    .pipe(z.string().min(1, `${field}不能为空`).max(max, `${field}不能超过 ${max} 个字符`));
}

const safeNumber = z
  .number()
  .finite("数据点必须是有限数值")
  .min(-MAX_ABS_VALUE, `数据点不能小于 -${MAX_ABS_VALUE}`)
  .max(MAX_ABS_VALUE, `数据点不能大于 ${MAX_ABS_VALUE}`);

const cartesianPointSchema = z
  .object({
    label: safeLabel(80, "数据标签"),
    value: safeNumber,
  })
  .strict();

const piePointSchema = z
  .object({
    label: safeLabel(80, "数据标签"),
    value: safeNumber.nonnegative("饼图数据点不能为负数"),
  })
  .strict();

const cartesianSeriesSchema = z
  .object({
    name: safeLabel(60, "序列名称"),
    data: z.array(cartesianPointSchema).min(1).max(MAX_CARTESIAN_POINTS),
  })
  .strict();

const pieSeriesSchema = z
  .object({
    name: safeLabel(60, "序列名称"),
    data: z.array(piePointSchema).min(1).max(MAX_PIE_POINTS),
  })
  .strict();

const commonShape = {
  title: safeLabel(120, "图表标题"),
  description: safeLabel(300, "图表说明").optional(),
  unit: safeLabel(24, "数值单位").optional(),
};

function cartesianSchema(kind: "line" | "bar") {
  return z
    .object({
      ...commonShape,
      chart_type: z.literal(kind),
      series: z.array(cartesianSeriesSchema).min(1).max(MAX_SERIES),
    })
    .strict()
    .superRefine((value, ctx) => {
      let total = 0;
      const labels = value.series[0]?.data.map((point) => point.label) ?? [];
      const seriesNames = new Set<string>();
      value.series.forEach((series, index) => {
        total += series.data.length;
        const seriesLabels = series.data.map((point) => point.label);
        if (seriesNames.has(series.name)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ["series", index, "name"],
            message: "序列名称不能重复",
          });
        }
        seriesNames.add(series.name);
        if (new Set(seriesLabels).size !== seriesLabels.length) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ["series", index, "data"],
            message: "同一序列的数据标签不能重复",
          });
        }
        if (seriesLabels.length !== labels.length || seriesLabels.some((label, pointIndex) => label !== labels[pointIndex])) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ["series", index, "data"],
            message: "折线图和柱状图各序列的标签及顺序必须一致",
          });
        }
      });
      if (total > MAX_TOTAL_POINTS) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["series"],
          message: `图表数据点总数不能超过 ${MAX_TOTAL_POINTS}`,
        });
      }
    });
}

const pieSchema = z
  .object({
    ...commonShape,
    chart_type: z.literal("pie"),
    series: z.tuple([pieSeriesSchema]),
  })
  .strict()
  .superRefine((value, ctx) => {
    const points = value.series[0].data;
    const values = points.map((point) => point.value);
    if (new Set(points.map((point) => point.label)).size !== points.length) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["series", 0, "data"],
        message: "同一序列的数据标签不能重复",
      });
    }
    if (!values.some((point) => point > 0)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["series", 0, "data"],
        message: "饼图至少需要一个大于 0 的数据点",
      });
    }
  });

export const visualizationSchema = z.union([
  cartesianSchema("line"),
  cartesianSchema("bar"),
  pieSchema,
]);

export type VisualizationSpec = z.infer<typeof visualizationSchema>;
export type CartesianVisualizationSpec = Extract<VisualizationSpec, { chart_type: "line" | "bar" }>;
export type PieVisualizationSpec = Extract<VisualizationSpec, { chart_type: "pie" }>;
