"""MCP Tool Annotation：管理台标注（07 号 4+1 字段；scope=required 必填 path）。"""
from __future__ import annotations

from typing import Any

from domain.errors import ApiError, Err
from infra.db import row_json
from infra.repositories import mcp_tools


async def list_catalog() -> list[dict[str, Any]]:
    return [row_json(r) for r in await mcp_tools.list_catalog_with_annotation()]


async def save(tool_catalog_id: str, payload: dict[str, Any], by: str) -> None:
    scope_mode = payload.get("scope_mode", "none")
    appid_path = payload.get("appid_arg_path")
    if scope_mode == "required" and not (appid_path or "").strip():
        raise ApiError(Err.VALIDATION_FAILED, "scope_mode=required 时 appid_arg_path 必填")
    await mcp_tools.save_annotation(
        tool_catalog_id,
        bool(payload.get("is_approval_required", False)),
        bool(payload.get("is_secret_required", False)),
        scope_mode,
        appid_path,
        payload.get("status", "allowed"),
        payload.get("blocked_reason"),
        by,
    )
