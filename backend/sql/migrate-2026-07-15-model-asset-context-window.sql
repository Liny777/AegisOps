-- =====================================================================
-- OpenOps 增量迁移：平台模型资产加「上下文长度」列（2026-07-15）
-- 对应提交：feat(model) 增加模型处的「测试连接 + 接口协议 + 上下文长度」
--
-- 目标库：已建 26 表的存量库（内网测试/生产）。新建库无需本文件——直接跑
--        openops_v1_core.sql 即含本列。
-- 等效性：与重跑 openops_v1_core.sql 完全等效（该文件已含同列 + COMMENT）。幂等，可重复执行。
-- 执行：  psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 \
--            -f migrate-2026-07-15-model-asset-context-window.sql
-- 执行后：增量已收编 core.sql，本文件生命周期结束（照惯例从仓库删除）。
-- =====================================================================

-- 管理台注册平台模型时填写的上下文长度（token）；默认 128000，与用户自定义 LLM 口径一致。
ALTER TABLE sre_model_asset
  ADD COLUMN IF NOT EXISTS context_window_tokens integer NOT NULL DEFAULT 128000;

COMMENT ON COLUMN sre_model_asset.context_window_tokens IS '上下文长度（token），管理台注册时填，默认 128000';

-- ==== 验证（执行后手动跑；期望：context_window_tokens 在列、非空、默认 128000）====
-- SELECT column_name, is_nullable, column_default
--   FROM information_schema.columns
--  WHERE table_name = 'sre_model_asset' AND column_name = 'context_window_tokens';
-- 期望输出：context_window_tokens | NO | 128000
