-- =====================================================================
-- OpenOps 增量迁移：幂等键「先占位后执行」改造（2026-07-13 缺陷批 DEF-3）
-- 对应提交 a2b94b7（fix: DEF-3..8 缺陷批 + 连带 A/B/C/D 安全收口）
--
-- 目标库：已建 26 表的存量库（内网测试/生产）。新建库无需本文件——直接跑
--        openops_v1_core.sql 即含全部改动。
-- 等效性：与重跑 openops_v1_core.sql 完全等效（该文件尾部「增量迁移」段含同样语句）；
--        本脚本供 DBA 单独评审/执行用。幂等，可重复执行。
-- 执行：  psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 \
--            -f migrate-2026-07-13-idempotency.sql
-- 执行后：增量已收编 core.sql，本文件生命周期结束（照惯例从仓库删除）。
-- =====================================================================

-- ① 请求体指纹列：同 client_request_id 不同请求体 → 409 IDEMPOTENCY_KEY_CONFLICT
--   （21/31 号 INIT-006 验收断言，此前实现缺失）
ALTER TABLE sre_idempotency_key ADD COLUMN IF NOT EXISTS request_hash varchar(64);

-- ② result_json 允许 NULL：NULL=占位进行中（begin 先 INSERT 占位抢并发锁，
--    业务成功 commit 回填结果，失败 rollback 删占位行）
ALTER TABLE sre_idempotency_key ALTER COLUMN result_json DROP NOT NULL;

-- ③ 去掉旧默认 '{}'：⚠承重坑——占位 INSERT 不写 result_json 时旧默认会把它落成
--    '{}'，非 NULL 即被误判「已有结果」，重放请求拿到空结果
ALTER TABLE sre_idempotency_key ALTER COLUMN result_json DROP DEFAULT;

-- ④ 防御性清理：只有「新代码曾对未迁移库运行」才会产生 '{}' 占位行（正常存量库
--    此步 0 行）。这类行语义已损坏（既非进行中也非真结果），删除后同 key 重试按
--    全新请求处理（幂等键非业务数据，无审计保留要求，见 purge_expired 同口径）
DELETE FROM sre_idempotency_key WHERE result_json = '{}'::jsonb;

-- ⑤ 列注释（COMMENT 无 IF EXISTS，必须在 ① 之后执行；与 core.sql 保持一字不差）
COMMENT ON COLUMN sre_idempotency_key.result_json IS '首次执行的结果（row_json 后的 JSON），重放原样返回；NULL=占位进行中（DEF-3 先占位后执行）';
COMMENT ON COLUMN sre_idempotency_key.request_hash IS '请求体 sha256（DEF-3/INIT-006）：同 key 不同体 → 409 IDEMPOTENCY_KEY_CONFLICT';

-- ==== 验证（执行后手动跑；期望：request_hash 在列、result_json 可空且无默认值）====
-- SELECT column_name, is_nullable, column_default
--   FROM information_schema.columns
--  WHERE table_name = 'sre_idempotency_key'
--    AND column_name IN ('request_hash', 'result_json');
-- 期望输出：
--   request_hash | YES | (null)
--   result_json  | YES | (null)
