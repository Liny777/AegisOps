// 会话级模型选择器（Part B re-home）：浮在 CopilotChat 右上角，不占布局高度。
// 数据/写面复用既有 facade（getModelConfigs / selectModel，MODEL-005 会话级不生成配置版本）。
import { useEffect, useState } from "react";
import { color, radius } from "../../theme/tokens";
import { api } from "../../lib/api";
import type { ModelOption } from "../../lib/api/types";

export function CopilotModelFloat({ runId }: { runId: string }) {
  const [models, setModels] = useState<ModelOption[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);

  useEffect(() => {
    let dead = false;
    api.getModelConfigs()
      .then((ms) => {
        if (dead) return;
        setModels(ms);
        setCurrentId(ms.find((m) => m.current)?.llm_config_id ?? ms[0]?.llm_config_id ?? null);
      })
      .catch(() => undefined);
    return () => { dead = true; };
  }, [runId]);

  if (models.length < 2) return null;

  const onChange = async (id: string) => {
    if (id === currentId) return;
    const prev = currentId;
    setCurrentId(id);
    try {
      await api.selectModel(runId, id);
    } catch (e) {
      setCurrentId(prev);
      alert(`切换模型失败：${(e as Error).message}`);
    }
  };

  return (
    <select
      value={currentId ?? ""}
      onChange={(e) => void onChange(e.target.value)}
      title="会话级模型（本会话临时生效）"
      style={{ appearance: "none", height: 28, border: `1px solid ${color.borderInput}`, background: "#fff", borderRadius: radius.md, padding: "0 10px", fontSize: 12, color: color.textStrong, cursor: "pointer", outline: "none" }}
    >
      {models.map((m) => (
        <option key={m.llm_config_id} value={m.llm_config_id} disabled={!m.available}>
          {m.label}{m.available ? "" : `（${m.reason ?? "不可用"}）`}
        </option>
      ))}
    </select>
  );
}
