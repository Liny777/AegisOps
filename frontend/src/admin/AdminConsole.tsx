import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { color, radius } from "../theme/tokens";
import { toneColor } from "../theme/tokens";
import { Icon, Button, Dot, Pill, Pagination } from "../ui";
import { api, alertEventMeta } from "../lib/api";
import type { AdminModelAssetOption, AdminTableData, AlertOpsConfig, SandboxCfg, SandboxContainer, AuditNode } from "../lib/api/types";
import { useConnTest, ConnTestResult, HeadersEditor, headersToRecord, PROTOCOL_LABEL, DEFAULT_CONTEXT_WINDOW, type HeaderRow } from "../settings/AddCustomModelDialog";
import { PlatformAssetDialog } from "./PlatformAssetDialog";
import { ToolAnnotationSlideIn } from "./ToolAnnotationSlideIn";
import { TemplateEditorModal } from "./TemplateEditorModal";
import { ModelTemplateDialog } from "./ModelTemplateDialog";
import { STUDIO_ADMIN_PAGE } from "../studio/entry";
import { groupCatalog, normalizeSelection } from "./toolBinding";

/** 管理台表格每页条数（服务端分页；后端上限 100，对齐 29.3 §2.2）。 */
const ADMIN_PAGE_SIZE = 20;

const TITLES: Record<string, string> = {
  templates: "模板管理",
  "model-assets": "模型资产",
  "model-templates": "模型模板",
  skills: "Skill 基线",
  mcps: "MCP 服务",
  users: "用户与白名单",
  alerts: "告警接管清单",
  "alerts-config": "告警接管配置",
  sandbox: "沙箱与容量",
  audit: "审计与 Trace 回放",
  [STUDIO_ADMIN_PAGE.key]: STUDIO_ADMIN_PAGE.title,
};

/** 管理台（30.6 2026-07-09 IA）：5 一级页；模板管理内 drill（资产治理 → Tool 标注，标注全局一份）。 */
export function AdminConsole() {
  const { page = "templates" } = useParams();
  const [table, setTable] = useState<AdminTableData | null>(null);
  const [tablePage, setTablePage] = useState(1);  // 服务端分页（目前 Skill 基线消费；其余表后端不返 total → 不渲染分页器）
  const [audit, setAudit] = useState<AuditNode[]>([]);
  // 模板 drill：模板管理 → 模板名·资产治理 → MCP名·Tool 标注
  const [tplDrill, setTplDrill] = useState<{ id: string; name: string } | null>(null);
  const [mcpDrill, setMcpDrill] = useState<string | null>(null);
  const [toolsRaw, setToolsRaw] = useState<Record<string, unknown>[]>([]);
  const [annotRow, setAnnotRow] = useState<Record<string, unknown> | null>(null);
  // 模型资产：注册弹窗；模型模板：白名单授权弹窗（38.1 授权在模板维度）；模板编辑器（B7·二）
  const [registerOpen, setRegisterOpen] = useState(false);
  const [grantsFor, setGrantsFor] = useState<{ id: string; name: string } | null>(null);
  const [tplEdit, setTplEdit] = useState<string | null>(null);
  // 模型模板（38 号）：新建/编辑弹窗（id=null 新建）
  const [mtEdit, setMtEdit] = useState<{ id: string | null } | null>(null);
  const [maEdit, setMaEdit] = useState<AdminModelAssetOption | null>(null);
  // B7·三：白名单添加弹窗 / 表格动作错误横幅 / 审计 Trace 过滤
  const [wlAddOpen, setWlAddOpen] = useState(false);
  // 平台级资产写闭环（29.9）：上传 Skill / 注册 MCP 弹窗 + 成功提示（同名收敛条数要显式告知）
  const [assetDialog, setAssetDialog] = useState<"skill" | "mcp" | null>(null);
  const [actionOk, setActionOk] = useState("");
  const [actionErr, setActionErr] = useState("");
  const [traceFilter, setTraceFilter] = useState("");
  // 用户表关键字搜索（服务端 q 过滤 user_id/display_name，与审计过滤同做法：输入即查）
  const [userQ, setUserQ] = useState("");
  // 用户表标签筛选（服务端 tag 过滤，与 q 为 AND）：tagFilter=""=全部；userTags=下拉候选（标签全集）
  const [tagFilter, setTagFilter] = useState("");
  const [userTags, setUserTags] = useState<string[]>([]);
  // 告警事件表（管理员全量视角 §3.0 D1）：按用户精确 + 编号/类型/对象模糊，两者 AND
  const [alertUserQ, setAlertUserQ] = useState("");
  const [alertSearchQ, setAlertSearchQ] = useState("");

  const isTable = ["templates", "model-assets", "model-templates", "skills", "mcps", "users", "alerts"].includes(page);

  const load = useCallback(async () => {
    if (page === "templates") {
      if (mcpDrill) {
        const d = await api.getAdminMcpTools(mcpDrill);
        setToolsRaw(d.raw);
        setTable(d);
      } else if (tplDrill) {
        // 资产治理（drill 1）：平台资产表 + 按模板 default_tools 计算「绑定/解绑」列（B7·二写路径）
        const [assets, detail, toolsData] = await Promise.all([
          api.getAdminTemplateAssets(),
          api.getAdminTemplateDetail(tplDrill.id),
          api.getAdminMcpTools(null),
        ]);
        const base = (detail.draft_version ?? detail.active_version) as Record<string, unknown> | null;
        // 绑定判定按「server::tool」复合键（读时归一存量裸名）：同名工具跨 server 不再互相点亮绑定列
        const groups = groupCatalog(toolsData.raw);
        const boundRaw = (((base?.content_json as Record<string, unknown>)?.main as Record<string, unknown>)?.default_tools ?? []) as string[];
        const bound = new Set(normalizeSelection(boundRaw, groups).keys);
        setTable({
          ...assets,
          cols: [...assets.cols, { label: "模板绑定", width: "72px" }],
          rows: assets.rows.map((r) => {
            const keys = (groups[r.id] ?? []).map((t) => t.key);
            const isBound = keys.length > 0 && keys.every((k) => bound.has(k));
            return { ...r, cells: [...r.cells, { text: isBound ? "解绑" : "绑定", kind: "action" as const, onClickKey: "toggle-bind" }] };
          }),
        });
      } else {
        setTable(await api.getAdminTable("templates"));
      }
    } else if (isTable) {
      setTable(await api.getAdminTable(page === "alerts" ? "alert-events" : page, {
        page: tablePage, pageSize: ADMIN_PAGE_SIZE,
        q: page === "users" ? userQ.trim() || undefined
           : page === "alerts" ? alertSearchQ.trim() || undefined : undefined,
        tag: page === "users" ? tagFilter || undefined : undefined,
        userId: page === "alerts" ? alertUserQ.trim() || undefined : undefined,
      }));
    } else if (page === "audit") {
      setAudit(traceFilter ? await api.getAuditTrace(traceFilter) : await api.getAuditTimeline());
    }
  }, [page, isTable, tplDrill, mcpDrill, traceFilter, tablePage, userQ, tagFilter, alertUserQ, alertSearchQ]);

  // 进用户页拉标签下拉候选（标签全集）；离开或失败置空，不阻断列表
  useEffect(() => {
    if (page !== "users") { setUserTags([]); return; }
    api.adminListUserTags().then(setUserTags).catch(() => setUserTags([]));
  }, [page]);

  useEffect(() => { setTplDrill(null); setMcpDrill(null); setUserQ(""); setTagFilter(""); setAlertUserQ(""); setAlertSearchQ(""); }, [page]);
  useEffect(() => { setTablePage(1); }, [page, tplDrill, mcpDrill, userQ, tagFilter, alertUserQ, alertSearchQ]);  // 换页签/钻取/改搜索词/改标签回第 1 页，免停在越界页看空表
  // 加载失败进错误横幅：否则 load() 内任一请求 reject 都只留一条 unhandled rejection，界面静默空白。
  // 同时清掉 table——失败时留着上一个视图的旧表，会让它顶着新面包屑冒充本页数据。
  useEffect(() => {
    setActionErr("");
    setActionOk("");  // 换页/翻页即清成功提示（直接调 load() 的动作路径不触发本 effect，提示得以保留）
    void load().catch((e) => {
      const err = e as { code?: string; message?: string };
      if (err.code === "AUTH_REDIRECT") return; // 正在跳 IAM 登录页，别闪错误横幅
      setTable(null);
      setActionErr(err.message || "加载失败");
    });
  }, [load]);

  const tabs = table?.tabs;
  const gridCols = useMemo(() => (table ? table.cols.map((c) => c.width ?? "1fr").join(" ") : ""), [table]);

  const onCellAction = (key: string | undefined, rowId: string, rowName: string) => {
    if (key === "open-template") setTplDrill({ id: rowId, name: rowName });
    else if (key === "edit-template") setTplEdit(rowId);
    else if (key === "open-mcp") setMcpDrill(rowId);
    else if (key === "annotate") setAnnotRow(toolsRaw.find((r) => String(r.tool_catalog_id) === rowId) ?? null);
    else if (key === "mt-grants") setGrantsFor({ id: rowId, name: rowName });
    else if (key === "toggle-bind" && tplDrill) void toggleBind(rowId);
    else if (key === "alert-open") {
      const meta = alertEventMeta[rowId];
      if (meta?.detailUrl) window.open(meta.detailUrl, "_blank", "noopener");
    }
    else if (key === "alert-prioritize" || key === "alert-deprioritize") {
      // §5.3 软插队：reason 必填写审计（照 destroySandboxContainer 的 prompt 先例）
      const cancel = key === "alert-deprioritize";
      const why = window.prompt(cancel ? "取消置顶原因（写审计）：" : "置顶原因（写审计）：");
      if (!why || !why.trim()) return;
      const meta = alertEventMeta[rowId];
      if (!meta?.incidentId) return;
      setActionErr("");
      void api.adminPrioritizeIncident(meta.incidentId, why.trim(), cancel)
        .then(() => load()).catch((e) => setActionErr((e as Error).message));
    }
    else if (key === "alert-grant" || key === "alert-revoke") {
      // 告警接管白名单（算力有限按需开通）：即点即切，错误进动作横幅
      setActionErr("");
      void api.adminSetAlertGrant(rowId, key === "alert-grant")
        .then(() => load()).catch((e) => setActionErr((e as Error).message));
    }
    else if (key === "wl-revoke" || key === "wl-add") {
      setActionErr("");
      const op = key === "wl-revoke" ? api.adminRevokeWhitelist(rowId) : api.adminAddWhitelist(rowId, "");
      void op.then(() => load()).catch((e) => setActionErr((e as Error).message));
    }
    else if (key === "role-admin" || key === "role-user") {
      // 角色升/降（set-role 补链）：改自己后端 400（防管理面锁死），错误进动作横幅
      setActionErr("");
      void api.adminSetRole(rowId, key === "role-admin" ? "platform_admin" : "user")
        .then(() => load()).catch((e) => setActionErr((e as Error).message));
    }
    else if (key === "user-delete") {
      // 删除用户（软删 + 连带撤白）：破坏性动作先确认；删自己后端 400 拦（防管理面锁死）
      if (!window.confirm(`确认删除用户 ${rowId}？将同时移出白名单并收回访问权限，重新加入白名单才会恢复。`)) return;
      setActionErr("");
      void api.adminDeleteUser(rowId).then(() => load()).catch((e) => setActionErr((e as Error).message));
    }
    else if (key === "mt-edit") setMtEdit({ id: rowId });
    else if (key === "mt-disable" || key === "mt-enable") {
      // 停用不删数据：用户选单隐身、已绑实例下次起任务降级回退（审计留痕）；启用即恢复
      if (key === "mt-disable" && !window.confirm(
        `停用模型模板「${rowName}」？停用后不再出现在用户选单，已绑定该模板的实例起任务时回退平台默认模型。`)) return;
      setActionErr("");
      void api.adminSetModelTemplateStatus(rowId, key === "mt-enable" ? "active" : "disabled")
        .then(() => load()).catch((e) => setActionErr((e as Error).message));
    }
    else if (key === "mt-default") {
      setActionErr("");
      void api.adminSetModelTemplateDefault(rowId)
        .then(() => load()).catch((e) => setActionErr((e as Error).message));
    }
    else if (key === "mt-delete") {
      // 软删（38.2）：已绑实例走既有降级链（下次任务回退平台默认 + 审计留痕），不阻断删除
      if (!window.confirm(
        `确认删除模型模板「${rowName}」？已绑定该模板的实例将在下次任务回退平台默认模型；如为默认模板，删除后用户选单回退首个模板。`)) return;
      setActionErr("");
      void api.adminDeleteModelTemplate(rowId).then(() => load()).catch((e) => setActionErr((e as Error).message));
    }
    else if (key === "ma-edit") {
      // 表格 cells 只有文本，改连接配置要完整行 → 现拉一次资产清单再开弹窗
      setActionErr("");
      void api.adminListModelAssets()
        .then((rows) => {
          const row = rows.find((a) => a.model_asset_id === rowId);
          if (row) setMaEdit(row);
          else setActionErr("该模型资产已不存在，请刷新后重试");
        })
        .catch((e) => setActionErr((e as Error).message));
    }
    else if (key === "ma-delete") {
      // 软删（38.2）：被模板槽位引用时后端 400（错误横幅提示先调整模板）；同 model_id 删后可重注册
      if (!window.confirm(`确认删除模型资产「${rowName}」？被模型模板引用时将拒绝删除（需先调整模板槽位）。`)) return;
      setActionErr("");
      void api.adminDeleteModelAsset(rowId).then(() => load()).catch((e) => setActionErr((e as Error).message));
    }
    else if (key === "skill-delete") {
      // 平台 Skill 下架：先回删 SkillHub 再本地软删；上游拒绝会整体失败（错误进横幅），本地不会假删
      if (!window.confirm(
        `确认删除平台 Skill「${rowName}」？将同时从 Skill Hub 下架，所有用户都不再可用；已绑定该 Skill 的实例其绑定项会显示「已删除」。`)) return;
      setActionErr(""); setActionOk("");
      void api.adminDeleteSkill(rowId).then(() => { setActionOk(`已删除「${rowName}」`); return load(); })
        .catch((e) => setActionErr((e as Error).message));
    }
    else if (key === "mcp-delete") {
      if (!window.confirm(
        `确认删除平台 MCP「${rowName}」？将同时从 MCP Registry 注销，其工具会从模板草稿中摘除。`)) return;
      setActionErr(""); setActionOk("");
      void api.adminDeleteMcp(rowId).then(() => { setActionOk(`已删除「${rowName}」`); return load(); })
        .catch((e) => setActionErr((e as Error).message));
    }
    else if (key === "user-tags") {
      // 行内编辑领域标签（prompt 交互与「销毁容器 reason」同风格）：当前值从「标签」列单元格取（「设标签」=空）
      const ti = table?.cols.findIndex((c) => c.label === "标签") ?? -1;
      const cellText = ti >= 0 ? String(table?.rows.find((r) => r.id === rowId)?.cells[ti]?.text ?? "") : "";
      const cur = cellText === "设标签" ? "" : cellText.split("、").join(",");
      const input = window.prompt(`设置用户 ${rowId} 的领域标签（多个用逗号分隔，如 财经,研发；留空清除全部）：`, cur);
      if (input === null) return; // 取消不动
      const tags = input.split(/[,，、\s]+/).map((t) => t.trim()).filter(Boolean);
      setActionErr("");
      void api.adminSetTags(rowId, tags).then(() => load()).catch((e) => setActionErr((e as Error).message));
    }
  };

  /** 资产治理「绑定/解绑」：该 MCP 的全部 allowed tools 以「server::tool」复合键加入/移出模板草稿
   * default_tools（发布后生效）。存量裸名先读时归一——增删只命中本 server 的键，同名工具不串家。 */
  const toggleBind = async (mcpName: string) => {
    if (!tplDrill) return;
    const [detail, toolsData] = await Promise.all([
      api.getAdminTemplateDetail(tplDrill.id),
      api.getAdminMcpTools(null),
    ]);
    const base = (detail.draft_version ?? detail.active_version) as Record<string, unknown> | null;
    const content = { ...((base?.content_json ?? {}) as Record<string, unknown>) };
    const main = { ...((content.main ?? {}) as Record<string, unknown>) };
    const groups = groupCatalog(toolsData.raw);
    const cur = new Set(normalizeSelection(((main.default_tools ?? []) as string[]), groups).keys);
    const mcpKeys = (groups[mcpName] ?? []).map((t) => t.key);
    const isBound = mcpKeys.length > 0 && mcpKeys.every((k) => cur.has(k));
    mcpKeys.forEach((k) => (isBound ? cur.delete(k) : cur.add(k)));
    content.main = { ...main, default_tools: [...cur] };
    try {
      const v = await api.saveTemplateDraft(tplDrill.id, content);
      alert(`已保存到草稿 v${v.version_no}（在模板编辑器发布后生效）`);
      void load();
    } catch (e) {
      alert((e as Error).message);
    }
  };

  // 面包屑（drill 时替换标题）：模板管理 / 模板名 · 资产治理 / MCP名 · Tool 标注
  const crumbs = page === "templates" && tplDrill
    ? [
        { label: "模板管理", onClick: () => { setTplDrill(null); setMcpDrill(null); } },
        { label: `${tplDrill.name} · 资产治理`, onClick: mcpDrill ? () => setMcpDrill(null) : undefined },
        ...(mcpDrill ? [{ label: `${mcpDrill} · Tool 标注`, onClick: undefined }] : []),
      ]
    : null;

  return (
    <>
      <header style={{ flex: "0 0 auto", height: 56, borderBottom: `1px solid ${color.border}`, background: "#fff", display: "flex", alignItems: "center", padding: "0 24px", gap: 12 }}>
        {crumbs ? (
          <div style={{ fontSize: 14, fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
            {crumbs.map((c, i) => (
              <span key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {i > 0 ? <Icon name="chevron-right" size={14} color={color.textFaint} /> : null}
                <span onClick={c.onClick} style={{ cursor: c.onClick ? "pointer" : "default", color: c.onClick ? color.brand : color.textStrong, fontWeight: i === crumbs.length - 1 ? 700 : 600 }}>{c.label}</span>
              </span>
            ))}
          </div>
        ) : (
          <div style={{ fontSize: 15, fontWeight: 700 }}>{TITLES[page] ?? "管理台"}</div>
        )}
        <div style={{ flex: 1 }} />
        {page === "model-assets" && table?.primary ? (
          <Button icon={table.primary.icon} onClick={() => setRegisterOpen(true)}>{table.primary.label}</Button>
        ) : null}
        {page === "model-templates" && table?.primary ? (
          <Button icon={table.primary.icon} onClick={() => setMtEdit({ id: null })}>{table.primary.label}</Button>
        ) : null}
        {(page === "skills" || page === "mcps") && table?.primary ? (
          <Button icon={table.primary.icon}
            onClick={() => setAssetDialog(page === "skills" ? "skill" : "mcp")}>{table.primary.label}</Button>
        ) : null}
        {page === "alerts" ? (
          <>
            <input
              placeholder="按用户查看（user_id 精确）"
              value={alertUserQ}
              onChange={(e) => setAlertUserQ(e.target.value)}
              style={{ width: 200, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: "6px 10px", fontSize: 12, outline: "none" }}
            />
            <input
              placeholder="搜索 编号/类型/对象"
              value={alertSearchQ}
              onChange={(e) => setAlertSearchQ(e.target.value)}
              style={{ width: 200, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: "6px 10px", fontSize: 12, outline: "none" }}
            />
          </>
        ) : null}
        {page === "users" ? (
          <input
            placeholder="搜索 user_id / 展示名"
            value={userQ}
            onChange={(e) => setUserQ(e.target.value)}
            style={{ width: 220, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: "6px 10px", fontSize: 12, outline: "none" }}
          />
        ) : null}
        {page === "users" ? (
          <div style={{ position: "relative" }}>
            <select
              value={tagFilter}
              onChange={(e) => setTagFilter(e.target.value)}
              style={{ appearance: "none", WebkitAppearance: "none", border: `1px solid ${color.border}`, borderRadius: radius.md, background: "#fff", padding: "6px 28px 6px 10px", fontSize: 12, color: tagFilter ? color.textStrong : color.textSubtle, cursor: "pointer", outline: "none" }}
            >
              <option value="">全部标签</option>
              {userTags.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <Icon name="chevron-down" size={13} color={color.textSubtle} style={{ position: "absolute", right: 9, top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }} />
          </div>
        ) : null}
        {page === "users" && table?.primary ? (
          <Button icon={table.primary.icon} onClick={() => setWlAddOpen(true)}>{table.primary.label}</Button>
        ) : null}
      </header>

      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: 24 }}>
        <div style={{ maxWidth: 1080, margin: "0 auto" }}>
          {/* 错误横幅在 table 判空**之外**：加载失败时 table 为 null，放里面就永远显示不出来。 */}
          {actionErr ? (
            <div style={{ display: "flex", alignItems: "center", gap: 8, background: "#fdecec", border: "1px solid #f5c2c0", borderRadius: radius.lg, padding: "9px 13px", marginBottom: 12, fontSize: 12, color: color.dangerText }}>
              <Icon name="alert-triangle" size={14} color={color.dangerText} />{actionErr}
            </div>
          ) : null}
          {/* 成功提示（平台资产上传/注册/删除）：同名收敛清掉了几条旧记录必须显式说，否则管理员
              只会看到"列表少了一行"而不知为何。 */}
          {actionOk ? (
            <div style={{ display: "flex", alignItems: "center", gap: 8, background: "#eaf7ee", border: "1px solid #b7e0c2", borderRadius: radius.lg, padding: "9px 13px", marginBottom: 12, fontSize: 12, color: color.goodText }}>
              <Icon name="circle-check" size={14} color={color.goodText} />{actionOk}
            </div>
          ) : null}
          {isTable && table ? (
            <>
              {page === "templates" && tplDrill && !mcpDrill ? (
                <div style={{ display: "flex", alignItems: "flex-start", gap: 10, background: color.brandTintBg, border: "1px solid rgba(22,131,255,.18)", borderRadius: radius.lg, padding: "11px 14px", marginBottom: 14, fontSize: 12, color: color.brandStrong, lineHeight: 1.6 }}>
                  <Icon name="info-circle" size={15} color={color.brand} />
                  <span>模板「{tplDrill.name}」的资产治理——此处治理该模板引用的平台资产；<b>Tool 标注为全局配置</b>，修改将影响引用同一 tool 的所有模板（30.6 拍板②）。</span>
                </div>
              ) : null}
              {tabs ? (
                <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
                  {tabs.map((tb) => (
                    <span key={tb.key}
                      style={{ fontSize: 12.5, fontWeight: 600, padding: "6px 14px", borderRadius: radius.md, color: color.textNav, background: "#eceef2" }}>
                      {tb.label}
                    </span>
                  ))}
                </div>
              ) : null}
              <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, overflow: "hidden" }}>
                <div style={{ display: "grid", gridTemplateColumns: gridCols, gap: 12, padding: "11px 16px", background: color.surfaceAlt, borderBottom: `1px solid ${color.border}`, fontSize: 11.5, fontWeight: 700, color: color.textSubtle }}>
                  {table.cols.map((c, i) => <div key={i} style={{ textAlign: i === table.cols.length - 1 && c.width ? "right" : "left" }}>{c.label}</div>)}
                </div>
                {table.rows.map((r, ri) => (
                  <div key={r.id} style={{ display: "grid", gridTemplateColumns: gridCols, gap: 12, padding: "12px 16px", alignItems: "center", borderTop: ri ? `1px solid ${color.borderFaint}` : "none" }}>
                    {r.cells.map((cell, ci) => (
                      <div key={ci} style={{ textAlign: ci === r.cells.length - 1 && table.cols[ci]?.width ? "right" : "left", minWidth: 0 }}>
                        {cell.kind === "badge" ? (
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11.5, fontWeight: 600, color: toneColor[cell.tone ?? "neutral"].text, background: toneColor[cell.tone ?? "neutral"].bg, border: `1px solid ${toneColor[cell.tone ?? "neutral"].border}`, padding: "3px 9px", borderRadius: radius.pill }}>
                            <Dot tone={cell.tone ?? "neutral"} />{cell.text}
                          </span>
                        ) : cell.kind === "action" ? (
                          <span onClick={() => onCellAction(cell.onClickKey, r.id, String(r.cells[0]?.text ?? ""))} title={cell.text} style={{ fontSize: 12, color: color.brand, fontWeight: 600, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4, minWidth: 0, maxWidth: "100%" }}>
                            {/* 长文案（如 32 位告警编号）收进省略号，别压住相邻列 */}
                            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{cell.text}</span>
                            <Icon name="chevron-right" size={13} color={color.brand} />
                          </span>
                        ) : (
                          /* wrap 单元格（如平台 MCP 的 endpoint）折行展示全量：长 URL 被省略号截掉
                             管理员就核对不了地址。其余仍是 nowrap+省略号，并挂 title 便于悬停看全。 */
                          <span title={cell.text} style={{ fontSize: 12.5, color: color.textStrong, fontFamily: cell.mono ? "ui-monospace, monospace" : undefined, display: "block", ...(cell.wrap ? { whiteSpace: "normal", overflowWrap: "anywhere", lineHeight: 1.5 } : { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }) }}>{cell.text}</span>
                        )}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
              {/* 服务端分页（Skill 基线）：后端返 total 才渲染；platform 过滤已在服务端，不再客户端 filter */}
              {table.total !== undefined ? (
                <Pagination page={table.page ?? tablePage} pageSize={table.pageSize ?? ADMIN_PAGE_SIZE}
                  total={table.total} onPage={setTablePage} />
              ) : null}
            </>
          ) : null}

          {page === "alerts-config" ? <AlertOpsPanel /> : null}
          {page === "alerts-config" ? <div style={{ height: 14 }} /> : null}
          {page === "alerts-config" ? <AlertPoolPanel /> : null}

          {page === "sandbox" ? <SandboxPanel /> : null}

          {page === STUDIO_ADMIN_PAGE.key ? STUDIO_ADMIN_PAGE.render() : null}

          {page === "audit" ? (
            <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "18px 20px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
                <div style={{ fontSize: 14, fontWeight: 700 }}>{traceFilter ? "Trace 串联回放" : "回放时间线 · 最近事件"}</div>
                <div style={{ flex: 1 }} />
                <input
                  placeholder="按 audit_trace_id 过滤（或点击事件的 trace 徽标）"
                  value={traceFilter}
                  onChange={(e) => setTraceFilter(e.target.value.trim())}
                  style={{ width: 320, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: "6px 10px", fontSize: 12, fontFamily: "ui-monospace, monospace" }}
                />
                {traceFilter ? (
                  <span onClick={() => setTraceFilter("")} style={{ fontSize: 12, color: color.brand, fontWeight: 600, cursor: "pointer" }}>清除过滤</span>
                ) : null}
              </div>
              {audit.map((n, i) => (
                <div key={i} style={{ display: "grid", gridTemplateColumns: "18px 1fr", gap: 10, paddingBottom: 12 }}>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
                    <div style={{ width: 10, height: 10, borderRadius: "50%", background: color.brand, marginTop: 3 }} />
                    {i < audit.length - 1 ? <div style={{ width: 2, flex: 1, marginTop: 3, background: color.border }} /> : null}
                  </div>
                  <div style={{ paddingBottom: 2 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontSize: 12.5, fontWeight: 600, color: color.textStrong, fontFamily: "ui-monospace, monospace" }}>{n.event}</span>
                      {n.trace ? (
                        <span onClick={() => setTraceFilter(n.trace!)} title={`按此 Trace 串联：${n.trace}`}
                          style={{ fontSize: 10.5, fontFamily: "ui-monospace, monospace", color: color.brandStrong, background: color.brandTintBg, padding: "1px 7px", borderRadius: radius.pill, cursor: "pointer" }}>
                          {n.trace.slice(0, 8)}
                        </span>
                      ) : null}
                    </div>
                    <div style={{ fontSize: 11.5, color: color.textSubtle, marginTop: 2 }}>{n.detail}</div>
                  </div>
                </div>
              ))}
              <div style={{ marginTop: 6, fontSize: 11.5, color: color.textSubtle }}>脱敏铁律：不显 Cookie / Secret / token / prompt 全文，只显 hash / request_id。</div>
            </div>
          ) : null}
        </div>
      </div>

      <ToolAnnotationSlideIn
        open={!!annotRow}
        row={annotRow}
        onClose={() => setAnnotRow(null)}
        onSaved={() => { setAnnotRow(null); void load(); }}
      />
      {registerOpen ? <RegisterModelDialog onClose={() => setRegisterOpen(false)} onSaved={() => { setRegisterOpen(false); void load(); }} /> : null}
      {/* 编辑态复用同一弹窗（key 强制按行重挂，换行编辑时表单不残留上一行的值） */}
      {maEdit ? <RegisterModelDialog key={maEdit.model_asset_id} editing={maEdit}
        onClose={() => setMaEdit(null)} onSaved={() => { setMaEdit(null); void load(); }} /> : null}
      {grantsFor ? <ModelGrantsDialog target={grantsFor} onClose={() => setGrantsFor(null)} onSaved={() => { setGrantsFor(null); void load(); }} /> : null}
      <TemplateEditorModal open={!!tplEdit} templateId={tplEdit} onClose={() => setTplEdit(null)} onChanged={() => void load()} />
      {mtEdit ? <ModelTemplateDialog target={mtEdit.id} onClose={() => setMtEdit(null)} onSaved={() => { setMtEdit(null); void load(); }} /> : null}
      {wlAddOpen ? <AddWhitelistDialog onClose={() => setWlAddOpen(false)} onSaved={() => { setWlAddOpen(false); void load(); }} /> : null}
      {assetDialog ? (
        <PlatformAssetDialog kind={assetDialog} onClose={() => setAssetDialog(null)}
          onSaved={(msg) => { setAssetDialog(null); setActionErr(""); setActionOk(msg); void load(); }} />
      ) : null}
    </>
  );
}

/** 加入白名单弹窗（B7·三）：user_id 必填；不存在的用户会自动建行（角色默认 user）。 */
function AddWhitelistDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [userId, setUserId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [tagsText, setTagsText] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = () => {
    if (!userId.trim()) { setErr("user_id 必填（工号，如 0026xxxx）"); return; }
    setBusy(true);
    // 留空传 undefined（后端不动已有标签）——重复加白不冲已设标签；填了才整体设置
    const tags = tagsText.split(/[,，、\s]+/).map((t) => t.trim()).filter(Boolean);
    api.adminAddWhitelist(userId.trim(), displayName.trim(), tags.length ? tags : undefined)
      .then(onSaved)
      .catch((e) => setErr((e as Error).message))
      .finally(() => setBusy(false));
  };
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(21,25,35,.4)", zIndex: 60, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 380, background: "#fff", borderRadius: radius.xl, padding: 20, display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>加入白名单</div>
        <input placeholder="user_id（工号，必填）" value={userId} onChange={(e) => setUserId(e.target.value)}
          style={{ border: `1px solid ${color.border}`, borderRadius: radius.md, padding: "8px 10px", fontSize: 12.5, fontFamily: "ui-monospace, monospace" }} />
        <input placeholder="展示名（选填，缺省用 user_id）" value={displayName} onChange={(e) => setDisplayName(e.target.value)}
          style={{ border: `1px solid ${color.border}`, borderRadius: radius.md, padding: "8px 10px", fontSize: 12.5 }} />
        <input placeholder="标签（选填，逗号分隔，如 财经,研发）" value={tagsText} onChange={(e) => setTagsText(e.target.value)}
          style={{ border: `1px solid ${color.border}`, borderRadius: radius.md, padding: "8px 10px", fontSize: 12.5 }} />
        {err ? <div style={{ fontSize: 12, color: color.dangerText }}>{err}</div> : null}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
          <button onClick={onClose} style={{ height: 32, padding: "0 13px", border: `1px solid ${color.border}`, background: "#fff", borderRadius: radius.md, fontSize: 12, fontWeight: 600, color: color.textNav, cursor: "pointer" }}>取消</button>
          <button onClick={submit} disabled={busy} style={{ height: 32, padding: "0 14px", border: "none", background: color.brand, color: "#fff", borderRadius: radius.md, fontSize: 12, fontWeight: 700, cursor: "pointer", opacity: busy ? 0.7 : 1 }}>加入</button>
        </div>
      </div>
    </div>
  );
}

/** 注册 / 编辑模型接口（30.6 五）：display_name / model_id / base_url / secret_env_var / 协议 /
 *  上下文长度 / 自定义 Header。38.1：授权范围已迁「模型模板」维度，注册不再选 scope。
 *  存前须「测试连接」通过（Key 走服务器环境变量探测）；走平台网关的模型（不填 base_url）可跳过测试直接存。
 *
 *  传 `editing` 即编辑态（PUT /admin/model-assets/{id}，PATCH 语义只提交改动键）：
 *  model_id 是实例 overlay.platform_model_id 的绑定键，编辑态锁定不可改（后端白名单里也没有它）。 */
function RegisterModelDialog({ onClose, onSaved, editing }: {
  onClose: () => void; onSaved: () => void; editing?: AdminModelAssetOption;
}) {
  const [f, setF] = useState({
    display_name: editing?.display_name ?? "", model_id: editing?.model_id ?? "",
    base_url: editing?.base_url ?? "", secret_env_var: editing?.secret_env_var ?? "",
  });
  const [contextWindow, setContextWindow] = useState(editing?.context_window_tokens || DEFAULT_CONTEXT_WINDOW);
  const [headerRows, setHeaderRows] = useState<HeaderRow[]>(
    Object.entries(editing?.extra_headers ?? {}).map(([name, value]) => ({ name, value })),
  );
  const [err, setErr] = useState("");
  const test = useConnTest();
  // 探测相关字段（base_url/model_id/secret_env_var/自定义 Header）一改即让上次测试失效——
  // header 参与探测，不 reset 就会出现「改了头还挂着旧绿勾」
  const setField = (key: keyof typeof f, val: string) => {
    setF((d) => ({ ...d, [key]: val }));
    if (key === "base_url" || key === "model_id" || key === "secret_env_var") test.reset();
  };
  const onHeaders = (rows: HeaderRow[]) => { setHeaderRows(rows); test.reset(); };
  const input = (key: keyof typeof f, placeholder: string, mono = false) => (
    <input value={f[key]} onChange={(e) => setField(key, e.target.value)} placeholder={placeholder}
      style={{ width: "100%", height: 36, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, padding: "0 11px", fontSize: 12.5, outline: "none", fontFamily: mono ? "ui-monospace, monospace" : undefined, boxSizing: "border-box" }} />
  );

  const fieldsOk = Boolean(f.display_name.trim() && f.model_id.trim() && contextWindow > 0);
  const hasBaseUrl = Boolean(f.base_url.trim());
  // 有 base_url 必须测通才能存；走网关（不填 base_url）无端点可测，允许直接存
  const canSave = fieldsOk && (test.state === "ok" || !hasBaseUrl);

  const runTest = () => {
    if (!hasBaseUrl || !f.model_id.trim() || test.state === "testing") return;
    void test.run(() => api.testModelAssetConnection({
      base_url: f.base_url.trim(), model_id: f.model_id.trim(),
      secret_env_var: f.secret_env_var.trim(), extra_headers: headersToRecord(headerRows),
    }));
  };
  const save = () => {
    const env = f.secret_env_var.trim();
    if (env && !/^[A-Z][A-Z0-9_]{2,63}$/.test(env)) {
      setErr("「API Key 环境变量名」应填变量名（大写字母/数字/下划线，如 OPENOPS_PLATFORM_GLM_API_KEY）——看起来填入了 Key 本身：Key 不落库，请配到后端环境变量后在此填变量名");
      return;
    }
    const done = () => onSaved();
    const fail = (e: unknown) => setErr((e as Error).message);
    if (editing) {
      // PATCH 语义：只提交改动键。base_url/secret_env_var 清空要显式传 null（后端靠 exclude_unset
      // 区分「没传」与「传 null」），传 undefined 会被 JSON 丢掉、等同没传。
      api.adminUpdateModelAsset(editing.model_asset_id, {
        ...(f.display_name.trim() !== editing.display_name ? { display_name: f.display_name.trim() } : {}),
        ...(f.base_url.trim() !== editing.base_url ? { base_url: f.base_url.trim() || null } : {}),
        ...(env !== editing.secret_env_var ? { secret_env_var: env || null } : {}),
        ...(contextWindow !== editing.context_window_tokens ? { context_window_tokens: contextWindow } : {}),
        ...(JSON.stringify(headersToRecord(headerRows)) !== JSON.stringify(editing.extra_headers)
          ? { extra_headers: headersToRecord(headerRows) } : {}),
      }).then(done).catch(fail);
      return;
    }
    api.adminRegisterModel({
      display_name: f.display_name.trim(), model_id: f.model_id.trim(),
      base_url: f.base_url.trim() || undefined, secret_env_var: env || undefined,
      context_window_tokens: contextWindow, extra_headers: headersToRecord(headerRows),
    }).then(done).catch(fail);
  };
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 60, background: "rgba(20,24,31,.42)", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 460, background: "#fff", borderRadius: radius.modal, padding: "22px 22px 18px" }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 14 }}>{editing ? "编辑模型接口" : "注册模型接口"}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {input("display_name", "展示名，如：交易大模型-TX")}
          {editing ? (
            /* model_id 是实例 overlay.platform_model_id 的绑定键：改了会让已绑定实例静默失配，编辑态锁定 */
            <div title="model_id 是绑定键，不可修改" style={{ height: 36, display: "flex", alignItems: "center", padding: "0 11px", fontSize: 12.5, color: color.textSubtle, background: color.neutralBg, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, boxSizing: "border-box", fontFamily: "ui-monospace, monospace" }}>
              {f.model_id}<span style={{ marginLeft: 8, fontFamily: "inherit", fontSize: 11.5 }}>（model_id 不可改）</span>
            </div>
          ) : input("model_id", "model_id，如：tx-llm-v2", true)}
          <div style={{ display: "flex", gap: 10 }}>
            <div style={{ flex: 1 }}>
              <div style={{ height: 36, display: "flex", alignItems: "center", padding: "0 11px", fontSize: 12.5, color: color.textNav, background: color.neutralBg, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, boxSizing: "border-box" }}>接口协议：{PROTOCOL_LABEL}</div>
            </div>
            <div style={{ flex: 1 }}>
              <input type="number" min={1} step={1000} value={contextWindow} onChange={(e) => setContextWindow(Number(e.target.value) || 0)} placeholder="上下文长度，默认 128000"
                style={{ width: "100%", height: 36, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, padding: "0 11px", fontSize: 12.5, outline: "none", boxSizing: "border-box" }} />
            </div>
          </div>
          {input("base_url", "OpenAI 兼容 endpoint（可空，用平台网关时）", true)}
          {input("secret_env_var", "API Key 环境变量名，如 OPENOPS_PLATFORM_GLM_API_KEY", true)}
          <div style={{ fontSize: 11.5, color: color.textSubtle, lineHeight: 1.5, marginTop: -4 }}>
            ⚠ 此处填<b>环境变量名</b>，不是 Key 本身——真实 Key 配在后端进程环境变量（run-backend 里
            <span style={{ fontFamily: "ui-monospace, monospace" }}> export 变量名=Key</span>），绝不落库。填了 base_url 须「测试连接」通过（Key 从环境变量取）才能{editing ? "保存" : "注册"}。
          </div>
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: color.textSubtle, marginBottom: 5 }}>自定义 Header（可选）</div>
            <HeadersEditor rows={headerRows} onChange={onHeaders} />
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
          <Button variant="secondary" icon={test.state === "testing" ? "loader-2" : "plug-connected"}
            disabled={!hasBaseUrl || !f.model_id.trim() || test.state === "testing"} onClick={runTest}>测试连接</Button>
          <ConnTestResult state={test.state} reason={test.reason} mock={test.mock} />
        </div>
        {err ? <div style={{ fontSize: 12, color: color.dangerText, marginTop: 10 }}>{err}</div> : null}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
          <Button variant="secondary" onClick={onClose}>取消</Button>
          <Button disabled={!canSave} onClick={save}
            title={!canSave && fieldsOk && hasBaseUrl ? "请先测试连接通过" : undefined}>{editing ? "保存" : "注册"}</Button>
        </div>
      </div>
    </div>
  );
}

/** 模板白名单授权弹窗（38.1：授权在模型模板维度；30.6 五·人员勾选版，按人不按部门口径沿用）。 */
function ModelGrantsDialog({ target, onClose, onSaved }: {
  target: { id: string; name: string }; onClose: () => void; onSaved: () => void;
}) {
  const [scope, setScope] = useState<"all" | "restricted">("all");
  const [users, setUsers] = useState<{ user_id: string; display_name: string }[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [err, setErr] = useState("");
  useEffect(() => {
    api.adminGetModelTemplateGrants(target.id).then((g) => { setScope(g.access_scope as "all" | "restricted"); setPicked(new Set(g.user_ids)); });
    api.adminListUsers().then(setUsers);
  }, [target.id]);
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 60, background: "rgba(20,24,31,.42)", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 480, background: "#fff", borderRadius: radius.modal, padding: "22px 22px 18px" }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>白名单授权 · {target.name}</div>
        <div style={{ fontSize: 12, color: color.textSubtle, marginBottom: 14, lineHeight: 1.6 }}>受限模板通常只对指定人员开放。设置后，仅授权范围内的用户可在初始化 / 编辑 Agent 时看到并选用本模板；已绑定用户被撤销后，下次任务自动回退平台默认模型。</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 13 }}>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
            <input type="radio" checked={scope === "all"} onChange={() => setScope("all")} />
            全员开放 <span style={{ fontSize: 11.5, color: color.textSubtle }}>所有白名单用户可选用</span>
          </label>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
            <input type="radio" checked={scope === "restricted"} onChange={() => setScope("restricted")} />
            限定人员 <span style={{ fontSize: 11.5, color: color.textSubtle }}>仅勾选用户可选用</span>
          </label>
        </div>
        {scope === "restricted" ? (
          <div style={{ marginTop: 12, border: `1px solid ${color.border}`, borderRadius: radius.lg, maxHeight: 200, overflowY: "auto", padding: "6px 4px" }}>
            {users.map((u) => (
              <label key={u.user_id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 10px", fontSize: 12.5, cursor: "pointer" }}>
                <input type="checkbox" checked={picked.has(u.user_id)}
                  onChange={(e) => setPicked((p) => { const n = new Set(p); e.target.checked ? n.add(u.user_id) : n.delete(u.user_id); return n; })} />
                <span style={{ fontFamily: "ui-monospace, monospace" }}>{u.user_id}</span>
                <span style={{ color: color.textSubtle }}>{u.display_name}</span>
              </label>
            ))}
          </div>
        ) : null}
        {err ? <div style={{ fontSize: 12, color: color.dangerText, marginTop: 10 }}>{err}</div> : null}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
          <Button variant="secondary" onClick={onClose}>取消</Button>
          <Button onClick={() => {
            api.adminSaveModelTemplateGrants(target.id, scope, scope === "all" ? [] : [...picked])
              .then(onSaved).catch((e) => setErr((e as Error).message));
          }}>保存授权</Button>
        </div>
      </div>
    </div>
  );
}

/** 分池并发（§5.1 两层闸的任务级，2026-08-15 落地）：交互硬保底 + 告警硬保底 + 弹性共享区。
 * 三键存 sandbox 域（交互准入在 core 读自己域），本卡只是 UI 聚合——编辑走 saveSandboxCfg
 * 同一套 reason+审计。弹性=total−两保底，派生只读。 */
const POOL_KEYS: Record<string, string> = {
  model_total_concurrency: "任务级并发总额 M",
  reserve_interactive: "交互硬保底（告警永远抢不走）",
  reserve_alert: "告警硬保底（对话高峰也保留）",
};

function AlertPoolPanel() {
  const [cfg, setCfg] = useState<Record<string, string> | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  const [saved, setSaved] = useState(false);

  const load = useCallback(() => {
    api.getSandboxCfg().then((rows) => {
      const picked: Record<string, string> = {};
      for (const r of rows) if (r.key in POOL_KEYS) picked[r.key] = r.val;
      setCfg(picked);
    }).catch((e) => setErr((e as Error).message));
  }, []);
  useEffect(load, [load]);

  if (!cfg) {
    return <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "18px 20px", fontSize: 12.5, color: err ? color.dangerText : color.textSubtle }}>{err || "加载中…"}</div>;
  }
  const num = (v: string | undefined, dflt: number) => { const n = Number(v); return Number.isFinite(n) && v !== undefined && v !== "" ? n : dflt; };
  const view = editing ? draft : cfg;
  const total = num(view.model_total_concurrency, 12);
  const ri = num(view.reserve_interactive, 5);
  const ra = num(view.reserve_alert, 2);
  const elastic = Math.max(0, total - ri - ra);

  const startEdit = () => { setDraft({ ...cfg }); setEditing(true); setSaved(false); setErr(""); };
  const cancel = () => { setEditing(false); setReason(""); setErr(""); };
  const confirm = async () => {
    if (!reason.trim()) { setErr("请填写变更原因——配置修改必须写审计。"); return; }
    const updates: Record<string, unknown> = {};
    for (const k of Object.keys(POOL_KEYS)) if (draft[k] !== cfg[k]) updates[k] = draft[k];
    if (!Object.keys(updates).length) { setErr("没有改动。"); return; }
    try {
      await api.saveSandboxCfg(updates, reason.trim());
      setEditing(false); setReason(""); setErr(""); setSaved(true);
      load();
    } catch (e) { setErr((e as Error).message); }
  };

  return (
    <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "18px 20px" }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, marginBottom: 6 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>分池并发（交互 / 告警）</div>
          <div style={{ fontSize: 12, color: color.textSubtle }}>
            两池各留硬保底、超出共抢弹性区；保存即生效。运营口径：交互上限 {ri}+{elastic}={ri + elastic}、告警上限 {ra}+{elastic}={ra + elastic}；夜间告警最多 {ra + elastic}（交互 {ri} 恒留给值班）。
          </div>
        </div>
        {!editing ? (
          <Button variant="secondary" icon="pencil" onClick={startEdit} style={{ fontSize: 12.5, padding: "8px 15px" }}>编辑</Button>
        ) : (
          <Pill tone="warning">编辑中</Pill>
        )}
      </div>
      {saved ? <div style={{ fontSize: 12, color: color.goodText, marginBottom: 8 }}>已保存并生效。</div> : null}
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {Object.keys(POOL_KEYS).map((k) => (
          <div key={k} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px" }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12.5, fontWeight: 600 }}>{POOL_KEYS[k]}</div>
              <div style={{ fontSize: 11, color: color.textFaint, fontFamily: "ui-monospace, monospace" }}>{k}</div>
            </div>
            {editing ? (
              <input type="number" value={draft[k] ?? ""} min={0}
                     onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))}
                     style={{ width: 110, height: 30, border: `1px solid ${color.border}`, borderRadius: radius.sm, fontSize: 12.5, padding: "0 8px", textAlign: "right" }} />
            ) : (
              <span style={{ fontSize: 13, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{cfg[k] ?? "—"}</span>
            )}
          </div>
        ))}
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", background: color.pageBg, borderRadius: radius.md }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600 }}>弹性共享区（派生只读）</div>
            <div style={{ fontSize: 11, color: color.textFaint, fontFamily: "ui-monospace, monospace" }}>= M − 交互保底 − 告警保底</div>
          </div>
          <span style={{ fontSize: 13, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{elastic}</span>
        </div>
      </div>
      {editing ? (
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 14, paddingTop: 14, borderTop: `1px solid ${color.border}` }}>
          <input value={reason} placeholder="变更原因（必填，写审计）" onChange={(e) => setReason(e.target.value)}
                 style={{ flex: 1, height: 32, border: `1px solid ${color.border}`, borderRadius: radius.sm, fontSize: 12.5, padding: "0 10px" }} />
          <Button variant="secondary" onClick={cancel} style={{ fontSize: 12.5, padding: "8px 14px" }}>取消</Button>
          <Button onClick={() => void confirm()} style={{ fontSize: 12.5, padding: "8px 14px" }}>确认生效</Button>
        </div>
      ) : null}
      {err && cfg ? <div style={{ fontSize: 12, color: color.dangerText, marginTop: 10 }}>{err}</div> : null}
    </div>
  );
}

/** 告警接管运行参数（2026-08-14）：只读 → 编辑 → reason 必填 → config:update 写审计，
 * 保存即生效（消费/派发循环每轮重读配置，免重启）。env 钉死键（OPENOPS_<键大写>）
 * 禁用输入——env 恒盖 DB，UI 改了「保存成功但回显不变」会像 bug。 */
const ALERT_KNOB_LABELS: Record<string, string> = {
  alert_enabled: "告警接管总开关",
  alert_max_concurrent_diagnosis: "诊断并发上限",
  alert_queue_max: "全局排队上限",
  alert_max_open_incidents_per_instance: "单实例未完结上限",
  alert_dedup_window_s: "同指纹去重窗口（秒）",
  alert_group_cooldown_s: "同组冷却（秒）",
  alert_diagnosis_timeout_s: "单次诊断超时（秒）",
  alert_max_retries: "失败重试上限",
  alert_retry_backoff_s: "重试退避（秒）",
  alert_requeue_max_age_s: "重启补诊陈旧上限（秒）",
  alert_run_idle_ttl_minutes: "告警会话空闲回收（分钟，0=跟随平台）",
  alert_pull_batch_limit: "Kafka 单批消费上限",
  alert_queue_max_age_s: "排队超时上限（秒，超过翻「接管失败」）",
  alert_aging_minutes: "老化步长（分钟：排队每满 N 分钟升一档防饿死；1440≈关）",
  alert_stale_message_age_s: "陈旧放弃阈值（秒：消费延迟超此值留痕不诊断，清单显「延迟放弃」；0=关）",
};

function AlertOpsPanel() {
  const [data, setData] = useState<AlertOpsConfig | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  const [saved, setSaved] = useState(false);

  const load = useCallback(() => {
    api.getAlertOpsConfig().then(setData).catch((e) => setErr((e as Error).message));
  }, []);
  useEffect(load, [load]);

  if (!data) {
    return <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "18px 20px", fontSize: 12.5, color: err ? color.dangerText : color.textSubtle }}>{err || "加载中…"}</div>;
  }
  const keys = Object.keys(ALERT_KNOB_LABELS).filter((k) => k in data.config);
  const locked = new Set(data.env_locked);

  const startEdit = () => {
    setDraft(Object.fromEntries(keys.map((k) => [k, String(data.config[k])])));
    setEditing(true); setSaved(false); setErr("");
  };
  const cancel = () => { setEditing(false); setReason(""); setErr(""); };
  const confirm = async () => {
    if (!reason.trim()) { setErr("请填写变更原因——配置修改必须写审计。"); return; }
    const updates: Record<string, unknown> = {};
    for (const k of keys) {
      if (locked.has(k) || draft[k] === String(data.config[k])) continue;
      updates[k] = k === "alert_enabled" ? draft[k] === "true" : Number(draft[k]);
    }
    if (!Object.keys(updates).length) { setErr("没有改动。"); return; }
    try {
      await api.saveAlertOpsConfig(updates, reason.trim());
      setEditing(false); setReason(""); setErr(""); setSaved(true);
      load();
    } catch (e) { setErr((e as Error).message); }
  };

  return (
    <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "18px 20px" }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>运行参数</div>
          <div style={{ fontSize: 12, color: color.textSubtle }}>修改需填写 reason 并写审计；保存即生效（消费/派发循环每轮重读）。标「环境锁定」的键由部署环境变量钉死，请改 env 后重启。</div>
        </div>
        {!editing ? (
          <Button variant="secondary" icon="pencil" onClick={startEdit} style={{ fontSize: 12.5, padding: "8px 15px" }}>编辑</Button>
        ) : (
          <Pill tone="warning">编辑中</Pill>
        )}
      </div>
      {saved ? <div style={{ fontSize: 12, color: color.goodText, marginBottom: 10 }}>已保存并生效。</div> : null}
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {keys.map((k) => {
          const isLocked = locked.has(k);
          const bound = data.bounds[k];
          return (
            <div key={k} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", borderRadius: radius.md, background: isLocked ? color.pageBg : "transparent" }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12.5, fontWeight: 600 }}>{ALERT_KNOB_LABELS[k]}</div>
                <div style={{ fontSize: 11, color: color.textFaint, fontFamily: "ui-monospace, monospace" }}>
                  {k}{bound ? `　[${bound[0]} ~ ${bound[1]}]` : ""}
                </div>
              </div>
              {isLocked ? <Pill tone="neutral" icon="lock">环境锁定</Pill> : null}
              {!editing || isLocked ? (
                <span style={{ fontSize: 13, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
                  {k === "alert_enabled" ? (data.config[k] ? "开" : "关") : String(data.config[k])}
                </span>
              ) : k === "alert_enabled" ? (
                <select value={draft[k]} onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))}
                        style={{ height: 30, border: `1px solid ${color.border}`, borderRadius: radius.sm, fontSize: 12.5, padding: "0 6px" }}>
                  <option value="true">开</option>
                  <option value="false">关</option>
                </select>
              ) : (
                <input type="number" value={draft[k]} min={bound?.[0]} max={bound?.[1]}
                       onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))}
                       style={{ width: 110, height: 30, border: `1px solid ${color.border}`, borderRadius: radius.sm, fontSize: 12.5, padding: "0 8px", textAlign: "right" }} />
              )}
            </div>
          );
        })}
      </div>
      {editing ? (
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 14, paddingTop: 14, borderTop: `1px solid ${color.border}` }}>
          <input value={reason} placeholder="变更原因（必填，写审计）" onChange={(e) => setReason(e.target.value)}
                 style={{ flex: 1, height: 32, border: `1px solid ${color.border}`, borderRadius: radius.sm, fontSize: 12.5, padding: "0 10px" }} />
          <Button variant="secondary" onClick={cancel} style={{ fontSize: 12.5, padding: "8px 14px" }}>取消</Button>
          <Button onClick={() => void confirm()} style={{ fontSize: 12.5, padding: "8px 14px" }}>确认生效</Button>
        </div>
      ) : null}
      {err ? <div style={{ fontSize: 12, color: color.dangerText, marginTop: 10 }}>{err}</div> : null}
    </div>
  );
}

/** 新原型：沙箱可调配置——只读 → 编辑中 → reason 必填 → 确认生效（写审计 runtime_config.updated）。 */
function SandboxPanel() {
  const [cfg, setCfg] = useState<SandboxCfg[]>([]);
  const [containers, setContainers] = useState<SandboxContainer[]>([]);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  const [saved, setSaved] = useState(false);
  // 销毁反馈独立于配置表单的 err/saved：那两个只在「编辑中」分支渲染，销毁复用它们等于没有反馈
  const [destroyMsg, setDestroyMsg] = useState<{ tone: "good" | "danger"; text: string } | null>(null);

  const load = useCallback(() => {
    api.getSandboxCfg().then(setCfg);
    api.getSandboxContainers().then(setContainers).catch(() => setContainers([]));
  }, []);
  useEffect(load, [load]);

  const destroy = async (userId: string) => {
    const why = window.prompt(`强制销毁用户 ${userId} 的沙箱容器会中断其当前任务。请填写原因：`);
    if (!why || !why.trim()) return;
    setDestroyMsg(null);
    try {
      const r = await api.destroySandboxContainer(userId, why.trim());
      const n = r.cancelled_task_ids.length;
      setDestroyMsg(r.destroyed
        ? { tone: "good", text: `已销毁 ${userId} 的容器${n ? `，并中断 ${n} 个运行中任务` : ""}。` }
        : { tone: "danger", text: `${userId} 的容器已不存在（可能已被回收），无需销毁。` });
    } catch (e) {
      setDestroyMsg({ tone: "danger", text: `销毁失败：${(e as Error).message}` });
    }
    load();  // 失败也刷新：让管理员看到真实现状，而不是停在一行过期数据上
  };

  const startEdit = () => {
    setDraft(Object.fromEntries(cfg.map((c) => [c.key, c.val])));
    setEditing(true);
    setSaved(false);
    setErr("");
  };
  const cancel = () => { setEditing(false); setReason(""); setErr(""); };
  const confirm = async () => {
    if (!reason.trim()) {
      setErr("请填写变更原因——配置修改必须写审计（runtime_config.updated）。");
      return;
    }
    const updates: Record<string, unknown> = {};
    for (const c of cfg) if (draft[c.key] !== c.val) updates[c.key] = draft[c.key];
    try {
      await api.saveSandboxCfg(updates, reason.trim());
      setEditing(false);
      setReason("");
      setErr("");
      setSaved(true);
      load();
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  return (
    <div style={{ background: "#fff", border: `1px solid ${color.border}`, borderRadius: radius.xl, padding: "18px 20px" }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>可调配置</div>
          <div style={{ fontSize: 12, color: color.textSubtle }}>修改需填写 reason 并写审计；影响新建容器与后续容量准入，已存在容器重建后生效。</div>
        </div>
        {!editing ? (
          <Button variant="secondary" icon="pencil" onClick={startEdit} style={{ fontSize: 12.5, padding: "8px 15px" }}>编辑</Button>
        ) : (
          <Pill tone="warning">编辑中</Pill>
        )}
      </div>
      <div style={{ marginBottom: 18 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
          <div style={{ fontSize: 12.5, fontWeight: 700, color: color.textNav }}>
            用户容器（{containers.length} 个占用中 · 会话期常驻）
          </div>
          <button onClick={load} style={{ height: 28, padding: "0 11px", border: `1px solid ${color.border}`, background: "#fff", borderRadius: radius.sm, fontSize: 11.5, cursor: "pointer", color: color.textNav }}>刷新</button>
        </div>
        {destroyMsg ? (
          <div style={{ fontSize: 12, marginBottom: 8, color: destroyMsg.tone === "good" ? color.goodText : color.dangerText }}>
            {destroyMsg.text}
          </div>
        ) : null}
        {containers.length === 0 ? (
          <div style={{ fontSize: 12, color: color.textSubtle, padding: "10px 0" }}>当前无活跃用户容器（用户开启会话时按需创建）。</div>
        ) : (
          <div style={{ border: `1px solid ${color.borderFaint}`, borderRadius: radius.md, overflow: "hidden" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1.3fr 0.6fr 0.7fr 0.7fr 0.6fr 0.9fr 0.6fr", gap: 8, padding: "7px 12px", background: color.neutralBg, fontSize: 11, fontWeight: 700, color: color.textSubtle }}>
              <span>user_id</span><span>状态</span>
              <span title="用户开过、尚未关闭且未被 30 分钟 idle 回收的会话数；关掉浏览器不会减少——不代表在线人数，也不代表并发任务量">打开中的会话</span>
              <span title="当前运行的主任务数，对齐 per_user_running_task_limit 限额（子 Agent 属任务内部，不单独计数）">运行任务</span>
              <span title="名额满后在队列中等待的任务数（不占并发，轮到自动开始）；持续>0 说明对话侧拥堵">排队中</span>
              <span>镜像</span><span>操作</span>
            </div>
            {containers.map((c) => (
              <div key={c.user_id} style={{ display: "grid", gridTemplateColumns: "1.3fr 0.6fr 0.7fr 0.7fr 0.6fr 0.9fr 0.6fr", gap: 8, padding: "8px 12px", borderTop: `1px solid ${color.borderFaint}`, fontSize: 12, alignItems: "center" }}>
                <span style={{ fontFamily: "ui-monospace, monospace" }}>{c.user_id}</span>
                <Pill tone={c.runtime_status === "active" ? "good" : "neutral"}>{c.runtime_status}</Pill>
                <span>{c.active_run_count}</span>
                <span style={{ fontWeight: 600 }}>
                  {c.running_task_count}
                  {typeof c.running_subtask_count === "number" && c.running_subtask_count > 0
                    ? ` (+${c.running_subtask_count} 子)` : ""}
                </span>
                <span style={{ color: c.queued_task_count ? color.warningText : color.textSubtle, fontWeight: c.queued_task_count ? 700 : 400 }}>
                  {c.queued_task_count ?? 0}
                </span>
                <span style={{ fontFamily: "ui-monospace, monospace", fontSize: 11, color: color.textSubtle }}>{c.image_version}</span>
                <button onClick={() => destroy(c.user_id)} style={{ height: 26, padding: "0 9px", border: "1px solid #f3d9d7", background: "#fff", borderRadius: radius.sm, fontSize: 11, cursor: "pointer", color: color.dangerText }}>销毁</button>
              </div>
            ))}
          </div>
        )}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px 20px" }}>
        {cfg.map((c) => (
          <div key={c.key} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, borderBottom: `1px solid ${color.borderFaint}`, padding: "8px 0" }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 12.5, fontWeight: 600, color: color.textStrong, fontFamily: "ui-monospace, monospace" }}>{c.key}</div>
              <div style={{ fontSize: 11, color: color.textSubtle }}>{c.desc}</div>
            </div>
            {editing ? (
              <input
                value={draft[c.key] ?? ""}
                onChange={(e) => setDraft((d) => ({ ...d, [c.key]: e.target.value }))}
                style={{ width: 110, height: 32, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, padding: "0 10px", fontSize: 13, fontWeight: 700, color: color.brandStrong, textAlign: "right", outline: "none" }}
              />
            ) : (
              <span style={{ fontSize: 13, fontWeight: 700, color: color.brandStrong }}>{c.val}</span>
            )}
          </div>
        ))}
      </div>
      {editing ? (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 16, paddingTop: 14, borderTop: `1px solid ${color.borderInner}` }}>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="变更原因（必填，写入审计 runtime_config.updated）"
              style={{ flex: 1, height: 36, border: `1px solid #dfe2e8`, borderRadius: radius.md, padding: "0 12px", fontSize: 12.5, outline: "none" }}
            />
            <Button variant="secondary" onClick={cancel} style={{ fontSize: 12.5, padding: "9px 16px" }}>取消</Button>
            <Button onClick={confirm} style={{ fontSize: 12.5, padding: "9px 18px" }}>确认生效</Button>
          </div>
          {err ? <div style={{ fontSize: 12, color: color.dangerText, marginTop: 8 }}>{err}</div> : null}
        </>
      ) : null}
      {saved ? (
        <div style={{ fontSize: 12, color: color.goodText, fontWeight: 600, marginTop: 12, display: "inline-flex", alignItems: "center", gap: 5 }}>
          <Icon name="circle-check" size={14} color={color.goodText} />配置已生效：影响新建容器与后续容量准入，已写入审计事件 runtime_config.updated。
        </div>
      ) : null}
    </div>
  );
}
