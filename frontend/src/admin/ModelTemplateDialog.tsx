import { useEffect, useState } from "react";
import { color, radius } from "../theme/tokens";
import { Button } from "../ui";
import { api } from "../lib/api";
import type { AdminModelAssetOption } from "../lib/api/types";

/** 模型模板新建/编辑弹窗（38 号，30.6「模型模板」页）：
 * 模板名 / 描述 + 主/子 Agent 模型两个下拉（候选 = 已注册的 active 平台模型资产，value=model_asset_id）。
 * 主=子允许（同一模型跑双槽位是合法配置）。新建态可勾「设为全局默认」、可选授权范围（38.1：
 * 授权在模板维度）；编辑态默认走表格「设默认」/「白名单授权」动作（后端 update 接口刻意
 * 无 is_default / access_scope，各有专用端点）。 */
export function ModelTemplateDialog({ target, onClose, onSaved }: {
  target: string | null; // null=新建；id=编辑
  onClose: () => void; onSaved: () => void;
}) {
  const [assets, setAssets] = useState<AdminModelAssetOption[]>([]);
  const [f, setF] = useState({ display_name: "", description: "", main_model_asset_id: "", sub_model_asset_id: "" });
  const [isDefault, setIsDefault] = useState(false);
  const [scope, setScope] = useState<"all" | "restricted">("all");
  const [busy, setBusy] = useState(true);
  const [err, setErr] = useState("");

  useEffect(() => {
    Promise.all([api.adminListModelAssets(), target ? api.adminListModelTemplates() : Promise.resolve([])])
      .then(([as, tpls]) => {
        setAssets(as.filter((a) => a.status === "active"));
        const cur = target ? tpls.find((t) => t.model_template_id === target) : null;
        if (cur) {
          setF({ display_name: cur.display_name, description: cur.description,
                 main_model_asset_id: cur.main_model_asset_id, sub_model_asset_id: cur.sub_model_asset_id });
        }
        setBusy(false);
      })
      .catch((e) => { setErr((e as Error).message); setBusy(false); });
  }, [target]);

  const canSave = Boolean(f.display_name.trim() && f.main_model_asset_id && f.sub_model_asset_id) && !busy;

  const save = () => {
    setBusy(true);
    setErr("");
    const base = { display_name: f.display_name.trim(), description: f.description.trim(),
                   main_model_asset_id: f.main_model_asset_id, sub_model_asset_id: f.sub_model_asset_id };
    (target ? api.adminUpdateModelTemplate(target, base)
            : api.adminCreateModelTemplate({ ...base, access_scope: scope, is_default: isDefault }))
      .then(onSaved)
      .catch((e) => { setErr((e as Error).message); setBusy(false); });
  };

  const slotSelect = (key: "main_model_asset_id" | "sub_model_asset_id", label: string) => (
    <div>
      <div style={{ fontSize: 11.5, fontWeight: 600, color: color.textSubtle, marginBottom: 4 }}>{label}</div>
      <select value={f[key]} onChange={(e) => setF((d) => ({ ...d, [key]: e.target.value }))}
        style={{ width: "100%", height: 36, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, padding: "0 8px", fontSize: 12.5, background: "#fff", outline: "none", boxSizing: "border-box" }}>
        <option value="">请选择模型</option>
        {assets.map((a) => (
          <option key={a.model_asset_id} value={a.model_asset_id}>{a.display_name}（{a.model_id}）</option>
        ))}
      </select>
    </div>
  );

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 60, background: "rgba(20,24,31,.42)", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 460, background: "#fff", borderRadius: radius.modal, padding: "22px 22px 18px" }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 14 }}>{target ? "编辑模型模板" : "新建模型模板"}</div>
        {!busy && assets.length === 0 ? (
          <div style={{ fontSize: 12.5, color: color.textSubtle, lineHeight: 1.7, padding: "8px 0 4px" }}>
            暂无已注册的平台模型：请先到 <b>管理台 → 模型资产</b> 注册模型接口，再回来编排模型模板。
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <input value={f.display_name} onChange={(e) => setF((d) => ({ ...d, display_name: e.target.value }))}
              placeholder="模板名，如：均衡（推荐）"
              style={{ width: "100%", height: 36, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, padding: "0 11px", fontSize: 12.5, outline: "none", boxSizing: "border-box" }} />
            <input value={f.description} onChange={(e) => setF((d) => ({ ...d, description: e.target.value }))}
              placeholder="描述（选填），如：主推理 + 子执行的默认组合"
              style={{ width: "100%", height: 36, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, padding: "0 11px", fontSize: 12.5, outline: "none", boxSizing: "border-box" }} />
            {slotSelect("main_model_asset_id", "主 Agent 模型（任务理解与编排）")}
            {slotSelect("sub_model_asset_id", "子 Agent 模型（巡检 / 诊断 / 恢复等全部子角色共用）")}
            <div style={{ fontSize: 11.5, color: color.textSubtle, lineHeight: 1.5, marginTop: -2 }}>
              主 / 子槽位可选同一模型；模板对用户的可见性由「授权范围」控制（全员开放，或经列表「白名单授权」限定人员）。
            </div>
            {target ? null : (
              <div style={{ display: "flex", gap: 14, fontSize: 12.5 }}>
                {(["all", "restricted"] as const).map((s) => (
                  <label key={s} style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                    <input type="radio" checked={scope === s} onChange={() => setScope(s)} />
                    {s === "all" ? "全员开放" : "限定人员（创建后在列表「白名单授权」配置）"}
                  </label>
                ))}
              </div>
            )}
            {target ? null : (
              <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12.5, cursor: "pointer" }}>
                <input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} />
                设为全局默认（用户初始化 Agent 时预选本模板）
              </label>
            )}
          </div>
        )}
        {err ? <div style={{ fontSize: 12, color: color.dangerText, marginTop: 10 }}>{err}</div> : null}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
          <Button variant="secondary" onClick={onClose}>取消</Button>
          <Button disabled={!canSave} onClick={save}>{target ? "保存" : "创建"}</Button>
        </div>
      </div>
    </div>
  );
}
