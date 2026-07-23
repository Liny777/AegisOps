import assert from "node:assert/strict";
import { test } from "node:test";
import { groupCatalog, isComposite, normalizeSelection, parseToolKey, toolKey } from "./toolBinding";

const raw = (server: string, tool: string, status = "allowed", annotated = true) => ({
  tool_catalog_id: `${server}/${tool}`, tool_name: tool, mcp_display_name: server,
  annotation_id: annotated ? "a1" : null, annotation_status: status,
});

// 内网事故形态：两家 server 各有同名 query_resource/recover_execute + 各一独有工具
const CATALOG = [
  raw("omodel-mcp-server", "query_resource"),
  raw("omodel-mcp-server", "recover_execute"),
  raw("omodel-mcp-server", "query_topology"),
  raw("opsdfx-mcp", "query_resource"),
  raw("opsdfx-mcp", "recover_execute"),
  raw("opsdfx-mcp", "raw_shell", "blocked"),      // 非 allowed 不进目录
  raw("opsdfx-mcp", "unannotated_tool", "allowed", false), // 未标注不进目录
];

test("toolKey/parseToolKey 右切保 server 段完整", () => {
  assert.equal(toolKey("omodel-mcp-server", "query_resource"), "omodel-mcp-server::query_resource");
  assert.deepEqual(parseToolKey("oModel 查询与恢复::recover_execute"),
    { server: "oModel 查询与恢复", tool: "recover_execute" });
  assert.deepEqual(parseToolKey("gw:8080::foo"), { server: "gw:8080", tool: "foo" });
  assert.deepEqual(parseToolKey("bare_tool"), { server: null, tool: "bare_tool" });
  assert.ok(isComposite("a::b") && !isComposite("a:b"));
});

test("groupCatalog：同名工具分入各家、身份键不同；非 allowed/未标注被滤", () => {
  const g = groupCatalog(CATALOG);
  assert.deepEqual(Object.keys(g).sort(), ["omodel-mcp-server", "opsdfx-mcp"]);
  assert.deepEqual(g["opsdfx-mcp"].map((t) => t.name), ["query_resource", "recover_execute"]);
  const omodelKeys = g["omodel-mcp-server"].map((t) => t.key);
  const opsdfxKeys = g["opsdfx-mcp"].map((t) => t.key);
  assert.ok(omodelKeys.every((k) => !opsdfxKeys.includes(k))); // 同名不同键——勾选联动的根治点
});

test("normalizeSelection：复合键幂等、裸名唯一升级、同名多家展开、目录外残留保留", () => {
  const g = groupCatalog(CATALOG);
  const { keys, migrated, expanded } = normalizeSelection(
    ["omodel-mcp-server::query_resource", "query_topology", "query_resource", "ghost_tool"], g);
  assert.deepEqual(keys, [
    "omodel-mcp-server::query_resource",          // 复合键幂等
    "omodel-mcp-server::query_topology",          // 裸名唯一归属 → 升级
    "opsdfx-mcp::query_resource",                 // 裸名多家 → 展开补齐另一家（去重）
    "ghost_tool",                                 // 目录外 → 原样保留（残留 chip）
  ]);
  assert.equal(migrated, true);
  assert.deepEqual(expanded, { query_resource: ["omodel-mcp-server::query_resource", "opsdfx-mcp::query_resource"] });
});

test("normalizeSelection：纯复合键选中集不标记迁移", () => {
  const g = groupCatalog(CATALOG);
  const r = normalizeSelection(["opsdfx-mcp::recover_execute"], g);
  assert.deepEqual(r.keys, ["opsdfx-mcp::recover_execute"]);
  assert.equal(r.migrated, false);
});

test("勾选增删不串家：勾 A 家全部键后，B 家没有任何键被动选中", () => {
  const g = groupCatalog(CATALOG);
  const aKeys = g["omodel-mcp-server"].map((t) => t.key);
  const bKeys = g["opsdfx-mcp"].map((t) => t.key);
  const selected = new Set(aKeys); // 模拟 ServerPicker 勾 A（写入其全部复合键）
  assert.ok(bKeys.every((k) => !selected.has(k)));
  const bFull = bKeys.length > 0 && bKeys.every((k) => selected.has(k));
  assert.equal(bFull, false); // B 家 checked 判定不再被 A 家满足——原 bug 的直接反例
});
