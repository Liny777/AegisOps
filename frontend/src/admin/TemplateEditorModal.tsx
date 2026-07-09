import { color, radius } from "../theme/tokens";
import { Modal, Icon } from "../ui";

const SUB_AGENTS = [
  { key: "inspect", label: "巡检", role: "基于应用范围查看健康状态、异常信号与风险", tools: "query_metrics / query_health" },
  { key: "diagnose", label: "定界", role: "结合告警/指标/日志/链路/拓扑判断问题边界", tools: "query_logs / omodel_topo" },
  { key: "recover", label: "恢复", role: "给出受控恢复建议，用户确认后执行", tools: "recover_execute（需 ASK）" },
];

/** 模板编辑器模态（管理员）：main/sub Agent、模板 LLM、平台 Skill/MCP 绑定、发版。 */
export function TemplateEditorModal({ open, name, onClose }: { open: boolean; name: string; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} maxWidth={720}>
      <div style={{ flex: "0 0 auto", display: "flex", alignItems: "center", gap: 10, padding: "16px 20px", borderBottom: `1px solid ${color.border}` }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>{name}</div>
        <span style={{ fontSize: 11.5, fontWeight: 600, color: color.brandStrong, background: color.brandTintBg, padding: "2px 8px", borderRadius: radius.sm }}>v3 · active</span>
        <div style={{ flex: 1 }} />
        <button style={{ height: 32, padding: "0 13px", border: `1px solid ${color.border}`, background: "#fff", borderRadius: radius.md, fontSize: 12, fontWeight: 600, color: color.textNav, cursor: "pointer" }}>另存新版本</button>
        <button style={{ height: 32, padding: "0 14px", border: "none", background: color.brand, color: "#fff", borderRadius: radius.md, fontSize: 12, fontWeight: 700, cursor: "pointer" }}>发布</button>
        <Icon name="x" size={18} color="#697283" onClick={onClose} style={{ marginLeft: 4 }} />
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>
        <Box title="main Agent">
          <div style={{ fontSize: 12, color: color.textMuted, background: "#f5f6f8", borderRadius: radius.md, padding: "10px 12px", lineHeight: 1.6 }}>
            默认 role：理解用户任务，调度巡检/定界/恢复能力，工具调用前遵守平台安全策略。 · 权限上下文：effective_appids · 默认平台工具：query_metrics / query_logs
          </div>
        </Box>
        <Box title="sub Agent 组（只读，普通用户不可改）">
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {SUB_AGENTS.map((s) => (
              <div key={s.key} style={{ border: `1px solid ${color.borderFaint}`, borderRadius: radius.md, padding: "10px 12px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: color.brandStrong, background: color.brandTintBg, padding: "2px 8px", borderRadius: radius.sm }}>{s.label}</span>
                  <span style={{ fontSize: 11.5, color: color.textSubtle, fontFamily: "ui-monospace, monospace" }}>{s.tools}</span>
                </div>
                <div style={{ fontSize: 12, color: color.textBody, lineHeight: 1.5 }}>{s.role}</div>
              </div>
            ))}
          </div>
        </Box>
        <Box title="模板默认 LLM">
          <div style={{ fontSize: 12.5, color: color.textBody }}>provider: 平台网关 · model: Qwen3.5（128k，支持 tool calling）</div>
        </Box>
        <Box title="平台 Skill / MCP tool 绑定">
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {["巡检 inspection v2", "query_metrics", "query_logs", "omodel_topo", "recover_execute · 需审批"].map((t) => (
              <span key={t} style={{ fontSize: 11.5, fontWeight: 600, color: color.textNav, background: color.neutralBg, border: `1px solid ${color.border}`, padding: "4px 9px", borderRadius: radius.sm, fontFamily: "ui-monospace, monospace" }}>{t}</span>
            ))}
          </div>
          <div style={{ fontSize: 11.5, color: color.textSubtle, marginTop: 8 }}>只能绑定 mcp_tool_annotation.status = allowed 的平台 tool；发布后版本不可原地改。</div>
        </Box>
      </div>
    </Modal>
  );
}

function Box({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ border: `1px solid ${color.border}`, borderRadius: radius.lg, padding: 14 }}>
      <div style={{ fontSize: 12.5, fontWeight: 700, color: color.textNav, marginBottom: 8 }}>{title}</div>
      {children}
    </div>
  );
}
