"""PG 连接配置回归护栏（commit 32426dd：OPENOPS_PG_* 拆分 + schema search_path 隔离）。

默认 pytest 走 OPENOPS_DATABASE_URL 回退路径，不覆盖离散 PG_* / 共享 schema 路径——本文件补上：
- 纯逻辑（无 DB，恒跑）：`_conninfo()` 的 keyword 组串（特殊字符密码转义 + schema→search_path + sslmode）与 URL 回退。
- E2E（`OPENOPS_PG_SCHEMA_TEST=1` 门控，需可写 PG）：schema search_path 把 app 落到指定 schema，读到该 schema 内数据（非 public）。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from infra import db

_PG_ENV = ("OPENOPS_PG_HOST", "OPENOPS_PG_PORT", "OPENOPS_PG_DB", "OPENOPS_PG_USER",
           "OPENOPS_PG_PASSWORD", "OPENOPS_PG_SCHEMA", "OPENOPS_PG_SSLMODE")
DDL = Path(__file__).resolve().parents[1] / "sql" / "openops_v1_core.sql"


def _set_pg(monkeypatch, **kw):
    for k in _PG_ENV:
        monkeypatch.delenv(k, raising=False)
    for k, v in kw.items():
        monkeypatch.setenv(k, v)


def test_conninfo_pg_keyword_escapes_specials_and_maps_schema(monkeypatch):
    """OPENOPS_PG_* → make_conninfo keyword 串：特殊字符密码安全转义 + schema→search_path + sslmode。"""
    _set_pg(monkeypatch, OPENOPS_PG_HOST="db.internal", OPENOPS_PG_PORT="6432", OPENOPS_PG_DB="wesee",
            OPENOPS_PG_USER="sre_app", OPENOPS_PG_PASSWORD="p@ss w#rd:1'2\\x",  # 空格/@/#/:/'/\
            OPENOPS_PG_SCHEMA="observe_analysis", OPENOPS_PG_SSLMODE="require")
    ci = db._conninfo()
    assert "postgresql://" not in ci  # keyword 形式，非 URL
    d = conninfo_to_dict(ci)
    assert d["host"] == "db.internal" and d["port"] == "6432"
    assert d["dbname"] == "wesee" and d["user"] == "sre_app"
    assert d["password"] == "p@ss w#rd:1'2\\x"  # 特殊字符精确回环（免 URL 编码坑）
    assert d["options"] == "-c search_path=observe_analysis"
    assert d["sslmode"] == "require"


def test_conninfo_falls_back_to_database_url(monkeypatch):
    """无 OPENOPS_PG_HOST → 回退 OPENOPS_DATABASE_URL（测试/本地向后兼容）。"""
    _set_pg(monkeypatch)  # 清空 PG_*
    monkeypatch.setenv("OPENOPS_DATABASE_URL", "postgresql://u:p@h:5432/dbx")
    assert db._conninfo() == "postgresql://u:p@h:5432/dbx"


@pytest.mark.skipif(os.getenv("OPENOPS_PG_SCHEMA_TEST") != "1",
                    reason="共享 schema 隔离 E2E：设 OPENOPS_PG_SCHEMA_TEST=1（需可写 PG）才跑")
def test_pg_schema_search_path_isolation(monkeypatch):
    """OPENOPS_PG_SCHEMA 经 search_path 把 app 落到指定 schema（内网共享库隔离）。"""
    url = os.environ.get("OPENOPS_DATABASE_URL", "postgresql://openops:openops@localhost:5432/openops")
    conn_d = conninfo_to_dict(url)
    schema = "sre_regtest"
    ddl_sql = DDL.read_text(encoding="utf-8")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"SET search_path TO {schema}")
        conn.execute(ddl_sql)  # sre_ 表建进 schema 内（列名与 DDL 一致，本用例不依赖具体列）
    try:
        _set_pg(monkeypatch, OPENOPS_PG_HOST=conn_d.get("host", "localhost"),
                OPENOPS_PG_PORT=str(conn_d.get("port", "5432")), OPENOPS_PG_DB=conn_d.get("dbname", "openops"),
                OPENOPS_PG_USER=conn_d.get("user", "openops"), OPENOPS_PG_PASSWORD=conn_d.get("password", ""),
                OPENOPS_PG_SCHEMA=schema)
        ci = db._conninfo()
        assert f"search_path={schema}" in ci

        async def _probe():
            pool = AsyncConnectionPool(ci, min_size=1, max_size=2, open=False, kwargs={"row_factory": dict_row})
            await pool.open()
            await pool.wait()
            async with pool.connection() as conn:
                sp = (await (await conn.execute("show search_path")).fetchone())["search_path"]
                cs = (await (await conn.execute("select current_schema() cs")).fetchone())["cs"]
                cnt = (await (await conn.execute("select count(*) c from sre_openops_user")).fetchone())["c"]
            await pool.close()
            return sp, cs, cnt

        sp, cs, cnt = asyncio.run(_probe())
        assert schema in sp, f"search_path={sp}"
        assert cs == schema  # 未加限定的 sre_ 表解析到该 schema（非 public）——共享库隔离成立
        assert cnt == 0  # schema 内新建表为空，佐证读的不是 public 同名表
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
