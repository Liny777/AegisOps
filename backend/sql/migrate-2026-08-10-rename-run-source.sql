-- 增量迁移（2026-08-10）：sre_agent_run.run_source 改名 entry_source。
-- 背景：内网同事已有同语义 entry_source 列，我方改名对齐后共用一列（不新建）。
-- 幂等，可重复执行；GaussDB/openGauss 兼容（DO 匿名块 + information_schema）。
--
-- 三种库形态各自要跑什么：
--   ① 全新建库                     —— 只跑 openops_v1_core.sql（已含 entry_source），本文件不用跑（跑了也空转）。
--   ② 我方旧库（已有 run_source 列）—— 跑本文件：真 RENAME，数据原样保留。
--   ③ 内网库（同事已有 entry_source）—— 本文件条件不满足自动空转；仍需跑
--      migrate-2026-07-30-alert-entry-source.sql（entry_source 段幂等跳过，task_origin 段生效）。
-- ⚠ 后端代码与本迁移必须同批上线：老代码写 run_source 会在 RENAME 后的库上报列不存在。

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'sre_agent_run' AND column_name = 'run_source')
     AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                     WHERE table_name = 'sre_agent_run' AND column_name = 'entry_source') THEN
    ALTER TABLE sre_agent_run RENAME COLUMN run_source TO entry_source;
  END IF;
END $$;

COMMENT ON COLUMN sre_agent_run.entry_source IS '会话来源（与内网 entry_source 共用列）：alert=告警自动诊断（会话历史默认排除）；其余取值（user 及内网各入口标记）一律按用户会话处理';
