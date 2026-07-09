"""PG 访问：psycopg3 异步连接池 + 轻量查询助手。

Router 不得直接 import 本模块（22 号分层铁律）；只有 repositories 使用。
"""
from __future__ import annotations

import json
import os
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import AsyncConnectionPool

DATABASE_URL = os.environ.get(
    "OPENOPS_DATABASE_URL", "postgresql://openops:openops@localhost:5432/openops"
)

def _new_pool() -> AsyncConnectionPool:
    return AsyncConnectionPool(
        DATABASE_URL, min_size=1, max_size=8, open=False, kwargs={"row_factory": dict_row}
    )


pool = _new_pool()


async def open_pool() -> None:
    global pool
    if getattr(pool, "closed", False):
        pool = _new_pool()
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
