from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import psycopg

DDL = Path(__file__).resolve().parents[1] / "sql" / "openops_v1_core.sql"
# 平台表数基线统计的是 core + 各垂直切片自带 DDL 之和（切片见 sql/slices/，如 Agent Studio）。
# 单独留 DDL 指向 core.sql：下面的「无外键/无 trigger/对象名长度」等断言仍以 core 为准，
# 切片自己的同类断言在 tests/studio/test_ddl.py。
from conftest import DDL_FILES  # noqa: E402 —— DDL 事实源单一定义在 conftest

COMMENTED_RUNTIME_TABLES = (
    "sre_idempotency_key",
    "sre_task_state",
    "sre_agent_session_state",
    "sre_agent_delegation",
)


def _explicit_relation_names(ddl: str) -> list[str]:
    return re.findall(
        r"CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\s+"
        r"([^\s(]+)",
        ddl,
        flags=re.IGNORECASE,
    )


def _table_columns(ddl: str, table: str) -> set[str]:
    match = re.search(
        rf"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+{re.escape(table)}\s*\((.*?)\);",
        ddl,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, f"missing CREATE TABLE for {table}"

    columns = set()
    for line in match.group(1).splitlines():
        column = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s+", line)
        if column is not None:
            columns.add(column.group(1))
    return columns


def _commented_columns(ddl: str, table: str) -> set[str]:
    return set(
        re.findall(
            rf"COMMENT\s+ON\s+COLUMN\s+{re.escape(table)}\."
            r"([A-Za-z_][A-Za-z0-9_]*)\s+IS\s+",
            ddl,
            flags=re.IGNORECASE,
        )
    )


def test_ddl_001_has_core_tables_plus_runtime_config():
    """平台表数基线（**刻意保留人工门禁**）。

    建表是平台级事件（备份/保留期/脱敏定级/容量都要跟着评估），所以这条基线不做自动发现：
    任何人加表都必须显式改这个数字，从而被 review 看见。
    统计口径 = core.sql + sql/slices/*.sql（垂直切片自带 DDL）。
    """
    ddl = "\n".join(f.read_text(encoding="utf-8") for f in DDL_FILES)
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS ([a-z_]+)", ddl)
    assert len(tables) == 35  # 23 业务核心（含 model_template 两表） + runtime_config + P 块三表 + D 块 delegation + studio span（切片）+ alerts 六表（切片；订阅表 2026-08-15 随实例总开关下线）
    assert "sre_platform_runtime_config" in tables
    assert "sre_agent_studio_span" in tables
    assert "sre_agent_team_tpl_version" in tables
    assert "sre_agent_team_template_version" not in tables
    assert "sre_model_asset" in tables
    assert "sre_model_access_grant" in tables  # DEPRECATED（38.1）保留不消费，表仍在
    assert "sre_model_template" in tables
    assert "sre_model_template_grant" in tables
    assert "user_entitlement_cache" not in tables


def test_ddl_002_has_no_database_level_relations_or_callbacks():
    ddl = DDL.read_text(encoding="utf-8").upper()
    assert "FOREIGN KEY" not in ddl
    assert "REFERENCES" not in ddl
    assert "CREATE TRIGGER" not in ddl
    assert "CREATE FUNCTION" not in ddl


def test_ddl_003_no_old_policy_or_scope_columns():
    ddl = DDL.read_text(encoding="utf-8")
    assert "scope_id" not in ddl
    assert "risk_level" not in ddl
    assert "resource_type" not in ddl
    assert "operation_type" not in ddl


def test_ddl_004_contains_runtime_config_and_approval_projection_fields():
    ddl = DDL.read_text(encoding="utf-8")
    assert "sre_platform_runtime_config" in ddl
    assert "reply_id" in ddl
    assert "tool_call_id" in ddl
    assert "suggested_rules_redacted_json" in ddl


def test_ddl_005_explicit_table_and_index_names_fit_internal_limit():
    ddl = DDL.read_text(encoding="utf-8")
    relation_names = _explicit_relation_names(ddl)

    assert relation_names
    invalid_names = [
        name for name in relation_names if not name.isascii() or len(name) > 30
    ]
    assert invalid_names == []


def test_ddl_006_runtime_state_columns_are_all_commented():
    ddl = DDL.read_text(encoding="utf-8")
    table_columns = {
        table: _table_columns(ddl, table) for table in COMMENTED_RUNTIME_TABLES
    }

    assert sum(len(columns) for columns in table_columns.values()) == 56  # +2：task_state.selected_sub_model（模型模板 sub 槽）+ task_state.task_origin（告警分池）
    for table, columns in table_columns.items():
        assert _commented_columns(ddl, table) == columns, table


def test_ddl_007_subagent_activity_migration_is_idempotent_and_names_fit_limit():
    migration = DDL.parent / "migrate-2026-07-14-subagent-activity.sql"
    sql = migration.read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS dispatch_batch_id uuid" in sql
    assert "ADD COLUMN IF NOT EXISTS dispatch_batch_no integer" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_delegation_batch" in sql
    assert len("ix_delegation_batch") <= 30
    assert "DROP " not in sql.upper()


def test_ddl_008_subagent_activity_migration_executes_twice_and_preserves_data():
    migration = (DDL.parent / "migrate-2026-07-14-subagent-activity.sql").read_text(encoding="utf-8")
    schema = "test_activity_" + uuid.uuid4().hex[:12]
    dsn = os.environ.get("OPENOPS_DATABASE_URL", "postgresql://openops:openops@localhost:5432/openops")

    with psycopg.connect(dsn, autocommit=True) as conn:
        try:
            conn.execute(f'CREATE SCHEMA "{schema}"')
            conn.execute(f'SET search_path TO "{schema}", public')
            # 无 delegation 表时整段必须无操作，而不是在 COMMENT/CREATE INDEX 处失败。
            conn.execute(migration)
            conn.execute(
                """
                CREATE TABLE sre_agent_delegation (
                  delegation_id uuid PRIMARY KEY,
                  leader_task_id varchar(64) NOT NULL,
                  creation_date timestamptz NOT NULL DEFAULT now(),
                  sentinel text
                )
                """
            )
            sentinel_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO sre_agent_delegation (delegation_id, leader_task_id, sentinel) "
                "VALUES (%s, 'tsk-old', 'keep-me')",
                (sentinel_id,),
            )
            conn.execute(migration)
            conn.execute(migration)

            columns = {
                row[0]
                for row in conn.execute(
                    "select column_name from information_schema.columns "
                    "where table_schema=%s and table_name='sre_agent_delegation'",
                    (schema,),
                ).fetchall()
            }
            assert {"dispatch_batch_id", "dispatch_batch_no"} <= columns
            assert conn.execute(
                "select sentinel from sre_agent_delegation where delegation_id=%s", (sentinel_id,)
            ).fetchone()[0] == "keep-me"
            assert conn.execute(
                "select count(*) from pg_indexes where schemaname=%s and indexname='ix_delegation_batch'",
                (schema,),
            ).fetchone()[0] == 1
        finally:
            conn.execute("SET search_path TO public")
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_ddl_009_platform_skill_key_partial_unique_index():
    """平台 skill_key 唯一索引：名字合规、必须带 `WHERE deleted_at IS NULL`（软删行保留 skill_key，
    同名收敛与缺席墓碑每次都产生一条——普通唯一索引会让任何 skill 的第二次上传直接失败）。"""
    ddl = DDL.read_text(encoding="utf-8")

    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_skill_asset_platform_key" in ddl
    assert len("ux_skill_asset_platform_key") <= 30
    stmt = ddl.split("ux_skill_asset_platform_key", 1)[1].split(";", 1)[0]
    assert "sre_skill_asset (skill_key)" in stmt
    assert "source_type = 'platform'" in stmt and "deleted_at IS NULL" in stmt


def test_ddl_009b_platform_skill_key_index_enforced_only_on_live_platform_rows():
    """索引语义实测：同 key 的第二条**活平台行**被拒；软删行与 user 行不受限。"""
    schema = "test_pskey_" + uuid.uuid4().hex[:12]
    dsn = os.environ.get("OPENOPS_DATABASE_URL", "postgresql://openops:openops@localhost:5432/openops")

    def _ins(conn, key, source_type="platform", deleted=None):
        conn.execute(
            "insert into sre_skill_asset (skill_id, source, source_type, owner_user_id, display_name,"
            " skill_key, status, created_by, last_updated_by, deleted_at)"
            " values (%s,'openops',%s,null,'n',%s,'active','t','t',%s)",
            (str(uuid.uuid4()), source_type, key, deleted))

    with psycopg.connect(dsn, autocommit=True) as conn:
        try:
            conn.execute(f'CREATE SCHEMA "{schema}"')
            conn.execute(f'SET search_path TO "{schema}"')
            conn.execute(DDL.read_text(encoding="utf-8"))

            _ins(conn, "system-x")
            try:
                _ins(conn, "system-x")
                raise AssertionError("同 key 的第二条活平台行应被唯一索引拒绝")
            except psycopg.errors.UniqueViolation:
                pass
            _ins(conn, "system-x", deleted="2020-01-01")     # 软删行：部分索引放行
            _ins(conn, "system-x", source_type="user")       # user 行：索引只管 platform
            assert conn.execute(
                "select count(*) from sre_skill_asset where skill_key='system-x'").fetchone()[0] == 3
        finally:
            conn.execute("SET search_path TO public")
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_ddl_010_platform_skill_key_migration_dedupes_and_is_idempotent():
    """迁移脚本：存量脏库先**软删**重复（留最新一条）再建索引；连跑两次幂等、不物删任何行。

    这是本仓第一条可能因存量数据失败的 DDL——脏库直接建索引会 duplicate key，
    且 conftest 是整份 core.sql 一条 execute 跑完，会掀翻整个套件而非一个用例。"""
    migration = (DDL.parent / "migrate-2026-08-16-platform-skill-key-unique.sql").read_text(encoding="utf-8")
    assert "IF NOT EXISTS ux_skill_asset_platform_key" in migration
    assert "DROP TABLE" not in migration.upper() and "DELETE FROM" not in migration.upper()  # 只软删

    schema = "test_pkmig_" + uuid.uuid4().hex[:12]
    dsn = os.environ.get("OPENOPS_DATABASE_URL", "postgresql://openops:openops@localhost:5432/openops")
    with psycopg.connect(dsn, autocommit=True) as conn:
        try:
            conn.execute(f'CREATE SCHEMA "{schema}"')
            conn.execute(f'SET search_path TO "{schema}"')
            conn.execute(DDL.read_text(encoding="utf-8"))
            conn.execute("DROP INDEX ux_skill_asset_platform_key")  # 回到「索引之前」的存量库形态

            ids = []
            for i in range(3):
                sid = str(uuid.uuid4())
                ids.append(sid)
                conn.execute(
                    "insert into sre_skill_asset (skill_id, source, source_type, owner_user_id,"
                    " display_name, skill_key, status, created_by, last_updated_by, creation_date)"
                    " values (%s,'openops','platform',null,%s,'system-dup','active','t','t',"
                    " now() + make_interval(secs => %s))", (sid, f"d{i}", i))

            conn.execute(migration)
            conn.execute(migration)  # 幂等

            live = conn.execute("select skill_id::text from sre_skill_asset "
                                "where deleted_at is null").fetchall()
            assert [r[0] for r in live] == [ids[-1]], "应只留最新一条"
            dead = conn.execute("select status from sre_skill_asset "
                                "where deleted_at is not null").fetchall()
            assert len(dead) == 2 and {r[0] for r in dead} == {"deleted"}  # 软删，非物删
            assert conn.execute("select count(*) from sre_skill_asset").fetchone()[0] == 3
            assert conn.execute(
                "select count(*) from pg_indexes where schemaname=%s "
                "and indexname='ux_skill_asset_platform_key'", (schema,)).fetchone()[0] == 1
        finally:
            conn.execute("SET search_path TO public")
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
