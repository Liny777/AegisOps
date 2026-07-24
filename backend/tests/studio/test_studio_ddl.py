"""Agent Studio 切片自有 DDL 的守护测试。

切片表结构变更只需改 `sql/slices/studio_span.sql` + 本文件，**零 core 文件**。
平台级不变式（无外键/无 trigger、对象名长度）切片也必须遵守——这里各留一条，
免得切片成了绕开平台规矩的后门。
"""
from __future__ import annotations

import re
from pathlib import Path

DDL = Path(__file__).resolve().parents[2] / "sql" / "slices" / "studio_span.sql"


def _ddl() -> str:
    return DDL.read_text(encoding="utf-8")


def test_slice_declares_exactly_one_table():
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS ([a-z_]+)", _ddl())
    assert tables == ["sre_agent_studio_span"]


def test_every_column_has_comment():
    """列注释是本仓库的 DDL 铁律（core 的 test_ddl 同口径）。"""
    ddl = _ddl()
    body = ddl[ddl.index("CREATE TABLE IF NOT EXISTS sre_agent_studio_span"):ddl.index(");")]
    cols = {
        m.group(1)
        for line in body.splitlines()[1:]
        if (m := re.match(r"\s+([a-z_]+)\s+[a-z]", line))
    }
    commented = set(re.findall(r"COMMENT ON COLUMN sre_agent_studio_span\.([a-z_]+)", ddl))
    assert cols and not (cols - commented), f"缺列注释：{sorted(cols - commented)}"


def test_no_foreign_key_or_trigger():
    """平台级不变式：无库级关系与回调（切片同样适用）。"""
    up = _ddl().upper()
    assert "FOREIGN KEY" not in up
    assert "REFERENCES" not in up
    assert "CREATE TRIGGER" not in up


def test_object_names_fit_internal_limit():
    """对象名 ≤30 字符（GaussDB 口径，与 core 的 test_ddl_005 一致）。"""
    names = re.findall(r"CREATE (?:TABLE|INDEX) IF NOT EXISTS ([a-z_]+)", _ddl())
    too_long = [n for n in names if len(n) > 30]
    assert not too_long, too_long


def test_table_exists_after_conftest_apply(client):
    """apply 链完整性：conftest 的 DDL_FILES 必须真的把切片表建出来。

    这条守的是「有人漏挂 docker-compose 的 initdb / 漏改 DDL_FILES / 漏跑部署文档那条 psql」——
    否则症状是服务正常启动、drain 的 insert_span 异常被吞成 debug 日志、Studio 页全空，
    排查成本极高。让它在这里响，而不是在生产。
    """
    import os

    import psycopg

    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        got = conn.execute("select to_regclass('sre_agent_studio_span')").fetchone()[0]
    assert got is not None, "切片表未建出——检查 conftest.DDL_FILES 与 sql/slices/"
