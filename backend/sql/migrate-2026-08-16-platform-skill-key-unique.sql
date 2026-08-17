-- =====================================================================
-- OpenOps 迁移：平台 skill_key 部分唯一索引 + 存量同 key 重复收敛（2026-08-16）
--
-- 目标库：已建表的**存量库**。新建库无需本文件——直接跑 openops_v1_core.sql 即含本索引。
-- 等效性：与重跑 core.sql 完全等效（core.sql 已同步加入 ux_skill_asset_platform_key）。
-- 幂等：可重复执行（去重段重跑命中 0 行；建索引段 IF NOT EXISTS）。
--
-- 背景：管理台新增「上传平台 Skill」后，本地 sre_skill_asset 需保证同一个逻辑 skill 只有一条
--       活的平台行。两条并存不只是管理台多显示一行——domain/skill_alias.resolve_skill_alias
--       是**精确键优先**，`/foo` 会解析到过期的旧行，run_platform_skill 下载老包。
--       服务层已做同名收敛（asset_admin_service._converge_platform_skills），本索引是并发兜底。
--
-- ⚠ 这是本仓第一条**可能因存量数据而失败**的 DDL：库里若已有两条同 skill_key 的活平台行，
--   直接建索引会报 duplicate key。故本文件**先软删重复、再建索引**，同一事务内完成。
--
-- 执行：  psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 \
--            -f migrate-2026-08-16-platform-skill-key-unique.sql
-- 生命周期：内网执行确认后从仓库删除（core.sql 已收编）。
-- =====================================================================

-- ==================== 预检段（只读，建议先单独跑一遍） ====================
-- 期望 0 行。有行 = 存量已有重复，下方去重段会把每个 key 的**最新一条**留下、其余软删。
-- 跑之前先看清会合并掉什么（尤其确认落败行不是你要留的那条）。
SELECT skill_key, count(*) AS live_rows,
       string_agg(skill_id::text || ' / ' || display_name || ' / ' || creation_date::text,
                  E'\n  ' ORDER BY creation_date DESC) AS rows_detail
  FROM sre_skill_asset
 WHERE source_type = 'platform' AND deleted_at IS NULL
 GROUP BY skill_key
HAVING count(*) > 1;

-- ==================== 收敛 + 建索引（事务） ====================
BEGIN;

-- ① 同 skill_key 的活平台行只保留最新一条，其余**软删**（与服务层同名收敛同口径：
--    软删而非物删，行仍在、绑定行由 list_instance_bindings 的 ghost 检测标注「已删除」）。
--    排序键带 skill_id 做 tiebreaker：creation_date 不唯一（reconcile 批量插入同一时刻多行）。
--    幂等：重跑时已无重复 → 命中 0 行。
UPDATE sre_skill_asset s
   SET deleted_at = now(), status = 'deleted', last_updated_by = 'migration-20260816'
 WHERE s.source_type = 'platform' AND s.deleted_at IS NULL
   AND EXISTS (
     SELECT 1 FROM sre_skill_asset t
      WHERE t.source_type = 'platform' AND t.deleted_at IS NULL
        AND t.skill_key = s.skill_key
        AND (t.creation_date, t.skill_id) > (s.creation_date, s.skill_id)
   );

-- ② 建部分唯一索引（与 core.sql 逐字一致）
CREATE UNIQUE INDEX IF NOT EXISTS ux_skill_asset_platform_key
  ON sre_skill_asset (skill_key)
  WHERE source_type = 'platform' AND deleted_at IS NULL;

COMMIT;

-- 注意：同 display_name 但**不同** skill_key 的重复（Hub 侧改键留下的孤儿，如存量裸名 `foo`
-- 与新键 `system-foo` 并存）**不在本迁移范围**——索引无法表达该约束，且判定需要「哪个 key 才是
-- 上游权威」这一外部信息。它由管理台下次上传该 skill 时的服务层收敛清理，或由 reconcile 的
-- 缺席墓碑在宽限期后收敛。

-- ==== 验证（执行后手动跑）====
-- 期望 0 行：
-- SELECT skill_key, count(*) FROM sre_skill_asset
--  WHERE source_type='platform' AND deleted_at IS NULL GROUP BY 1 HAVING count(*) > 1;
-- 期望 1 行：
-- SELECT indexname FROM pg_indexes WHERE indexname = 'ux_skill_asset_platform_key';
