"""7x24 告警接管切片自有 DDL 的守护测试。

切片表结构变更只需改 `sql/slices/alerts.sql` + 本文件，**零 core 文件**
（core 侧仅 run_source/task_origin 两列走 migrate-2026-07-30-alert-run-source.sql）。
平台级不变式（无外键/无 trigger、对象名长度、全列注释）切片同样必须遵守——
与 tests/studio/test_studio_ddl.py 同口径，免得切片成了绕开平台规矩的后门。
"""
from __future__ import annotations

import re
from pathlib import Path

DDL = Path(__file__).resolve().parents[2] / "sql" / "slices" / "alerts.sql"

EXPECTED_TABLES = [
    "sre_alert_rule",
    "sre_alert_event",
    "sre_alert_incident",
    "sre_alert_incident_event",
    "sre_alert_subscription",
    "sre_alert_pull_state",
]


def _ddl() -> str:
    return DDL.read_text(encoding="utf-8")


def test_slice_declares_exactly_six_tables():
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS ([a-z_]+)", _ddl())
    assert tables == EXPECTED_TABLES


def test_every_column_has_comment():
    """列注释是本仓库的 DDL 铁律（core 的 test_ddl 同口径），六表逐一核。"""
    ddl = _ddl()
    for table in EXPECTED_TABLES:
        m = re.search(
            rf"CREATE TABLE IF NOT EXISTS {table}\s*\((.*?)\n\);",
            ddl,
            flags=re.DOTALL,
        )
        assert m is not None, f"missing CREATE TABLE for {table}"
        cols = {
            cm.group(1)
            for line in m.group(1).splitlines()
            if (cm := re.match(r"\s+([a-z_]+)\s+[a-z]", line))
        }
        commented = set(re.findall(rf"COMMENT ON COLUMN {table}\.([a-z_]+)", ddl))
        assert cols and not (cols - commented), f"{table} 缺列注释：{sorted(cols - commented)}"


def test_no_foreign_key_or_trigger():
    """平台级不变式：无库级关系与回调（切片同样适用）。"""
    up = _ddl().upper()
    assert "FOREIGN KEY" not in up
    assert "REFERENCES" not in up
    assert "CREATE TRIGGER" not in up


def test_object_names_fit_internal_limit():
    """对象名 ≤30 字符（GaussDB 口径，与 core 的 test_ddl_005 一致）。"""
    names = re.findall(r"CREATE (?:UNIQUE )?(?:TABLE|INDEX) IF NOT EXISTS ([a-z_]+)", _ddl())
    assert len(names) >= len(EXPECTED_TABLES)
    too_long = [n for n in names if len(n) > 30 or not n.isascii()]
    assert not too_long, too_long


def test_incident_state_machine_documented():
    """六态状态机是队列语义的契约，列注释必须完整枚举（防止实现漂移后注释失真）。"""
    ddl = _ddl()
    m = re.search(r"COMMENT ON COLUMN sre_alert_incident\.incident_state IS '([^']+)'", ddl)
    assert m is not None
    for state in ("queued", "diagnosing", "completed", "failed", "skipped", "ignored"):
        assert state in m.group(1), f"incident_state 注释缺少 {state}"


def test_migration_is_idempotent_and_covers_both_columns():
    """core 两列迁移：幂等（ADD COLUMN IF NOT EXISTS）、带注释、无破坏性语句。"""
    migration = DDL.parents[1] / "migrate-2026-07-30-alert-run-source.sql"
    sql = migration.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS run_source text NOT NULL DEFAULT 'user'" in sql
    assert "ADD COLUMN IF NOT EXISTS task_origin text NOT NULL DEFAULT 'user'" in sql
    assert "COMMENT ON COLUMN sre_agent_run.run_source" in sql
    assert "COMMENT ON COLUMN sre_task_state.task_origin" in sql
    assert "DROP " not in sql.upper()


def test_tables_exist_after_conftest_apply(client):
    """apply 链完整性：conftest 的 DDL_FILES（glob sql/slices/*.sql）必须真的把六表建出来。"""
    import os

    import psycopg

    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        for table in EXPECTED_TABLES:
            got = conn.execute(f"select to_regclass('{table}')").fetchone()[0]
            assert got is not None, f"切片表 {table} 未建出——检查 conftest.DDL_FILES 与 sql/slices/"
        run_cols = {
            r[0]
            for r in conn.execute(
                "select column_name from information_schema.columns "
                "where table_name='sre_agent_run'"
            ).fetchall()
        }
        assert "run_source" in run_cols
        task_cols = {
            r[0]
            for r in conn.execute(
                "select column_name from information_schema.columns "
                "where table_name='sre_task_state'"
            ).fetchall()
        }
        assert "task_origin" in task_cols
