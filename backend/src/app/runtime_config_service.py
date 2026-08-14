"""Runtime Config：沙箱容量配置（ADMIN-005：改动必带 reason + 写审计）。

平台模型自 B7 起迁至 model_asset（app/model_asset_service.py），本服务不再承载模型注册。
"""
from __future__ import annotations

import uuid
from typing import Any

from domain.errors import ApiError, Err
from infra.repositories import audit, runtime_config


def _pos_int(v: Any) -> bool:
    try:
        return int(str(v).strip()) > 0
    except (TypeError, ValueError):
        return False


def _nonneg_int(v: Any) -> bool:
    try:
        return int(str(v).strip()) >= 0
    except (TypeError, ValueError):
        return False


def _pos_float(v: Any) -> bool:
    try:
        return float(str(v).strip()) > 0
    except (TypeError, ValueError):
        return False


# 已知沙箱键写时校验（管理台编辑值按字符串回传，校验函数须容忍字符串形态）；未列键仍放行不变。
_SANDBOX_CHECKS = {
    "container_network_mode": lambda v: str(v).strip().lower() in {"bridge", "none"},
    "container_pids_limit": _pos_int,
    "container_memory_limit_mib": _pos_int,
    "max_user_containers_per_host": _pos_int,
    "per_user_running_task_limit": _pos_int,
    "model_total_concurrency": _pos_int,
    "reserve_interactive": _nonneg_int,  # 0=交互无保底（全走弹性），合法
    "reserve_alert": _nonneg_int,
    "user_container_idle_ttl_minutes": _nonneg_int,
    "run_idle_ttl_minutes": _pos_int,  # 用 _pos_int：0 会把刚建的静默 run 立刻回收
    "capacity_full_policy": lambda v: str(v).strip().lower() == "strict_ttl",  # V1 仅支持 strict_ttl，拦非法值

    "container_cpu_limit": _pos_float,
    "container_image": lambda v: bool(str(v).strip()),
}


async def get_sandbox() -> list[dict[str, Any]]:
    rows = await runtime_config.get_domain(runtime_config.DOMAIN_SANDBOX)
    return [
        {"key": r["config_key"], "val": r["config_value_json"], "desc": r["description"] or ""}
        for r in rows
    ]


async def update_sandbox(updates: dict[str, Any], reason: str, by: str) -> None:
    if not reason.strip():
        raise ApiError(Err.VALIDATION_FAILED, "配置修改必须填写变更原因（写入审计）")
    # 先整批校验再落库：一个非法键不落任何库，避免半应用
    for key, val in updates.items():
        check = _SANDBOX_CHECKS.get(key)
        if check is not None and not check(val):
            raise ApiError(Err.VALIDATION_FAILED, f"沙箱配置项 {key} 取值非法：{val!r}")
    for key, val in updates.items():
        await runtime_config.upsert(runtime_config.DOMAIN_SANDBOX, key, val, reason=reason, by=by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="runtime_config.updated", user_id=by,
        action="update", payload_redacted={"keys": sorted(updates.keys()), "reason": reason},
        actor_type="admin",
    )
