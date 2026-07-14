-- =====================================================================
-- OpenOps 增量迁移：缩短超 30 字符的表名与索引名（2026-07-14）
--
-- 适用：已按旧版 openops_v1_core.sql 建库的存量环境；新建库无需本文件，
--       直接执行更新后的 openops_v1_core.sql。
-- 执行顺序：本迁移 -> openops_v1_core.sql -> 更新后的后端代码。
-- 执行：psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 \
--          -f migrate-2026-07-14-ddl-object-names.sql
--
-- IF EXISTS 保证迁移正常重跑时无操作。若旧、新名称同时存在，RENAME 会
-- 因目标对象已存在而失败；脚本不会自动合并或删除任何数据。
-- =====================================================================

ALTER TABLE IF EXISTS sre_agent_team_template_version
  RENAME TO sre_agent_team_tpl_version;

ALTER INDEX IF EXISTS ix_platform_runtime_config_status
  RENAME TO ix_platform_runtime_cfg_status;
