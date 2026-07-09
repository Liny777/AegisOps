"""Audit & Trace：回放查询（owner 或管理员；脱敏铁律由写入侧保证）。"""
from __future__ import annotations

from typing import Any

from domain.errors import ApiError, Err
from infra.db import row_json
from infra.repositories import audit, runs


async def run_events(user: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    run = await runs.get_run(run_id)
    if run is None:
        raise ApiError(Err.NOT_FOUND, "Run 不存在")
    if run["user_id"] != user["user_id"] and user["role"] != "platform_admin":
        raise ApiError(Err.FORBIDDEN, "无权查看该 Run 审计")  # IAM-005
    return [row_json(e) for e in await audit.list_by_run(run_id)]


async def admin_recent(limit: int = 100) -> list[dict[str, Any]]:
    return [row_json(e) for e in await audit.list_recent(limit)]


async def by_trace(user: dict[str, Any], trace_id: str) -> list[dict[str, Any]]:
    rows = await audit.list_by_trace(trace_id)
    if rows and user["role"] != "platform_admin" and rows[0].get("user_id") != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权查看该 Trace")
    return [row_json(e) for e in rows]
