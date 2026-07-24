/** 工具身份复合键（`server::tool`）——后端 domain/tool_key.py 的前端镜像，两处规则必须一致。
 *
 * 背景（2026-07-23 同名冲突根治）：catalog 唯一约束只到 (mcp_version_id, tool_name)，跨 server
 * 同名合法；此前模板勾选/绑定用裸 tool_name 当身份，两家 server 共享同名工具时勾 A 家会把 B 家
 * 的「全选」条件被动满足（勾选联动），运行时标注也互踩。复合键把 server 维度带进身份。 */

export const SEP = "::";

export const toolKey = (server: string, tool: string): string => `${server}${SEP}${tool}`;

export const isComposite = (ref: string): boolean => ref.includes(SEP);

/** 复合键 → {server, tool}；裸名 → {server: null}。从右切（lastIndexOf）：tool_name 是 MCP
 * 协议名（不含 "::"），server 名可含单冒号/中文——右切保 server 段完整。 */
export const parseToolKey = (ref: string): { server: string | null; tool: string } => {
  const i = ref.lastIndexOf(SEP);
  return i < 0 ? { server: null, tool: ref } : { server: ref.slice(0, i), tool: ref.slice(i + SEP.length) };
};

export type ServerGroups = Record<string, { key: string; name: string }[]>;

/** getAdminMcpTools().raw → 按 server 分组（仅 allowed 行），元素带复合键（勾选身份）与裸名（展示）。 */
export function groupCatalog(raw: Record<string, unknown>[]): ServerGroups {
  const g: ServerGroups = {};
  for (const r of raw) {
    if (r.annotation_id != null && r.annotation_status === "allowed") {
      const s = String(r.mcp_display_name);
      (g[s] ??= []).push({ key: toolKey(s, String(r.tool_name)), name: String(r.tool_name) });
    }
  }
  return g;
}

/** 存量选中集读时归一（与后端 template_service._normalize_and_scrub 同规则）：
 * 复合键且在目录 → 保留；裸名 → 展开为全部同名复合键（唯一命中即「升级」，多家命中即全展开——
 * 忠实还原运行时裸名白名单本就放行各家同名工具的语义）；解析不到 → 原样保留（进残留 chip 区，
 * 由管理员手动处置；保存时后端同规则摘除并回传 dropped_tools）。 */
export function normalizeSelection(selected: string[], groups: ServerGroups): {
  keys: string[];                    // 归一后的选中集（复合键 + 目录外残留原样）
  migrated: boolean;                 // 是否发生过裸名→复合键换算（UI 提示「保存后完成迁移」）
  expanded: Record<string, string[]>; // 裸名 → 多家复合键（同名多家被显式展开的名单）
} {
  const known = new Set<string>();
  const byBare = new Map<string, string[]>();
  for (const ts of Object.values(groups)) {
    for (const t of ts) {
      known.add(t.key);
      byBare.set(t.name, [...(byBare.get(t.name) ?? []), t.key]);
    }
  }
  const keys: string[] = [];
  const seen = new Set<string>();
  const push = (k: string) => { if (!seen.has(k)) { seen.add(k); keys.push(k); } };
  const expanded: Record<string, string[]> = {};
  let migrated = false;
  for (const t of selected) {
    if (known.has(t)) { push(t); continue; }
    const hits = !isComposite(t) ? (byBare.get(t) ?? []) : [];
    if (hits.length) {
      migrated = true;
      if (hits.length > 1) expanded[t] = hits;
      hits.forEach(push);
    } else {
      push(t); // 目录外残留：不静默丢数据
    }
  }
  return { keys, migrated, expanded };
}
