"""PG 访问：psycopg3 异步连接池 + 轻量查询助手。

Router 不得直接 import 本模块（22 号分层铁律）；只有 repositories 使用。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import AsyncConnectionPool


def _conninfo() -> str:
    """连接串：离散 `OPENOPS_PG_*` 优先（keyword 格式，密码/特殊字符 make_conninfo 自动转义安全，
    避免 URL 编码坑），否则回退单一 `OPENOPS_DATABASE_URL`（默认本地）。

    `OPENOPS_PG_SCHEMA` 经 `options=-c search_path=<schema>` 把 app 连接落到指定 schema
    （内网共享库隔离用）；`OPENOPS_PG_SSLMODE` 可选（企业库常需 `require`）。
    """
    host = os.environ.get("OPENOPS_PG_HOST")
    if host:
        params: dict[str, str] = {
            "host": host,
            "port": os.environ.get("OPENOPS_PG_PORT", "5432"),
            "dbname": os.environ.get("OPENOPS_PG_DB", "openops"),
            "user": os.environ.get("OPENOPS_PG_USER", "openops"),
            "password": os.environ.get("OPENOPS_PG_PASSWORD", ""),
        }
        schema = os.environ.get("OPENOPS_PG_SCHEMA", "").strip()
        if schema:
            params["options"] = f"-c search_path={schema}"
        sslmode = os.environ.get("OPENOPS_PG_SSLMODE", "").strip()
        if sslmode:
            params["sslmode"] = sslmode
        return make_conninfo(**params)
    return os.environ.get(
        "OPENOPS_DATABASE_URL", "postgresql://openops:openops@localhost:5432/openops"
    )


DATABASE_URL = _conninfo()

def _new_pool() -> AsyncConnectionPool:
    return AsyncConnectionPool(
        DATABASE_URL, min_size=1, max_size=8, open=False, kwargs={"row_factory": dict_row}
    )


pool = _new_pool()


_log = logging.getLogger("openops.db")


def _log_target() -> None:
    """启动即打印真实连库目标（host/db/user，不含密码）——`OPENOPS_PG_*` 没进进程时会静默回退
    localhost 默认串，届时这行会显示 host=localhost，一眼看穿（而非只见 30s PoolTimeout）。"""
    try:
        from psycopg.conninfo import conninfo_to_dict

        d = conninfo_to_dict(DATABASE_URL)
        msg = (f"[db] connecting host={d.get('host')} port={d.get('port')} "
               f"dbname={d.get('dbname')} user={d.get('user')}")
    except Exception:  # noqa: BLE001 —— 诊断日志不得影响启动
        msg = "[db] connecting (conninfo 解析失败)"
    _log.warning(msg)
    print(f"[OpenOps]{msg}", flush=True)


async def open_pool() -> None:
    global pool
    if getattr(pool, "closed", False):
        pool = _new_pool()
    _log_target()
    await pool.open()
    await pool.wait()


async def close_pool() -> None:
    await pool.close()


def jsonb(v: Any) -> Json:
    """dict/list → jsonb 参数。"""
    return Json(v if v is not None else {})


async def q_one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        cur = await conn.execute(sql, params or {})
        return await cur.fetchone()


async def q_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        cur = await conn.execute(sql, params or {})
        return await cur.fetchall()


async def exec1(sql: str, params: dict[str, Any] | None = None) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(sql, params or {})
        return cur.rowcount


def row_json(row: dict[str, Any]) -> dict[str, Any]:
    """行对象 → 可 JSON 序列化 dict（uuid/datetime → str）。"""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if v is None or isinstance(v, (str, int, float, bool, dict, list)):
            out[k] = v
        else:
            out[k] = str(v) if not hasattr(v, "isoformat") else v.isoformat()
    return out
