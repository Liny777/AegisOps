-- =====================================================================
-- OpenOps 增量迁移：模型模板（主/子 Agent 槽位）+ 任务快照子模型列（2026-07-29）
--                  + 模板授权范围（38.1，2026-08-04 追加）
-- 对应提交：feat(model) 模型配置模板化——主/子 Agent 分模型 + 管理台模板编排
--          fix(model) 授权范围迁移到模型模板维度（38.1）
--
-- 目标库：已建 27 表的存量库（内网测试/生产）。新建库无需本文件——直接跑
--        openops_v1_core.sql 即含本表与本列。
-- 等效性：与重跑 openops_v1_core.sql 完全等效（该文件已含同表/列/索引/COMMENT）。
--        幂等，可重复执行。**已按旧版（无 38.1 段）执行过本文件的库：直接重跑，
--        幂等补齐 38.1 增量（模板 access_scope 列 + 模板授权表），安全。**
-- 执行：  psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 \
--            -f migrate-2026-07-29-model-template.sql
--        发布顺序：先执行本文件、后发后端——task_states 快照写入含新列、
--        模板授权查询打新表，旧库未迁移时快照 upsert 失败（仅 warning）、
--        模板列表/绑定/起任务 500。
-- 执行后：增量已收编 core.sql，本文件生命周期结束（照惯例从仓库删除）。
-- =====================================================================

-- 模型模板：管理台「模型模板」页编排的 主 Agent + 子 Agent 模型组合；
-- 实例 overlay.model_template_id 绑定，运行时逐槽过 B7 ACL 解析。
CREATE TABLE IF NOT EXISTS sre_model_template (
  model_template_id uuid NOT NULL PRIMARY KEY,
  display_name text NOT NULL,
  description text,
  main_model_asset_id uuid NOT NULL,
  sub_model_asset_id uuid NOT NULL,
  is_default boolean NOT NULL DEFAULT false,
  status text NOT NULL DEFAULT 'active',
  creation_date timestamptz NOT NULL DEFAULT now(),
  last_update_date timestamptz NOT NULL DEFAULT now(),
  created_by text NOT NULL DEFAULT 'system',
  last_updated_by text NOT NULL DEFAULT 'system',
  deleted_at timestamptz
);

-- 未删行 display_name 唯一；全局至多一个默认（应用层两步切换，部分唯一索引兜并发竞态）
CREATE UNIQUE INDEX IF NOT EXISTS ux_model_template_name
  ON sre_model_template (display_name)
  WHERE deleted_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_model_template_default
  ON sre_model_template (is_default)
  WHERE is_default AND deleted_at IS NULL;

COMMENT ON TABLE sre_model_template IS '模型模板（主 Agent + 子 Agent 两槽位；管理台「模型模板」页编排）：实例 overlay.model_template_id 绑定，运行时逐槽过 B7 ACL 解析';
COMMENT ON COLUMN sre_model_template.model_template_id IS '模型模板主键';
COMMENT ON COLUMN sre_model_template.display_name IS '模板展示名（未删行唯一），如「均衡（推荐）」';
COMMENT ON COLUMN sre_model_template.description IS '模板说明，可空';
COMMENT ON COLUMN sre_model_template.main_model_asset_id IS '主 Agent 槽位 → sre_model_asset.model_asset_id（应用层校验，无外键）';
COMMENT ON COLUMN sre_model_template.sub_model_asset_id IS '子 Agent 槽位 → sre_model_asset.model_asset_id（全部子 Agent 共用一个槽位）';
COMMENT ON COLUMN sre_model_template.is_default IS '全局唯一默认（ux_model_template_default 兜底）；仅影响用户选单预选，不影响运行时解析';
COMMENT ON COLUMN sre_model_template.status IS 'active / disabled：disabled 不进用户选单，已绑实例运行时回退平台默认并留痕';
COMMENT ON COLUMN sre_model_template.creation_date IS '创建时间';
COMMENT ON COLUMN sre_model_template.last_update_date IS '最后更新时间';
COMMENT ON COLUMN sre_model_template.created_by IS '创建人工号';
COMMENT ON COLUMN sre_model_template.last_updated_by IS '最后更新人工号';
COMMENT ON COLUMN sre_model_template.deleted_at IS '软删除时间，NULL 表示未删除';

-- 任务快照：子 Agent 槽位模型标识（P 块老表，存量库必有，裸 ALTER 即可——07-15 先例）
ALTER TABLE sre_task_state
  ADD COLUMN IF NOT EXISTS selected_sub_model text;

COMMENT ON COLUMN sre_task_state.selected_sub_model IS '子 Agent 槽位选用的模型标识（模型模板 sub 槽）；NULL=跟随主模型（BYO/legacy/平台默认）';

-- ==== 38.1（2026-08-04 追加）：授权范围迁至模板维度 ====
-- 已跑过上方旧版建表的库靠 ALTER 补列；未跑过的库上方 CREATE 已含列，ALTER 幂等空转。
-- 旧的资产级授权结构（sre_model_asset.access_scope 列、sre_model_access_grant 表）
-- 保留废弃不消费、无 DROP；存量授权行仅作历史留痕。

ALTER TABLE sre_model_template
  ADD COLUMN IF NOT EXISTS access_scope text NOT NULL DEFAULT 'all';

CREATE TABLE IF NOT EXISTS sre_model_template_grant (
  grant_id uuid NOT NULL PRIMARY KEY,
  model_template_id uuid NOT NULL,
  user_id text NOT NULL,
  granted_by text NOT NULL,
  status text NOT NULL DEFAULT 'active',
  creation_date timestamptz NOT NULL DEFAULT now(),
  last_update_date timestamptz NOT NULL DEFAULT now(),
  created_by text NOT NULL DEFAULT 'system',
  last_updated_by text NOT NULL DEFAULT 'system',
  deleted_at timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_model_template_grant_user
  ON sre_model_template_grant (model_template_id, user_id)
  WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_model_template_grant_user
  ON sre_model_template_grant (user_id)
  WHERE deleted_at IS NULL;

COMMENT ON COLUMN sre_model_template.access_scope IS 'all=全员开放 / restricted=仅白名单授权用户（按人；38.1 起授权在模板维度，白名单见 sre_model_template_grant）';
COMMENT ON TABLE sre_model_template_grant IS '模型模板白名单授权（38.1：按 user_id 逐人授权，仅 restricted 模板消费；接替已废弃的 sre_model_access_grant）';
COMMENT ON COLUMN sre_model_template_grant.grant_id IS '授权主键';
COMMENT ON COLUMN sre_model_template_grant.model_template_id IS '关联模型模板 ID';
COMMENT ON COLUMN sre_model_template_grant.user_id IS '被授权用户工号';
COMMENT ON COLUMN sre_model_template_grant.granted_by IS '授权人（管理员工号）';
COMMENT ON COLUMN sre_model_template_grant.status IS '状态：active / revoked';
COMMENT ON COLUMN sre_model_template_grant.creation_date IS '创建时间';
COMMENT ON COLUMN sre_model_template_grant.last_update_date IS '最后更新时间';
COMMENT ON COLUMN sre_model_template_grant.created_by IS '创建人工号';
COMMENT ON COLUMN sre_model_template_grant.last_updated_by IS '最后更新人工号';
COMMENT ON COLUMN sre_model_template_grant.deleted_at IS '软删除时间，NULL 表示未删除';

COMMENT ON COLUMN sre_model_asset.access_scope IS 'DEPRECATED（38.1：授权迁至模型模板维度 sre_model_template.access_scope）：本列保留不消费，新写恒 DEFAULT all';
COMMENT ON TABLE sre_model_access_grant IS 'DEPRECATED（38.1：授权迁至 sre_model_template_grant 模板维度）：本表保留不消费，存量授权行仅作历史留痕';

-- ==== 验证（执行后手动跑）====
-- 期望 1：模板表索引齐
-- SELECT indexname FROM pg_indexes WHERE tablename = 'sre_model_template';
-- 期望输出含：ux_model_template_name / ux_model_template_default
-- 期望 2：selected_sub_model 在列、可空
-- SELECT column_name, is_nullable FROM information_schema.columns
--  WHERE table_name = 'sre_task_state' AND column_name = 'selected_sub_model';
-- 期望输出：selected_sub_model | YES
-- 期望 3（38.1）：access_scope 在列且默认 'all'
-- SELECT column_name, column_default FROM information_schema.columns
--  WHERE table_name = 'sre_model_template' AND column_name = 'access_scope';
-- 期望 4（38.1）：模板授权表两索引在
-- SELECT indexname FROM pg_indexes WHERE tablename = 'sre_model_template_grant';
-- 期望输出含：ux_model_template_grant_user / ix_model_template_grant_user
