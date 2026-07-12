-- 增量迁移（2026-07-12）：sre_agent_run 加会话名称列。
-- 适用：已按旧版 openops_v1_core.sql 建库的环境（新库跑 core.sql 已含此列，无需本文件）。
-- 幂等，可重复执行。GaussDB/openGauss 兼容（run_title 非保留字）。
ALTER TABLE sre_agent_run ADD COLUMN IF NOT EXISTS run_title text;
COMMENT ON COLUMN sre_agent_run.run_title IS '会话名称：首个任务输入自动起名（前 30 字），用户可改；NULL=未起名（显示「新对话」）';
