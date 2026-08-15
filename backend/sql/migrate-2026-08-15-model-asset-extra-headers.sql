-- =====================================================================
-- OpenOps 增量迁移：平台模型资产加「自定义出站 Header」列（2026-08-15）
-- 对应提交：feat(model) 平台模型资产支持 extra_headers + 自带模型可改可删
--
-- 目标库：已建表的存量库（内网测试/生产）。新建库无需本文件——直接跑
--        openops_v1_core.sql 即含本列。
-- 等效性：与重跑 openops_v1_core.sql 完全等效（该文件已含同列 + COMMENT）。幂等，可重复执行。
-- 执行：  psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 \
--            -f migrate-2026-08-15-model-asset-extra-headers.sql
-- 执行后：增量已收编 core.sql，本文件生命周期结束（照惯例从仓库删除）。
--
-- 说明：用户自带模型（sre_user_llm_config）的同名列早已存在，本次只补平台侧，
--       使两条模型链路的出站 Header 能力对齐。列内只放路由/租户类明文头——
--       Authorization 等保留头在 schema 层被拒，密钥仍走环境变量（secret_env_var）。
-- =====================================================================

ALTER TABLE sre_model_asset
  ADD COLUMN IF NOT EXISTS extra_params_json jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN sre_model_asset.extra_params_json IS 'Provider 差异化参数（含 extra_headers：自定义出站 Header，schema 层禁 Authorization 等保留头），不允许保存密钥';

-- ==== 验证（执行后手动跑；期望：extra_params_json 在列、非空、默认 '{}'）====
-- SELECT column_name, is_nullable, column_default
--   FROM information_schema.columns
--  WHERE table_name = 'sre_model_asset' AND column_name = 'extra_params_json';
-- 期望输出：extra_params_json | NO | '{}'::jsonb
