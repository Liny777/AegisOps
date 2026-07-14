-- OpenOps 存量库迁移：为子 Agent 派发账本增加稳定批次标识。
--
-- 发布顺序：本迁移 -> 更新后的 openops_v1_core.sql -> 后端 -> 前端/sidecar。
-- 幂等、无损、可重复执行；存量行不猜测历史轮次，新增列保持 NULL。
-- 兼容尚未创建 delegation 表的环境：整段直接无操作，避免 COMMENT/CREATE INDEX 单独报错。

DO $openops$
BEGIN
  IF to_regclass('sre_agent_delegation') IS NULL THEN
    RETURN;
  END IF;

  ALTER TABLE sre_agent_delegation
    ADD COLUMN IF NOT EXISTS dispatch_batch_id uuid;

  ALTER TABLE sre_agent_delegation
    ADD COLUMN IF NOT EXISTS dispatch_batch_no integer;

  COMMENT ON COLUMN sre_agent_delegation.dispatch_batch_id
    IS '同一次批量派发共享的批次 ID；存量记录可为空';
  COMMENT ON COLUMN sre_agent_delegation.dispatch_batch_no
    IS '主任务内递增的派发批次号；存量记录可为空';

  CREATE INDEX IF NOT EXISTS ix_delegation_batch
    ON sre_agent_delegation (leader_task_id, dispatch_batch_no, creation_date DESC);
END
$openops$;
