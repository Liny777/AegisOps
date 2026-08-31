"""MCP Tool Annotation：管理台标注（07 号 4+1 字段；scope=required 必填 path）。"""
from __future__ import annotations

import logging
import os
from typing import Any

from domain import tool_key
from domain.errors import ApiError, Err
from infra.db import row_json
from infra.repositories import mcp_tools

log = logging.getLogger("openops.mcp_annotation")


async def list_catalog() -> list[dict[str, Any]]:
    """管理台目录视图：**全量**（含占位平台 MCP），管理员该看得见、也该能管。
    运行时口径见 runtime_annotations（真机会排除占位资产）。"""
    return [row_json(r) for r in await mcp_tools.list_catalog_with_annotation()]


async def runtime_annotations() -> dict[str, dict[str, Any]]:
    """运行时标注视图（task 启动时挂到 TaskState）：Gateway 校验与 ASK 判定用。

    键 = 复合键 `"{mcp_display_name}::{tool_name}"`（tool_key），值内附 mcp_display_name/tool_name
    元数据；同名工具跨 MCP 资产各留各条，不再「后者赢」互踩。裸 tool_name **仅在全局唯一时**给
    别名条目（与复合键条目同对象）——存量模板绑定与 mock demo 编排器按裸名取用的兼容缝；同名
    多家时裸名不指向任何一条（fail-closed，引导改用复合键），打 info 引导而非静默。

    未标注的工具不入 dict → Gateway 按 TOOL_NOT_ANNOTATED fail-closed（28.2 标注裁剪）。

    真机排除占位平台 MCP：seed 的「oModel 查询与恢复」(endpoint=http://mock) **自带标注**，
    复合键下虽已不会盖真 server 同名标注，但真机的运行时视图仍不该出现占位资产的 demo 工具
    （其裸名别名会抢占真 server 同名工具的唯一性）。与 38f7fb0（真机不种）/ eb695b3（真机不展示）
    同一开关、同一判定。mock/默认模式行为不变——占位 MCP 是 demo 工具标注的唯一来源。
    """
    exclude_ph = os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() == "real"
    out: dict[str, dict[str, Any]] = {}
    by_tool: dict[str, list[str]] = {}  # tool_name → [复合键...]（裸名别名判定）
    for r in await mcp_tools.list_catalog_with_annotation(exclude_placeholder=exclude_ph):
        if r.get("annotation_id") is None:
            continue
        srv, name = str(r.get("mcp_display_name") or ""), str(r["tool_name"])
        key = tool_key.make_key(srv, name)
        out[key] = {
            "is_approval_required": bool(r["is_approval_required"]),
            "is_secret_required": bool(r["is_secret_required"]),
            "is_flow_check_required": bool(r.get("is_flow_check_required") or False),
            "flow_check_config": r.get("flow_check_config") or {},
            "scope_mode": r["scope_mode"],
            "appid_arg_path": r["appid_arg_path"],
            "status": r["annotation_status"],
            "blocked_reason": r["blocked_reason"],
            "mcp_display_name": srv,
            "tool_name": name,
        }
        by_tool.setdefault(name, []).append(key)
    for name, keys in by_tool.items():
        if len(keys) == 1:
            out[name] = out[keys[0]]  # 裸名别名（同对象）：全局唯一才给
        else:
            log.info("MCP 工具同名跨 server 共存：tool=%s → %s；裸名引用对该工具不再可用，"
                     "模板绑定与标注解析请用「server::tool」复合键", name, keys)
    return out


async def save(tool_catalog_id: str, payload: dict[str, Any], by: str) -> None:
    scope_mode = payload.get("scope_mode", "none")
    appid_path = payload.get("appid_arg_path")
    if scope_mode == "required" and not (appid_path or "").strip():
        raise ApiError(Err.VALIDATION_FAILED, "scope_mode=required 时 appid_arg_path 必填")
    is_approval = bool(payload.get("is_approval_required", False))
    is_flow_check = bool(payload.get("is_flow_check_required", False))
    if is_approval and is_flow_check:  # 互斥（29.14）：DB CHECK 之外的应用层同等校验（报错更友好）
        raise ApiError(Err.VALIDATION_FAILED,
                       "is_approval_required 与 is_flow_check_required 不可同时为 true")
    flow_config = payload.get("flow_check_config") or {}
    if is_flow_check:
        _validate_flow_config(flow_config)
    await mcp_tools.save_annotation(
        tool_catalog_id,
        is_approval,
        bool(payload.get("is_secret_required", False)),
        scope_mode,
        appid_path,
        payload.get("status", "allowed"),
        payload.get("blocked_reason"),
        by,
        is_flow_check_required=is_flow_check,
        flow_check_config=flow_config if is_flow_check else {},
    )


def _validate_flow_config(cfg: Any) -> None:
    """四号校验配置完整性（29.14）：init/verify 只许 URI 路径（域名由前端 origin 拼接）。"""
    if not isinstance(cfg, dict):
        raise ApiError(Err.VALIDATION_FAILED, "flow_check_config 必须是对象")
    for key in ("init_path", "verify_path", "invoking_method"):
        if not str(cfg.get(key) or "").strip():
            raise ApiError(Err.VALIDATION_FAILED, f"flow_check_config.{key} 必填")
    for path_key in ("init_path", "verify_path"):
        val = str(cfg.get(path_key) or "")
        if not val.startswith("/") or "://" in val:
            raise ApiError(Err.VALIDATION_FAILED,
                           f"flow_check_config.{path_key} 必须是以 / 开头的 URI 路径，禁止含域名")
    sids = cfg.get("service_id_by_tenant") or cfg.get("service_id")
    if not sids:
        raise ApiError(Err.VALIDATION_FAILED, "flow_check_config 至少配置一个租户的 service_id")
    obj_path = str(cfg.get("object_arg_path") or "").strip()
    if obj_path and not obj_path.startswith("$"):
        raise ApiError(Err.VALIDATION_FAILED,
                       "flow_check_config.object_arg_path 须为 $ 开头的 JSONPath（留空表示不提取）")
