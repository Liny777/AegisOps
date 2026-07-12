-- 增量迁移（2026-07-12，D 块·sub_agents 派发账本）。幂等可重跑；GaussDB 兼容。
-- D 块：派发账本（老 D6 状态机迁移；同步 gather 模式下无独立 watcher，超时由派发边界裁决）
CREATE TABLE IF NOT EXISTS sre_agent_delegation (
  delegation_id uuid NOT NULL PRIMARY KEY,
  run_id uuid NOT NULL,
  leader_task_id text NOT NULL,
  agent_key text NOT NULL,
  task_text text NOT NULL DEFAULT '',
  delegation_status text NOT NULL DEFAULT 'running',
  had_final_report boolean NOT NULL DEFAULT false,
  deadline timestamptz,
  report_text text,
  creation_date timestamptz NOT NULL DEFAULT now(),
  last_update_date timestamptz NOT NULL DEFAULT now(),
  created_by text NOT NULL DEFAULT 'system',
  last_updated_by text NOT NULL DEFAULT 'system',
  deleted_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_delegation_leader ON sre_agent_delegation (leader_task_id, creation_date DESC);
COMMENT ON TABLE sre_agent_delegation IS '派发账本（D 块）：main→sub 每次派发一行；预算按本表计数（活跃=非终态；累计=全行含软删，防删除重置）';
COMMENT ON COLUMN sre_agent_delegation.delegation_status IS 'running / completed / failed_no_report / timeout / cancelled';
