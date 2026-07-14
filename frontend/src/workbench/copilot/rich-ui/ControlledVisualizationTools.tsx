import { useRenderTool } from "@copilotkit/react-core/v2";

import { color, font, radius } from "../../../theme/tokens";
import { VisualizationCard } from "./VisualizationCard";
import { resolveChartRender } from "./renderState";
import { visualizationSchema } from "./schema";

export const RENDER_CHART_TOOL_NAME = "render_chart" as const;

function ChartPendingCard() {
  return (
    <div
      aria-live="polite"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        minHeight: 44,
        padding: "8px 11px",
        border: `1px solid ${color.border}`,
        borderRadius: radius.lg,
        background: color.surface,
        color: color.textMuted,
        fontFamily: font.sans,
        fontSize: 12,
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 7,
          height: 7,
          borderRadius: "50%",
          background: color.brand,
          animation: "omPulse 1.2s ease-in-out infinite",
        }}
      />
      正在整理图表数据…
    </div>
  );
}

function InvalidChartCard() {
  return (
    <div
      role="status"
      style={{
        padding: "8px 11px",
        border: `1px solid ${color.warningBorder}`,
        borderRadius: radius.lg,
        background: color.warningBg,
        color: color.warningText,
        fontFamily: font.sans,
        fontSize: 12,
        lineHeight: 1.45,
      }}
    >
      图表数据不符合安全展示规范，已停止渲染。
    </div>
  );
}

/**
 * Decorates the backend-owned `render_chart` tool with controlled React/SVG UI.
 *
 * This hook does not create a browser tool and does not enable A2UI. The
 * backend remains the authority that decides whether `render_chart` is
 * available. Partial streaming parameters intentionally render only a fixed
 * loading state; complete parameters are validated again before use.
 */
export function ControlledVisualizationTools({ agentId }: { agentId?: string }) {
  useRenderTool(
    {
      name: RENDER_CHART_TOOL_NAME,
      parameters: visualizationSchema,
      agentId,
      render: ({ status, parameters }) => {
        const resolution = resolveChartRender(status, parameters);
        if (resolution.kind === "pending") return <ChartPendingCard />;
        if (resolution.kind === "invalid") return <InvalidChartCard />;
        return <VisualizationCard spec={resolution.spec} />;
      },
    },
    [agentId],
  );
  return null;
}
