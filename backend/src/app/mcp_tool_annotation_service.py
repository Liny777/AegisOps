"""MCP Tool Annotation：管理台标注（07 号 4+1 字段；scope=required 必填 path）。"""
from __future__ import annotations

import logging
import os
from typing import Any

from domain.errors import ApiError, Err
from infra.db import row_json
from infra.repositories import mcp_tools

log = logging.getLogger("openops.mcp_annotation")


async def list_catalog() -> list[dict[str, Any]]:
    """管理台目录视图：**全量**（含占位平台 MCP），管理员该看得见、也该能管。
    运行时口径见 runtime_annotations（真机会排除占位资产）。"""
    return [row_json(r) for r in await mcp_tools.list_catalog_with_annotation()]


async def runtime_annotations() -> dict[str, dict[str, Any]]:
    """运行时标注视图（by tool_name，task 启动时挂到 TaskState）：Gateway 校验与 ASK 判定用。

    未标注的工具不入 dict → Gateway 按 TOOL_NOT_ANNOTATED fail-closed（28.2 标注裁剪）。

    真机排除占位平台 MCP：seed 的「oModel 查询与恢复」(endpoint=http://mock) **自带标注**
    （`recover_execute` 还是需审批），而本函数按 tool_name 扁平收敛、SQL 按 display_name 排序即
    「后者赢」——中文名排在 `alarm-server` 这类 ASCII 名之后，于是真 server 上同名工具的管理员标注
    会被占位那条静默盖掉，表现为「管理台设了不生效」。与 38f7fb0（真机不种）/ eb695b3（真机不展示）
    同一开关、同一判定，是这条线的第三步：门控挡新库、隐藏挡展示、这条挡运行时取值。
    mock/默认模式行为不变——占位 MCP 正是 demo 工具标注的唯一来源，本地端到端与用例都依赖它。
    """
    exclude_ph = os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() == "real"
    out: dict[str, dict[str, Any]] = {}
    seen: dict[str, str] = {}  # tool_name → 已收下那条所属 MCP（仅用于冲突告警）
    for r in await mcp_tools.list_catalog_with_annotation(exclude_placeholder=exclude_ph):
        if r.get("annotation_id") is None:
            continue
        name = str(r["tool_name"])
        # 同名工具跨 MCP 资产是合法的（唯一约束只到 (mcp_version_id, tool_name)），但本视图是扁平
        # name→标注 映射，只能留一条。取值口径保持不变（后者赢），但**不再静默**：打出冲突让
        # 「我明明标了却不生效」可诊断。真要区分需按 server 维度解析标注（更大的改动，另议）。
        if name in seen:
            log.warning("MCP 工具标注同名冲突：tool=%s 同时来自「%s」与「%s」，运行时取后者；"
                        "如需区分请重命名工具或下线其中一个 MCP", name, seen[name], r.get("mcp_display_name"))
        seen[name] = str(r.get("mcp_display_name") or "")
        out[name] = {
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
