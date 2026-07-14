import { useEffect, useState } from "react";
import { color, radius } from "../theme/tokens";
import { Modal, Icon, Pill } from "../ui";
import { api } from "../lib/api";

/** 模板编辑器（B7·二真化 + 编排对称化）：main role + default_tools 勾选（仅 allowed 平台 tool
 *  可绑，**含动态注册表工具**，空=零工具纯编排派发）+ main skills 白名单（空=不限）、
 *  sub Agent 组增删角色与逐角色白名单、保存草稿（另存新版本，可反复改）/ 发布（草稿转正不可变；
 *  已实例化用户下次任务边界自动派生）。 */
export function TemplateEditorModal({ open, templateId, onClose, onChanged }: {
  open: boolean;
  templateId: string | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [tpl, setTpl] = useState<Record<string, unknown> | null>(null);
  const [activeVer, setActiveVer] = useState<Record<string, unknown> | null>(null);
  const [draftVer, setDraftVer] = useState<Record<string, unknown> | null>(null);
  const [role, setRole] = useState("");
  const [tools, setTools] = useState<Set<string>>(new Set());
  const [allowedTools, setAllowedTools] = useState<string[]>([]);
  const [skillKeys, setSkillKeys] = useState<string[]>([]); // 技能目录（active 行的 skill_key）：main/sub skills 勾选源
  const [content, setContent] = useState<Record<string, unknown>>({});
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(true); // 数据未回前禁写，防把空表单存成草稿/发布
  const [disarm, setDisarm] = useState(false); // 禁用版本两步确认（B7·三）

  const load = async (tid: string) => {
    setBusy(true);
    const d = await api.getAdminTemplateDetail(tid);
    setTpl(d.template);
    setActiveVer(d.active_version);
    setDraftVer(d.draft_version);
    const base = (d.draft_version ?? d.active_version) as Record<string, unknown> | null;
    const c = ((base?.content_json ?? {}) as Record<string, unknown>);
    setContent(c);
    const main = (c.main ?? {}) as Record<string, unknown>;
    setRole(String(main.role ?? ""));
    setTools(new Set(((main.default_tools ?? []) as string[])));
    const toolsData = await api.getAdminMcpTools(null);
    setAllowedTools(toolsData.raw.filter((r) => r.annotation_id != null && r.annotation_status === "allowed").map((r) => String(r.tool_name)));
    // 技能目录（SkillHub 对账后的资产）：skills 白名单从这里勾选，键=skill_key（运行时同键）
    const skills = await api.getSkillLibrary().catch(() => []);
    setSkillKeys([...new Set(skills.filter((s) => s.status === "active" && s.skillKey).map((s) => String(s.skillKey)))]);
    setMsg("");
    setErr("");
    setBusy(false);
  };
  useEffect(() => { if (open && templateId) load(templateId).catch((e) => setErr((e as Error).message)); }, [open, templateId]);

  const buildContent = (): Record<string, unknown> => ({
    ...content,
    // __new 是编辑器内部标记（新增角色的 key/label 可输入），入库前剥掉
    sub_agents: ((content.sub_agents ?? []) as Record<string, unknown>[]).map(({ __new: _n, ...rest }) => rest),
    main: { ...((content.main ?? {}) as Record<string, unknown>), role: role.trim(), default_tools: [...tools] },
  });
  const patchMain = (field: string, value: unknown) =>
    setContent({ ...content, main: { ...((content.main ?? {}) as Record<string, unknown>), [field]: value } });
  const mainNum = (field: string, dft: number) => Number(((content.main ?? {}) as Record<string, unknown>)[field] ?? dft);

  const saveDraft = () => {
    if (!templateId) return;
    setBusy(true);
    api.saveTemplateDraft(templateId, buildContent())
      .then((v) => { setDraftVer(v); setMsg(`草稿 v${v.version_no} 已保存（发布后生效）`); setErr(""); })
      .catch((e) => setErr((e as Error).message))
      .finally(() => setBusy(false));
  };
  const publish = () => {
    if (!templateId || !draftVer) return;
    setBusy(true);
    api.saveTemplateDraft(templateId, buildContent())  // 先落当前表单再发布，防未保存修改丢失
      .then((v) => api.publishTemplateVersion(String(v.template_version_id)))
      .then(() => load(templateId))  // 先刷新（load 会清 msg），再设成功提示
      .then(() => { setMsg("已发布：新实例按新版本创建；已实例化用户在下一次任务边界自动升级（保留其角色追加与资产绑定）。"); onChanged(); })
      .catch((e) => setErr((e as Error).message))
      .finally(() => setBusy(false));
  };

  const activeNo = activeVer ? `v${activeVer.version_no}` : "—";
  return (
    <Modal open={open} onClose={onClose} maxWidth={720}>
      <div style={{ flex: "0 0 auto", display: "flex", alignItems: "center", gap: 10, padding: "16px 20px", borderBottom: `1px solid ${color.border}` }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>{String(tpl?.display_name ?? "模板")}</div>
        <span style={{ fontSize: 11.5, fontWeight: 600, color: color.brandStrong, background: color.brandTintBg, padding: "2px 8px", borderRadius: radius.sm }}>{activeNo} · active</span>
        {draftVer ? <Pill tone="warning">草稿 v{String(draftVer.version_no)}</Pill> : null}
        <div style={{ flex: 1 }} />
        {activeVer ? (
          <button
            onClick={() => {
              if (!disarm) { setDisarm(true); return; }
              setBusy(true);
              api.disableTemplateVersion(String(activeVer.template_version_id))
                .then(() => (templateId ? load(templateId) : undefined))
                .then(() => { setMsg("active 版本已禁用：模板暂不可实例化，发布新草稿即恢复。"); setDisarm(false); onChanged(); })
                .catch((e) => setErr((e as Error).message))
                .finally(() => setBusy(false));
            }}
            disabled={busy}
            title="禁用当前 active 版本（模板将暂不可实例化；已有实例不受影响）"
            style={{ height: 32, padding: "0 12px", border: `1px solid ${disarm ? "#e24b4a" : color.border}`, background: disarm ? "#fdecec" : "#fff", borderRadius: radius.md, fontSize: 12, fontWeight: 600, color: disarm ? "#c73835" : color.textNav, cursor: "pointer" }}>
            {disarm ? "再点确认禁用" : "禁用版本"}
          </button>
        ) : null}
        <button onClick={saveDraft} disabled={busy} style={{ height: 32, padding: "0 13px", border: `1px solid ${color.border}`, background: "#fff", borderRadius: radius.md, fontSize: 12, fontWeight: 600, color: color.textNav, cursor: "pointer" }}>保存草稿</button>
        <button onClick={publish} disabled={busy} title={draftVer ? "" : "发布会先保存当前修改为草稿"} style={{ height: 32, padding: "0 14px", border: "none", background: color.brand, color: "#fff", borderRadius: radius.md, fontSize: 12, fontWeight: 700, cursor: "pointer", opacity: busy ? 0.7 : 1 }}>发布</button>
        <Icon name="x" size={18} color="#697283" onClick={onClose} style={{ marginLeft: 4 }} />
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>
        <Box title="main Agent 默认 role（模板级，普通用户只能追加不可改）">
          <textarea
            value={role}
            onChange={(e) => setRole(e.target.value)}
            style={{ width: "100%", minHeight: 90, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: "10px 12px", fontSize: 12.5, lineHeight: 1.6, fontFamily: "inherit", outline: "none", boxSizing: "border-box" }}
          />
          <div style={{ display: "flex", gap: 16, marginTop: 10, alignItems: "center" }}>
            <label style={{ fontSize: 11.5, color: color.textSubtle }} title="同时活跃子 Agent 上限（另有单批硬上限 5）">max_children（1..10）
              <input type="number" min={1} max={10} value={mainNum("max_children", 3)}
                onChange={(e) => patchMain("max_children", Number(e.target.value) || 3)}
                style={{ width: 52, marginLeft: 6, border: `1px solid ${color.borderInput}`, borderRadius: radius.sm, padding: "2px 6px", fontSize: 12 }} />
            </label>
            <label style={{ fontSize: 11.5, color: color.textSubtle }} title="单 task 累计派发兜底（防失败重派死循环）">delegation_max_spawns（1..100）
              <input type="number" min={1} max={100} value={mainNum("delegation_max_spawns", 10)}
                onChange={(e) => patchMain("delegation_max_spawns", Number(e.target.value) || 10)}
                style={{ width: 60, marginLeft: 6, border: `1px solid ${color.borderInput}`, borderRadius: radius.sm, padding: "2px 6px", fontSize: 12 }} />
            </label>
            <span style={{ fontSize: 11, color: color.textFaint }}>派发预算（D 块两层模型）</span>
          </div>
          <div style={{ fontSize: 11.5, color: color.textSubtle, marginTop: 10 }}>
            main skills 白名单（勾选 skill_key；<b>全不勾=不限</b>，沿用 平台 active ∪ 实例绑定）
            <div style={{ marginTop: 6 }}>
              <ChipPicker options={skillKeys}
                selected={(((content.main ?? {}) as Record<string, unknown>).skills ?? []) as string[]}
                onChange={(next) => patchMain("skills", next)}
                emptyHint="技能目录为空——先在设置页上传 Skill 或等 SkillHub 对账（status=active 才可勾）。" />
            </div>
          </div>
        </Box>
        <Box title="main 平台 MCP tool 绑定（仅 allowed 标注可绑，含动态注册表工具；空=零工具纯编排派发，模板外工具运行时 fail-closed）">
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
            {allowedTools.map((t) => (
              <label key={t} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontFamily: "ui-monospace, monospace", border: `1px solid ${tools.has(t) ? color.brand : color.border}`, background: tools.has(t) ? color.brandTintBg : "#fff", padding: "5px 10px", borderRadius: radius.sm, cursor: "pointer" }}>
                <input type="checkbox" checked={tools.has(t)}
                  onChange={(e) => setTools((p) => { const n = new Set(p); e.target.checked ? n.add(t) : n.delete(t); return n; })} />
                {t}
              </label>
            ))}
            {!allowedTools.length ? (
              <div style={{ fontSize: 12, color: color.textSubtle, lineHeight: 1.7 }}>
                目录暂无 allowed 标注工具——先到 <b>资产治理 → Tool 标注</b> 把工具标为 allowed
                （动态注册表工具需先经 MCP 对账进目录），标注后这里即可勾选绑定。
              </div>
            ) : null}
          </div>
          <div style={{ fontSize: 11.5, color: color.textSubtle, marginTop: 8 }}>main 直连的动态工具也须在此勾选（未勾=运行时被剪，由子 Agent 承接）。发布后版本不可原地改；再次修改须另存新草稿。</div>
        </Box>
        <Box title="sub Agent 组（D 块：管理员可编辑；skills/mcp_tools 为该子 Agent 的工具白名单）">
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {(((content.sub_agents ?? []) as Record<string, unknown>[])).map((s, i) => {
              const patch = (field: string, value: unknown) => {
                const subs = [...((content.sub_agents ?? []) as Record<string, unknown>[])];
                subs[i] = { ...subs[i], [field]: value };
                setContent({ ...content, sub_agents: subs });
              };
              const removeSub = () => {
                const subs = ((content.sub_agents ?? []) as Record<string, unknown>[]).filter((_, j) => j !== i);
                setContent({ ...content, sub_agents: subs });
              };
              return (
                <div key={i} style={{ border: `1px solid ${color.borderFaint}`, borderRadius: radius.md, padding: "10px 12px", display: "flex", flexDirection: "column", gap: 6 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    {s.__new ? (
                      <>
                        <input placeholder="key（英文标识）" value={String(s.key ?? "")} onChange={(e) => patch("key", e.target.value.trim())}
                          style={{ width: 110, border: `1px solid ${color.borderInput}`, borderRadius: radius.sm, padding: "3px 8px", fontSize: 12, fontFamily: "ui-monospace, monospace" }} />
                        <input placeholder="label（中文名）" value={String(s.label ?? "")} onChange={(e) => patch("label", e.target.value)}
                          style={{ width: 96, border: `1px solid ${color.borderInput}`, borderRadius: radius.sm, padding: "3px 8px", fontSize: 12 }} />
                      </>
                    ) : (
                      <>
                        <span style={{ fontSize: 12, fontWeight: 700, color: color.brandStrong, background: color.brandTintBg, padding: "2px 8px", borderRadius: radius.sm }}>{String(s.label ?? s.key)}</span>
                        <span style={{ fontSize: 11, color: color.textSubtle, fontFamily: "ui-monospace, monospace" }}>{String(s.key)}</span>
                      </>
                    )}
                    <span style={{ flex: 1 }} />
                    <label style={{ fontSize: 11.5, color: color.textSubtle }}>max_iters
                      <input type="number" min={1} max={200} value={Number(s.max_iters ?? 20)}
                        onChange={(e) => patch("max_iters", Number(e.target.value) || 20)}
                        style={{ width: 58, marginLeft: 6, border: `1px solid ${color.borderInput}`, borderRadius: radius.sm, padding: "2px 6px", fontSize: 12 }} />
                    </label>
                    <label style={{ fontSize: 11.5, color: color.textSubtle }} title="单条工具结果进上下文的保留 token；必须小于模型窗口，经验 ≤1/3（如 128k 窗口 ≤ 40000）">tool_result_limit
                      <input type="number" min={1000} max={200000} step={1000} value={Number(s.tool_result_limit ?? 24000)}
                        onChange={(e) => patch("tool_result_limit", Number(e.target.value) || 24000)}
                        style={{ width: 76, marginLeft: 6, border: `1px solid ${color.borderInput}`, borderRadius: radius.sm, padding: "2px 6px", fontSize: 12 }} />
                    </label>
                    <Icon name="trash" size={14} color={color.dangerText} title="删除该角色（存草稿/发布后生效）" onClick={removeSub} />
                  </div>
                  <textarea value={String(s.role ?? "")} onChange={(e) => patch("role", e.target.value)} rows={2}
                    style={{ width: "100%", border: `1px solid ${color.borderInput}`, borderRadius: radius.sm, padding: "6px 8px", fontSize: 12, resize: "vertical", fontFamily: "inherit" }} />
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    <div style={{ fontSize: 11.5, color: color.textSubtle }}>skills（勾选；该子 Agent 的技能白名单）
                      <div style={{ marginTop: 4 }}>
                        <ChipPicker options={skillKeys} selected={(s.skills ?? []) as string[]}
                          onChange={(next) => patch("skills", next)}
                          emptyHint="技能目录为空——先上传 Skill / 等 SkillHub 对账。" />
                      </div>
                    </div>
                    <div style={{ fontSize: 11.5, color: color.textSubtle }}>mcp_tools（勾选；仅 allowed 标注可选）
                      <div style={{ marginTop: 4 }}>
                        <ChipPicker options={allowedTools} selected={(s.mcp_tools ?? []) as string[]}
                          onChange={(next) => patch("mcp_tools", next)}
                          emptyHint="目录暂无 allowed 标注工具——先到 资产治理 → Tool 标注。" />
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
          <button
            onClick={() => setContent({
              ...content,
              sub_agents: [...((content.sub_agents ?? []) as Record<string, unknown>[]),
                { key: "", label: "", role: "", skills: [], mcp_tools: [], max_iters: 20, tool_result_limit: 24000, __new: true }],
            })}
            style={{ marginTop: 10, height: 30, padding: "0 12px", border: `1px dashed ${color.brand}`, background: color.brandTintBg, borderRadius: radius.md, fontSize: 12, fontWeight: 600, color: color.brandStrong, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 6 }}>
            <Icon name="plus" size={13} color={color.brandStrong} />新增角色
          </button>
          <div style={{ fontSize: 11.5, color: color.textSubtle, marginTop: 8 }}>写工具可绑到子 Agent（E1 审批桥）：需人工审批的工具触发时弹审批卡（任务号带子 Agent 后缀），批准后由该子 Agent 继续执行；tool_result_limit 必须小于模型窗口（经验 ≤1/3）。新增角色的 key/role 必填、key 唯一、绑定工具须已 allowed 标注（发布时校验）。</div>
        </Box>
        <Box title="模板默认 LLM">
          <div style={{ fontSize: 12.5, color: color.textBody, fontFamily: "ui-monospace, monospace" }}>
            {String(((content.default_llm ?? {}) as Record<string, unknown>).provider ?? "platform")} · {String(((content.default_llm ?? {}) as Record<string, unknown>).model ?? "—")}
          </div>
        </Box>
        {msg ? <div style={{ fontSize: 12, color: color.goodText, fontWeight: 600 }}>{msg}</div> : null}
        {err ? <div style={{ fontSize: 12, color: color.dangerText }}>{err}</div> : null}
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

/** 目录勾选器（skills/mcp_tools 白名单通用）：渲染 目录 ∪ 已选 的并集——已选但不在目录的旧值
 * 也显示为可取消 chip（标「不在目录」），绝不静默丢数据；目录与已选都空时显示引导文案。 */
function ChipPicker({ options, selected, onChange, emptyHint }: {
  options: string[]; selected: string[]; onChange: (next: string[]) => void; emptyHint: string;
}) {
  const all = [...new Set([...options, ...selected])];
  if (!all.length) return <div style={{ fontSize: 12, color: color.textSubtle, lineHeight: 1.7 }}>{emptyHint}</div>;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
      {all.map((k) => {
        const on = selected.includes(k);
        return (
          <label key={k} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontFamily: "ui-monospace, monospace", border: `1px solid ${on ? color.brand : color.border}`, background: on ? color.brandTintBg : "#fff", padding: "4px 9px", borderRadius: radius.sm, cursor: "pointer" }}>
            <input type="checkbox" checked={on}
              onChange={(e) => onChange(e.target.checked ? [...selected, k] : selected.filter((x) => x !== k))} />
            {k}{!options.includes(k) ? <span style={{ color: color.dangerText, fontSize: 10 }}>（不在目录）</span> : null}
          </label>
        );
      })}
    </div>
  );
}
