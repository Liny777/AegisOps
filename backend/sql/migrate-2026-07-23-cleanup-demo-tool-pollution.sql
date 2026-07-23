-- =====================================================================
-- OpenOps 数据清理：内置 demo 工具污染真实平台 MCP 目录（2026-07-23）
--
-- 现象：每个平台 MCP server 名下都多出 query_resource / recover_execute 两个工具，
--       引发「MCP 工具标注同名冲突」告警（运行时后者赢、管理员标注互踩）与
--       模板编辑器勾选联动（勾 A 家自动勾上 B 家）。
-- 机制：mcp_registry_client.discover_tools 在 mock 模式（OPENOPS_MCPREGISTRY 未设即默认）
--       无视 server_url 恒回内置 _TOOLS；real 模式对占位 endpoint（空 / host=mock）同样回
--       _TOOLS。对账循环把返回按各资产自己的 mcp_version_id 落库、只增不删 → 污染永久残留。
--       代码侧防呆已随本清理同批上线（asset_reconcile_service 方向守卫）。
--
-- 执行顺序（runbook）：
--   1. 先跑【诊断段】（只读）确认污染路径与行清单；
--   2. 部署含对账方向守卫的版本（先堵回写，再清数据）；
--   3. 跑【清理段】（事务 + 备份表）；若诊断为路径 (b) 另跑【endpoint 修复】；
--   4. 触发一次配置页 refresh 对账（POST /assets:reconcile），复跑诊断段确认零回写；
--   5. 验证：管理台各真 server 下无 demo 双工具、冲突告警不再出现、模板编辑勾选不再联动。
--
-- 执行：  psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 \
--            -f migrate-2026-07-23-cleanup-demo-tool-pollution.sql
--        （诊断段默认执行且只读；清理段在同文件下方，幂等可重跑——重跑命中 0 行）
-- 生命周期：一次性数据清理（非 schema 迁移，不收编 core.sql）；内网执行确认后从仓库删除。
--
-- demo 工具指纹（由 mcp_registry_client._TOOLS 经 _schema_hash 算得，改 _TOOLS 需同步复核）：
--   query_resource  → schema_hash = 'd1c5f9fd51063f98'
--   recover_execute → schema_hash = 'd1e06014fbd2c0e5'
-- 保留判据：种子占位资产 display_name = 'oModel 查询与恢复'（mock/本地端到端的 demo 目录
--   唯一合法宿主，不清）；同名但非 demo 指纹的行 = 真 server 自己的真工具，不清。
-- 硬删而非软删：ux_mcp_tool_catalog_name / ux_mcp_tool_annotation_catalog 均为绝对唯一
--   （含软删行）。软删会永久占键——日后真 server 真暴露同名工具时 reconcile INSERT 必撞
--   UniqueViolation、该 server 永远进 tool_sync_errors。备份表承担审计保留职责。
-- =====================================================================

-- ==================== 诊断段（只读，先跑） ====================
-- 全景：各平台 MCP 名下的 demo 同名工具行 + endpoint 形态 + 标注在场情况。
-- 判读：
--   污染行挂 endpoint_kind='real' 资产      → 路径 (a)：某实例曾以 mock 模式连本库跑过对账；
--   污染行挂 endpoint_kind='placeholder' 且 display_name <> 'oModel 查询与恢复'
--                                           → 路径 (b)：该资产 endpoint 为空/mock，real 对账
--                                             走占位短路拿到 _TOOLS（需另做 endpoint 修复）；
--   is_demo_shape = false 的同名行          → 真 server 自己的真工具，清理段不会碰它。
SELECT m.display_name,
       coalesce(m.endpoint_config_json->>'endpoint','')                          AS endpoint,
       CASE WHEN coalesce(m.endpoint_config_json->>'endpoint','') = ''
              OR (m.endpoint_config_json->>'endpoint') ~ '^[a-zA-Z][a-zA-Z0-9+.-]*://mock([:/]|$)'
            THEN 'placeholder' ELSE 'real' END                                   AS endpoint_kind,
       c.tool_name, c.schema_hash,
       (c.schema_hash IN ('d1c5f9fd51063f98','d1e06014fbd2c0e5'))                AS is_demo_shape,
       c.discovered_at, c.deleted_at                                             AS catalog_deleted_at,
       a.annotation_id IS NOT NULL                                               AS has_annotation_row,
       a.deleted_at                                                              AS annotation_deleted_at
FROM sre_mcp_asset m
JOIN sre_mcp_asset_version v ON v.mcp_id = m.mcp_id
JOIN sre_mcp_tool_catalog  c ON c.mcp_version_id = v.mcp_version_id
LEFT JOIN sre_mcp_tool_annotation a ON a.tool_catalog_id = c.tool_catalog_id
WHERE m.source_type = 'platform'
  AND c.tool_name IN ('query_resource','recover_execute')
ORDER BY m.display_name, c.tool_name;

-- 辅证（污染时间点与触发方式：看 tools_created 突增的那轮及其 trigger login/refresh/background）：
-- SELECT creation_date, action, payload_redacted_json
--   FROM sre_audit_event WHERE event_type = 'asset.reconciled'
--  ORDER BY creation_date DESC LIMIT 50;

-- ==================== 清理段（事务 + 幂等备份 + 硬删） ====================
BEGIN;

-- ① 备份（幂等：表已存在则本轮不重建；恢复 = INSERT ... SELECT 回原表）
CREATE TABLE IF NOT EXISTS bak_20260723_demo_tool_catalog AS
  SELECT c.* FROM sre_mcp_tool_catalog c
  JOIN sre_mcp_asset_version v ON v.mcp_version_id = c.mcp_version_id
  JOIN sre_mcp_asset m ON m.mcp_id = v.mcp_id
  WHERE m.source_type = 'platform' AND m.display_name <> 'oModel 查询与恢复'
    AND ((c.tool_name = 'query_resource'  AND c.schema_hash = 'd1c5f9fd51063f98')
      OR (c.tool_name = 'recover_execute' AND c.schema_hash = 'd1e06014fbd2c0e5'));

CREATE TABLE IF NOT EXISTS bak_20260723_demo_tool_annotation AS
  SELECT a.* FROM sre_mcp_tool_annotation a
  WHERE a.tool_catalog_id IN (SELECT tool_catalog_id FROM bak_20260723_demo_tool_catalog);

-- ② 先删标注（含被 sync 软删的历史行——mock 覆写真工具 schema 时软删的原真标注也在此列），
--    再删 catalog。幂等：重跑命中 0 行。
DELETE FROM sre_mcp_tool_annotation a
 WHERE a.tool_catalog_id IN (SELECT tool_catalog_id FROM bak_20260723_demo_tool_catalog);

DELETE FROM sre_mcp_tool_catalog c
 WHERE c.tool_catalog_id IN (SELECT tool_catalog_id FROM bak_20260723_demo_tool_catalog);

COMMIT;

-- 模板 content_json 里对这两个名字的引用**刻意不清**：种子模板 sensai_fast_recovery 本就
-- 引用它们（seed.py TEMPLATE_CONTENT）；四层自愈/fail-closed 已兜住（编辑器「不在目录」
-- 残留 chip 可手动移除、保存/发布 scrub 摘除、运行时未标注 TOOL_NOT_ANNOTATED 拦截），
-- 且真 oModel server 若真暴露同名工具，修好 endpoint 重新对账+标注后引用应继续有效。
-- 审计事件与悬挂审批同样不动：审计是不可变历史；悬挂审批决策时会按最新标注 fail-closed。

-- ==================== endpoint 修复（仅诊断为路径 (b) 时执行） ====================
-- ingest 按 display_name create-if-missing、不回填 endpoint——不修则该 server 目录永不更新。
-- 人工核对 console 注册表（mcps/list/query 的 server_url）后逐条执行：
-- UPDATE sre_mcp_asset
--    SET endpoint_config_json = jsonb_set(coalesce(endpoint_config_json,'{}'::jsonb),
--                                         '{endpoint}', to_jsonb('<真实 server_url>'::text)),
--        last_updated_by = 'ops-cleanup-20260723', last_update_date = now()
--  WHERE source_type = 'platform' AND deleted_at IS NULL AND display_name = '<omodel-mcp-server 等>';

-- ==== 验证（清理后手动跑；期望 0 行）====
-- 复跑顶部诊断段：真实 server 名下不应再出现 is_demo_shape=true 的行。
