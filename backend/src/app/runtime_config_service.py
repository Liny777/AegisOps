"""Runtime Config：沙箱容量配置（ADMIN-005：改动必带 reason + 写审计）+ 平台模型注册。"""
from __future__ import annotations

import uuid
from typing import Any

from domain.errors import ApiError, Err
from infra.repositories import audit, runtime_config


async def get_sandbox() -> list[dict[str, Any]]:
    rows = await runtime_config.get_domain(runtime_config.DOMAIN_SANDBOX)
    return [
        {"key": r["config_key"], "val": r["config_value_json"], "desc": r["description"] or ""}
        for r in rows
    ]


async def update_sandbox(updates: dict[str, Any], reason: str, by: str) -> None:
    if not reason.strip():
        raise ApiError(Err.VALIDATION_FAILED, "配置修改必须填写变更原因（写入审计）")
    for key, val in updates.items():
        await runtime_config.upsert(runtime_config.DOMAIN_SANDBOX, key, val, reason=reason, by=by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="runtime_config.updated", user_id=by,
        action="update", payload_redacted={"keys": sorted(updates.keys()), "reason": reason},
        actor_type="admin",
    )


async def list_platform_models() -> list[dict[str, Any]]:
    rows = await runtime_config.get_domain(runtime_config.DOMAIN_MODEL)
    return [r["config_value_json"] for r in rows]


async def register_platform_model(model: dict[str, Any], by: str) -> None:
    mid = model.get("model_id") or model.get("name")
    if not mid:
        raise ApiError(Err.VALIDATION_FAILED, "缺少 model_id")
    safe_model = {
        k: v
        for k, v in model.items()
        if k.lower() not in {"api_key", "apikey", "authorization", "cookie", "token", "secret"}
    }
    await runtime_config.upsert(runtime_config.DOMAIN_MODEL, str(mid), safe_model, reason="register", by=by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="platform_model.registered", user_id=by,
        action="register", payload_redacted={"model_id": mid}, actor_type="admin",
    )
