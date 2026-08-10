-- 增量迁移（2026-07-30）：7x24 告警接管——run 来源标记与任务并发分池两列。
-- 适用：已按旧版 openops_v1_core.sql 建库的环境（新库跑 core.sql 已含此两列，无需本文件）。
-- 幂等，可重复执行。GaussDB/openGauss 兼容（entry_source/task_origin 非保留字）。
-- 发布顺序：本文件 -> sql/slices/alerts.sql -> 更新后的 core.sql -> 后端 -> 前端。
ALTER TABLE sre_agent_run ADD COLUMN IF NOT EXISTS entry_source text NOT NULL DEFAULT 'user';
COMMENT ON COLUMN sre_agent_run.entry_source IS '会话来源：user=用户交互 / alert=告警自动诊断；会话历史列表默认排除 alert';
ALTER TABLE sre_task_state ADD COLUMN IF NOT EXISTS task_origin text NOT NULL DEFAULT 'user';
COMMENT ON COLUMN sre_task_state.task_origin IS '任务来源池：user 占 per_user_running_task_limit；alert 由告警派发并发闸把守，不占用户交互额度';
