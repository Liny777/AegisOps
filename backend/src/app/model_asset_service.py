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
        context_window_tokens=getattr(req, "context_window_tokens", 128000),
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


def _default_model_id(rows: list[dict[str, Any]]) -> str | None:
    """与 [[model_gateway.resolve_runtime_model]] 完全同口径的平台默认解析：
    优先 `OPENOPS_RUNTIME_MODEL`（默认 glm-5.1），否则首个带 `secret_env_var` 的模型。
    默认**必须带 Key** 才成立——否则运行时回退 stub。避免把无 Key 的种子资产（如 Qwen3.5-千问）
    当默认却根本跑不起来（前端初始化向导据此展示真实默认名，而非写死）。"""
    from app.model_gateway import DEFAULT_RUNTIME_MODEL

    by_id = {r.get("model_id"): r for r in rows}
    target = by_id.get(DEFAULT_RUNTIME_MODEL) or next((r for r in rows if r.get("secret_env_var")), None)
    if target is None or not target.get("secret_env_var"):
        return None
    return target.get("model_id")


async def list_available(user: dict[str, Any]) -> list[dict[str, Any]]:
    """用户可见平台模型（30.5 ModelTab / 工作台 ModelPicker 数据源）：按授权过滤。
    每行带 `is_default`（本用户授权范围内运行时实际会用的那个，同 model_gateway 口径），
    供初始化向导等直接展示真实平台默认模型。"""
    rows = await model_assets.list_available_for_user(user["user_id"])
    default_id = _default_model_id(rows)
    return [{**row_json(r), "is_default": r.get("model_id") == default_id} for r in rows]


async def test_connection(req: Any) -> dict[str, Any]:
    """平台模型「测试连接」：Key 从服务器环境变量取（secret_env_var 是变量名，客户端不持 Key）。
    egress SSRF 校验 + tool-calling 探测。返回 {ok, supports_tool_calling, reason}。"""
    import os

    from infra import egress
    from infra.external import llm_provider_client

    env_var = (req.secret_env_var or "").strip()
    if env_var and not _ENV_VAR_RE.match(env_var):
        return {"ok": False, "supports_tool_calling": False,
                "reason": "「API Key 环境变量名」应填变量名（大写字母/数字/下划线），此处像是填了 Key 本身"}
    api_key = os.environ.get(env_var) if env_var else None
    if env_var and not api_key:
        return {"ok": False, "supports_tool_calling": False,
                "reason": f"服务器进程未配置环境变量 {env_var}（Key 未注入，无法测试）"}
    base_url = (req.base_url or "").strip()
    if not base_url:
        return {"ok": False, "supports_tool_calling": False,
                "reason": "该模型未配置 base_url，无法测试连接（走平台网关的模型可不填而直接保存）"}
    try:
        egress.check_llm_egress(base_url)
    except ApiError as e:
        return {"ok": False, "supports_tool_calling": False, "reason": e.message}
    probe = await llm_provider_client.probe(base_url, req.model_id, api_key)
    return {"ok": bool(probe["ok"] and probe["supports_tool_calling"]),
            "supports_tool_calling": bool(probe["supports_tool_calling"]),
            "reason": probe.get("reason")}


async def is_authorized(user_id: str, model_id: str) -> bool:
    """select-model / Model Gateway 校验：未知模型、禁用、restricted 白名单外一律 False（fail-closed）。"""
    m = await model_assets.get_by_model_id(model_id)
    if m is None or m["status"] != "active":
        return False
    if m["access_scope"] == "all":
        return True
    return await model_assets.has_grant(str(m["model_asset_id"]), user_id)
