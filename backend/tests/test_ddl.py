from __future__ import annotations

import re
from pathlib import Path

DDL = Path(__file__).resolve().parents[1] / "sql" / "openops_v1_core.sql"


def test_ddl_001_has_core_tables_plus_runtime_config():
    ddl = DDL.read_text(encoding="utf-8")
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS ([a-z_]+)", ddl)
    assert len(tables) == 20
    assert "platform_runtime_config" in tables
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
    assert "platform_runtime_config" in ddl
    assert "reply_id" in ddl
    assert "tool_call_id" in ddl
    assert "suggested_rules_redacted_json" in ddl
