from __future__ import annotations

import re
from pathlib import Path

DDL = Path(__file__).resolve().parents[1] / "sql" / "openops_v1_core.sql"

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
    ddl = DDL.read_text(encoding="utf-8")
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS ([a-z_]+)", ddl)
    assert len(tables) == 26  # 21 业务核心 + runtime_config + P 块三表 + D 块 delegation
    assert "sre_platform_runtime_config" in tables
    assert "sre_agent_team_tpl_version" in tables
    assert "sre_agent_team_template_version" not in tables
    assert "sre_model_asset" in tables
    assert "sre_model_access_grant" in tables
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

    assert sum(len(columns) for columns in table_columns.values()) == 52
    for table, columns in table_columns.items():
        assert _commented_columns(ddl, table) == columns, table
