-- =====================================================================
-- OpenOps 增量迁移：平台模型 API Key 密文入库（2026-08-17）
-- 对应提交：feat(model) 平台模型 Key 从进程环境变量迁移到 PG 密文列
--
-- 目标库：已建表的存量库（内网测试/生产）。新建库无需本文件——直接跑
--        openops_v1_core.sql 即含三列。
-- 等效性：与重跑 openops_v1_core.sql 完全等效（该文件尾部增量段已含同三列 + COMMENT）。
--        幂等，可重复执行。
-- 执行：  psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 \
--            -f migrate-2026-08-17-model-asset-secret.sql
-- 执行后：增量已收编 core.sql，本文件生命周期结束（照惯例从仓库删除）。
--
-- 说明：原口径「平台 Key 只进进程环境变量、绝不落库」（SEC-001）改为与用户自带模型
--       （sre_user_secret）同构的 Fernet 密文落库，管理台可自助改 Key、不必登服务器重启。
--       ⚠ 本 DDL 只加列不搬数据：psql 读不到后端进程的环境变量，存量 Key 的导入由后端
--       启动时的一次性 backfill 完成（infra/seed.py ensure_platform_key_backfill：凡
--       secret_ciphertext 为空且 secret_env_var 指向的环境变量有值 → 加密写入）。
--       所以升级顺序必须是「先跑本脚本 → 再重启后端（此时 export 仍在）→ 确认导入成功
--       后才可从 run-backend 删除那些 export」。
--       ⚠ OPENOPS_ENCRYPTION_KEY 自此成为平台模型的可用性依赖：key 丢失 = 全部平台模型
--       密钥不可解 = 只能逐个重新录入。必须备份。
-- =====================================================================

ALTER TABLE sre_model_asset
  ADD COLUMN IF NOT EXISTS secret_ciphertext  text,
  ADD COLUMN IF NOT EXISTS secret_key_version text,
  ADD COLUMN IF NOT EXISTS secret_fingerprint text;

COMMENT ON COLUMN sre_model_asset.secret_ciphertext IS '平台模型 API Key 密文（Fernet，见 infra/crypto）；明文不可回显，只在模型构建/探测边界瞬时解密，绝不进 API 响应/审计/日志';
COMMENT ON COLUMN sre_model_asset.secret_key_version IS '加密 key 版本（cfg=OPENOPS_ENCRYPTION_KEY / dev=派生），轮换时据此批量重加密';
COMMENT ON COLUMN sre_model_asset.secret_fingerprint IS '脱敏指纹 fp_<sha256 前 12 位>：唯一可回显给管理台的密钥信息';

-- secret_env_var 降级：运行时不再从环境变量取 Key，该列仅作一次性导入源与回滚锚点保留
COMMENT ON COLUMN sre_model_asset.secret_env_var IS 'DEPRECATED（2026-08-17：Key 已迁 secret_ciphertext 密文列）：仅保留作首次启动的一次性导入源与回滚锚点，运行时不再读取';

-- ==== 验证（执行后手动跑）====
-- 期望：三列在列、均可空（is_nullable=YES）
-- SELECT column_name, is_nullable
--   FROM information_schema.columns
--  WHERE table_name = 'sre_model_asset'
--    AND column_name IN ('secret_ciphertext', 'secret_key_version', 'secret_fingerprint')
--  ORDER BY column_name;
--
-- 期望：重启后端后，原先带 secret_env_var 且环境变量已注入的资产 has_secret 为 t
-- SELECT model_id, secret_env_var, secret_fingerprint,
--        (secret_ciphertext IS NOT NULL) AS has_secret
--   FROM sre_model_asset WHERE deleted_at IS NULL ORDER BY creation_date;
