-- =====================================================================
-- OpenOps 增量迁移：用户表加「领域标签」列（2026-07-23）
-- 对应提交：feat(admin) 用户与白名单加「标签」字段（多标签，管理台维护）
--
-- 目标库：已建 27 表的存量库（内网测试/生产）。新建库无需本文件——直接跑
--        openops_v1_core.sql 即含本列。
-- 等效性：与重跑 openops_v1_core.sql 完全等效（该文件已含同列 + COMMENT）。幂等，可重复执行。
-- 执行：  psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 \
--            -f migrate-2026-07-23-user-tags.sql
-- 执行后：增量已收编 core.sql，本文件生命周期结束（照惯例从仓库删除）。
-- =====================================================================

-- 管理台「用户与白名单」的领域标签（如 ["财经","研发"]）：加白弹窗/行内编辑写入；
-- 登录链路（resolve_user 的 upsert）不触碰本列，不会被登录覆盖。NULL=从未设置。
ALTER TABLE sre_openops_user
  ADD COLUMN IF NOT EXISTS tags_json jsonb;

COMMENT ON COLUMN sre_openops_user.tags_json IS '领域标签数组（如 ["财经","研发"]）；管理台维护，登录链路不写';

-- ==== 验证（执行后手动跑；期望：tags_json 在列、可空、无默认）====
-- SELECT column_name, is_nullable, column_default
--   FROM information_schema.columns
--  WHERE table_name = 'sre_openops_user' AND column_name = 'tags_json';
-- 期望输出：tags_json | YES | (null)
