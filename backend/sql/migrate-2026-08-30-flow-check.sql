-- 增量迁移（2026-08-30）：MCP 工具标注新增「四号校验」（风控二次认证，29.14）
-- 目标库：已建表的存量库（内网测试/生产）。新建库无需本文件——直接跑
--        openops_v1_core.sql 即含本列与新表。
-- 等效性：与重跑 openops_v1_core.sql 完全等效（该文件已含同列/同表 + COMMENT）。幂等，可重复执行。
-- 执行：  psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f migrate-2026-08-30-flow-check.sql
-- 执行后：增量已收编 core.sql，本文件生命周期结束（照惯例从仓库删除）。

-- 1) 标注表新增两列 + 互斥约束（人工审批与四号校验二选一）
ALTER TABLE sre_mcp_tool_annotation ADD COLUMN IF NOT EXISTS is_flow_check_required boolean NOT NULL DEFAULT false;
ALTER TABLE sre_mcp_tool_annotation ADD COLUMN IF NOT EXISTS flow_check_config jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE sre_mcp_tool_annotation DROP CONSTRAINT IF EXISTS ck_annot_approval_xor_flow;
ALTER TABLE sre_mcp_tool_annotation ADD CONSTRAINT ck_annot_approval_xor_flow
  CHECK (NOT (is_approval_required AND is_flow_check_required));
COMMENT ON COLUMN sre_mcp_tool_annotation.is_flow_check_required IS '是否需要四号校验（风控二次认证）；与 is_approval_required 互斥';
COMMENT ON COLUMN sre_mcp_tool_annotation.flow_check_config IS '四号校验配置：init_path/verify_path/invoking_method/service_id_by_tenant/object_arg_path（29.14）';

-- 2) 四号校验请求表（形制对齐 sre_approval_request；token/四号编码不落库）
CREATE TABLE IF NOT EXISTS sre_flow_check_request (
  flow_check_request_id uuid NOT NULL PRIMARY KEY,
  user_id text NOT NULL,
  agent_team_instance_id uuid NOT NULL,
  agent_run_id uuid NOT NULL,
  framework_session_id text NOT NULL,
  task_id text,
  tool_call_name text NOT NULL,
  arguments_redacted_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  flow_check_config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  decision text NOT NULL DEFAULT 'pending',
  decided_by text,
  decided_at timestamptz,
  audit_trace_id uuid NOT NULL,
  creation_date timestamptz NOT NULL DEFAULT now(),
  last_update_date timestamptz NOT NULL DEFAULT now(),
  created_by text NOT NULL DEFAULT 'system',
  last_updated_by text NOT NULL DEFAULT 'system',
  expire_at timestamptz
);

CREATE INDEX IF NOT EXISTS ix_fc_run_decision
  ON sre_flow_check_request (agent_run_id, decision);
CREATE INDEX IF NOT EXISTS ix_fc_user_created
  ON sre_flow_check_request (user_id, creation_date DESC);
CREATE INDEX IF NOT EXISTS ix_fc_trace
  ON sre_flow_check_request (audit_trace_id);
CREATE INDEX IF NOT EXISTS ix_fc_pending_expire
  ON sre_flow_check_request (expire_at)
  WHERE decision = 'pending';

COMMENT ON TABLE sre_flow_check_request IS '四号校验（风控二次认证）请求；token/四号编码不落库，仅存决策终态';
COMMENT ON COLUMN sre_flow_check_request.flow_check_request_id IS '四号校验记录主键';
COMMENT ON COLUMN sre_flow_check_request.user_id IS '发起人工号';
COMMENT ON COLUMN sre_flow_check_request.agent_team_instance_id IS '所属实例 ID';
COMMENT ON COLUMN sre_flow_check_request.agent_run_id IS '所属运行 ID';
COMMENT ON COLUMN sre_flow_check_request.framework_session_id IS 'AgentScope session ID';
COMMENT ON COLUMN sre_flow_check_request.task_id IS '任务 ID，可空';
COMMENT ON COLUMN sre_flow_check_request.tool_call_name IS '待执行的恢复类 tool 名称';
COMMENT ON COLUMN sre_flow_check_request.arguments_redacted_json IS '脱敏参数摘要';
COMMENT ON COLUMN sre_flow_check_request.flow_check_config_json IS '创建时按租户解析后的四号校验配置快照（init_path/verify_path/service_id/invoking_method/operator/enterprise_id/target_object）';
COMMENT ON COLUMN sre_flow_check_request.decision IS 'pending / approved / rejected / timeout / cancelled';
COMMENT ON COLUMN sre_flow_check_request.decided_by IS '确认人工号';
COMMENT ON COLUMN sre_flow_check_request.decided_at IS '确认时间';
COMMENT ON COLUMN sre_flow_check_request.audit_trace_id IS '关联 trace';
COMMENT ON COLUMN sre_flow_check_request.creation_date IS '创建时间';
COMMENT ON COLUMN sre_flow_check_request.last_update_date IS '最后更新时间';
COMMENT ON COLUMN sre_flow_check_request.created_by IS '创建人工号';
COMMENT ON COLUMN sre_flow_check_request.last_updated_by IS '最后更新人工号';
COMMENT ON COLUMN sre_flow_check_request.expire_at IS '四号校验超时时间，pending 超过即按 timeout 关闭';

-- 验证（psql 手动执行）：
--   \d sre_mcp_tool_annotation   → 应含 is_flow_check_required / flow_check_config 两列与 ck_annot_approval_xor_flow 约束
--   \d sre_flow_check_request    → 应含 18 列与 4 个 ix_fc_* 索引
--   期望：重复执行本文件不报错（幂等）。
