"""模型资产与白名单授权（B7，30.6 五 / 18 号 model_asset·model_access_grant）。

- 授权按人（不引入部门维度，2026-07-09 拍板）；`access_scope='all'` 全员开放。
- 注册走 DTO 白名单字段（api_key/token 等敏感键天然进不来）；Key 只经 `secret_env_var` 环境变量名。
- fail-closed 三处 gating：列表过滤（本服务 list_available）、select-model 校验（is_authorized）、
  Model Gateway 运行时解析二次校验（app/model_gateway.py）。
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from domain.errors import ApiError, Err
from infra.db import row_json
from infra.repositories import audit, model_assets

# secret_env_var 是环境变量名（非 Key 本身）：大写惯例。实测有管理员把真实 Key 填进来 → 明文落库
# + 日志泄漏（SEC-001），入口处直接拒绝。
_ENV_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


async def admin_list() -> list[dict[str, Any]]:
    return [row_json(r) for r in await model_assets.list_all()]


async def register(req: Any, by: str) -> dict[str, Any]:
    if await model_assets.get_by_model_id(req.model_id):
        raise ApiError(Err.VALIDATION_FAILED, f"model_id 已存在：{req.model_id}")
    if req.secret_env_var and not _ENV_VAR_RE.match(req.secret_env_var):
        raise ApiError(Err.VALIDATION_FAILED,
                       "「API Key 环境变量名」应填变量名（大写字母/数字/下划线，如 OPENOPS_PLATFORM_GLM_API_KEY）"
                       "——看起来填入了 API Key 本身：真实 Key 绝不落库，请把 Key 配到后端进程环境变量"
                       "（run-backend 里 export），此处只填变量名")
    row = await model_assets.create(
        req.display_name, req.protocol, req.model_id, req.base_url,
        req.secret_env_var, req.access_scope, "active", by,
    )
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="model_asset.registered", user_id=by,
        action="register", payload_redacted={"model_id": req.model_id, "access_scope": req.access_scope},
    )
    return row_json(row)


async def set_status(model_asset_id: str, status: str, by: str) -> None:
    n = await model_assets.set_status(model_asset_id, status, by)
    if n == 0:
        raise ApiError(Err.NOT_FOUND, "模型资产不存在")
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="model_asset.status_changed", user_id=by,
        action=status, payload_redacted={"model_asset_id": model_asset_id},
    )


async def get_grants(model_asset_id: str) -> dict[str, Any]:
    m = await model_assets.get(model_asset_id)
    if m is None:
        raise ApiError(Err.NOT_FOUND, "模型资产不存在")
    return {
        "model_asset_id": model_asset_id,
        "access_scope": m["access_scope"],
        "user_ids": [g["user_id"] for g in await model_assets.list_grants(model_asset_id)],
    }


async def save_grants(model_asset_id: str, req: Any, by: str) -> dict[str, Any]:
    """保存授权：scope + 人员集合（软删+插新）；all 时忽略 user_ids。写审计（含人数不含逐人全文）。"""
    m = await model_assets.get(model_asset_id)
    if m is None:
        raise ApiError(Err.NOT_FOUND, "模型资产不存在")
    await model_assets.set_access_scope(model_asset_id, req.access_scope, by)
    user_ids = [] if req.access_scope == "all" else list(req.user_ids)
    await model_assets.replace_grants(model_asset_id, user_ids, by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="model_asset.grants_updated", user_id=by,
        action="save_grants",
        payload_redacted={"model_asset_id": model_asset_id, "access_scope": req.access_scope,
                          "granted_count": len(user_ids)},
    )
    return await get_grants(model_asset_id)


async def list_available(user: dict[str, Any]) -> list[dict[str, Any]]:
    """用户可见平台模型（30.5 ModelTab / 工作台 ModelPicker 数据源）：按授权过滤。"""
    return [row_json(r) for r in await model_assets.list_available_for_user(user["user_id"])]


async def is_authorized(user_id: str, model_id: str) -> bool:
    """select-model / Model Gateway 校验：未知模型、禁用、restricted 白名单外一律 False（fail-closed）。"""
    m = await model_assets.get_by_model_id(model_id)
    if m is None or m["status"] != "active":
        return False
    if m["access_scope"] == "all":
        return True
    return await model_assets.has_grant(str(m["model_asset_id"]), user_id)
