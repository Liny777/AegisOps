"""MCP Tool Annotation：管理台标注（07 号 4+1 字段；scope=required 必填 path）。"""
from __future__ import annotations

from typing import Any

from domain.errors import ApiError, Err
from infra.db import row_json
from infra.repositories import mcp_tools


async def list_catalog() -> list[dict[str, Any]]:
    return [row_json(r) for r in await mcp_tools.list_catalog_with_annotation()]


async def runtime_annotations() -> dict[str, dict[str, Any]]:
    """运行时标注视图（by tool_name，task 启动时挂到 TaskState）：Gateway 校验与 ASK 判定用。

    未标注的工具不入 dict → Gateway 按 TOOL_NOT_ANNOTATED fail-closed（28.2 标注裁剪）。
    """
    out: dict[str, dict[str, Any]] = {}
    for r in await mcp_tools.list_catalog_with_annotation():
        if r.get("annotation_id") is None:
            continue
        out[r["tool_name"]] = {
            "is_approval_required": bool(r["is_approval_required"]),
            "is_secret_required": bool(r["is_secret_required"]),
            "scope_mode": r["scope_mode"],
            "appid_arg_path": r["appid_arg_path"],
            "status": r["annotation_status"],
            "blocked_reason": r["blocked_reason"],
        }
    return out


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
