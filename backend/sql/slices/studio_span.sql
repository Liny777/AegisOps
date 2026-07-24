-- OpenOps 存量库迁移：Agent Studio（管理员回溯复盘）span 捕获表。
--
-- 发布顺序：本迁移 -> 更新后的 openops_v1_core.sql -> 后端 -> 前端。
-- 幂等、无损、可重复执行。`OPENOPS_PG_SCHEMA` 用户先切 search_path 再执行
-- （psql: SET search_path TO <schema>;），与其余 migrate 文件同规矩。
--
-- ⚠ 本表存 LLM 输入输出 / 工具入参结果 / 交接内容**原文**（每字段 cap
--   OPENOPS_STUDIO_TEXT_CAP，默认 8000），仅 /admin/studio/*（platform_admin）可读；
--   与 sre_audit_event（脱敏审计链）刻意分离。到期硬删（expire_at，默认 30 天）。
--   append-only 遥测：写入方恒为 system，有意省略 created_by/last_updated_by/deleted_at。

CREATE TABLE IF NOT EXISTS sre_agent_studio_span (
  id bigserial PRIMARY KEY,
  user_id text NOT NULL DEFAULT '',
  agent_run_id uuid NOT NULL,
  task_id text NOT NULL DEFAULT '',
  session_id text NOT NULL DEFAULT '',
  agent_role text NOT NULL DEFAULT '',
  agent_name text NOT NULL DEFAULT '',
  kind text NOT NULL,
  model text NOT NULL DEFAULT '',
  provider text NOT NULL DEFAULT '',
  input_tokens bigint NOT NULL DEFAULT 0,
  output_tokens bigint NOT NULL DEFAULT 0,
  cache_input_tokens bigint NOT NULL DEFAULT 0,
  latency_ms double precision NOT NULL DEFAULT 0,
  started_at timestamptz NOT NULL,
  ended_at timestamptz NOT NULL,
  tool_name text NOT NULL DEFAULT '',
  tool_args text NOT NULL DEFAULT '',
  tool_result text NOT NULL DEFAULT '',
  input_messages text NOT NULL DEFAULT '',
  output_messages text NOT NULL DEFAULT '',
  finish_reason text NOT NULL DEFAULT '',
  span_status text NOT NULL DEFAULT '',
  trace_id text NOT NULL DEFAULT '',
  span_id text NOT NULL DEFAULT '',
  parent_span_id text NOT NULL DEFAULT '',
  creation_date timestamptz NOT NULL DEFAULT now(),
  expire_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_studio_span_run ON sre_agent_studio_span (agent_run_id, id);
CREATE INDEX IF NOT EXISTS ix_studio_span_user ON sre_agent_studio_span (user_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_studio_span_expire ON sre_agent_studio_span (expire_at);

COMMENT ON TABLE sre_agent_studio_span IS 'Agent Studio span 捕获（管理员回溯复盘）：agent/llm/tool 三类，含 LLM 与工具原文，仅 admin API 可读，到期硬删';
COMMENT ON COLUMN sre_agent_studio_span.id IS '自增主键，兼时间序（drain 单写者按结束序入队）';
COMMENT ON COLUMN sre_agent_studio_span.user_id IS 'span 归属用户工号（contextvar 注入）';
COMMENT ON COLUMN sre_agent_studio_span.agent_run_id IS '归组键：所属运行 ID（sre_agent_run）';
COMMENT ON COLUMN sre_agent_studio_span.task_id IS '产生 span 的任务 ID（主任务或子任务）';
COMMENT ON COLUMN sre_agent_studio_span.session_id IS 'agent 实例键（gen_ai.conversation_id）：main=framework_session_id；sub=fsid:{agent_key}-{delegation_id前8位}';
COMMENT ON COLUMN sre_agent_studio_span.agent_role IS 'main / 子 Agent 的 agent_key';
COMMENT ON COLUMN sre_agent_studio_span.agent_name IS 'agentscope Agent 名（invoke_agent span 独有，如 sre-rca）';
COMMENT ON COLUMN sre_agent_studio_span.kind IS 'agent（invoke_agent，含 agent 层输入输出）/ llm（chat）/ tool（execute_tool，含 MCP）';
COMMENT ON COLUMN sre_agent_studio_span.model IS 'LLM 调用的模型名（kind=llm）';
COMMENT ON COLUMN sre_agent_studio_span.provider IS 'LLM provider 名（kind=llm）';
COMMENT ON COLUMN sre_agent_studio_span.input_tokens IS '本次 LLM 调用输入 token 数';
COMMENT ON COLUMN sre_agent_studio_span.output_tokens IS '本次 LLM 调用输出 token 数';
COMMENT ON COLUMN sre_agent_studio_span.cache_input_tokens IS '本次 LLM 调用命中缓存的输入 token 数';
COMMENT ON COLUMN sre_agent_studio_span.latency_ms IS 'span 时长（毫秒）';
COMMENT ON COLUMN sre_agent_studio_span.started_at IS 'span 开始时间';
COMMENT ON COLUMN sre_agent_studio_span.ended_at IS 'span 结束时间';
COMMENT ON COLUMN sre_agent_studio_span.tool_name IS '工具名（kind=tool；MCP 工具为其注册名）';
COMMENT ON COLUMN sre_agent_studio_span.tool_args IS '工具入参原文（截断）';
COMMENT ON COLUMN sre_agent_studio_span.tool_result IS '工具结果原文（截断）';
COMMENT ON COLUMN sre_agent_studio_span.input_messages IS 'LLM 输入 messages（middleware 子类补写）/ agent 层输入（截断）';
COMMENT ON COLUMN sre_agent_studio_span.output_messages IS 'LLM 输出 / agent 层输出（截断）';
COMMENT ON COLUMN sre_agent_studio_span.finish_reason IS 'LLM 结束原因（stop/tool_calls/…）';
COMMENT ON COLUMN sre_agent_studio_span.span_status IS 'OTel span 状态：OK / ERROR / UNSET';
COMMENT ON COLUMN sre_agent_studio_span.trace_id IS 'OTel trace id（hex32）';
COMMENT ON COLUMN sre_agent_studio_span.span_id IS 'OTel span id（hex16）';
COMMENT ON COLUMN sre_agent_studio_span.parent_span_id IS '父 span id（hex16），空=根';
COMMENT ON COLUMN sre_agent_studio_span.creation_date IS '落库时间';
COMMENT ON COLUMN sre_agent_studio_span.expire_at IS '过期时间（写入时 now()+OPENOPS_STUDIO_RETENTION_DAYS，默认 30 天；到期硬删）';
