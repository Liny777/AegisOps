-- 增量迁移（2026-07-12，P 块·运行态持久化）：幂等键 / 任务快照 / 会话状态三表。
-- 适用：已按旧版 openops_v1_core.sql 建库的环境（新库跑 core.sql 已含，无需本文件）。
-- 幂等，可重复执行。GaussDB/openGauss 兼容（无保留字冲突，偏索引与既有 ux_platform_runtime_config 同款）。

CREATE TABLE IF NOT EXISTS sre_idempotency_key (
  idempotency_id uuid NOT NULL PRIMARY KEY,
  user_id text NOT NULL,
  op text NOT NULL,
  client_request_id text NOT NULL,
  result_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  expire_at timestamptz NOT NULL DEFAULT (now() + interval '7 days'),
  creation_date timestamptz NOT NULL DEFAULT now(),
  last_update_date timestamptz NOT NULL DEFAULT now(),
  created_by text NOT NULL DEFAULT 'system',
  last_updated_by text NOT NULL DEFAULT 'system',
  deleted_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_idempotency_key
  ON sre_idempotency_key (user_id, op, client_request_id) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS sre_task_state (
  task_id text NOT NULL PRIMARY KEY,
  run_id uuid NOT NULL,
  user_id text NOT NULL,
  instance_id uuid NOT NULL,
  task_status text NOT NULL DEFAULT 'running',
  input_text text NOT NULL DEFAULT '',
  rca_json jsonb,
  selected_model text,
  scope_ctx_json jsonb,
  approval_id text,
  started_at text,
  audit_trace_id text,
  creation_date timestamptz NOT NULL DEFAULT now(),
  last_update_date timestamptz NOT NULL DEFAULT now(),
  created_by text NOT NULL DEFAULT 'system',
  last_updated_by text NOT NULL DEFAULT 'system',
  deleted_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_task_state_run ON sre_task_state (run_id, creation_date DESC);
CREATE INDEX IF NOT EXISTS ix_task_state_running ON sre_task_state (user_id) WHERE task_status = 'running';

CREATE TABLE IF NOT EXISTS sre_agent_session_state (
  session_state_id uuid NOT NULL PRIMARY KEY,
  framework_session_id text NOT NULL,
  agent_key text NOT NULL DEFAULT 'main',
  state_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  creation_date timestamptz NOT NULL DEFAULT now(),
  last_update_date timestamptz NOT NULL DEFAULT now(),
  created_by text NOT NULL DEFAULT 'system',
  last_updated_by text NOT NULL DEFAULT 'system',
  deleted_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_session_state
  ON sre_agent_session_state (framework_session_id, agent_key) WHERE deleted_at IS NULL;
