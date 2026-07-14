import { visualizationSchema } from "./schema";
import type { VisualizationSpec } from "./schema";

export type ChartRendererStatus = "inProgress" | "executing" | "complete";
export type ChartRenderResolution =
  | { kind: "pending" }
  | { kind: "ready"; spec: VisualizationSpec }
  | { kind: "invalid" };

/** Pure state resolver kept separate so streaming/invalid transitions are testable. */
export function resolveChartRender(status: ChartRendererStatus, parameters: unknown): ChartRenderResolution {
  if (status === "inProgress") return { kind: "pending" };
  const parsed = visualizationSchema.safeParse(parameters);
  if (parsed.success) return { kind: "ready", spec: parsed.data };
  return status === "executing" ? { kind: "pending" } : { kind: "invalid" };
}
