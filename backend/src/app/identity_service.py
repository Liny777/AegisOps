"""Identity & Admission：/me 聚合（白名单事实在 PG，不看请求头声称）。"""
from __future__ import annotations

import uuid
from typing import Any

from domain.errors import ApiError, Err
from infra.repositories import agent_teams, audit, users


async def resolve_user(user_id: str, display_name: str | None) -> dict[str, Any]:
    """登录态解析：首登 upsert（角色以 DB 为准，默认 user）。"""
    row = await users.get_user(user_id)
    if row is None:
        await users.upsert_user(user_id, display_name or user_id, "user")
        row = await users.get_user(user_id)
    assert row is not None
    wl = await users.is_whitelisted(user_id)
    return {
        "user_id": row["user_id"],
        "display_name": row["display_name"],
        "role": row["role"],
        "whitelisted": wl,
    }


async def me(user: dict[str, Any]) -> dict[str, Any]:
    from app import asset_reconcile_service  # 局部导入避免环依赖

    asset_reconcile_service.kick_async("login")  # 登录对账（28.7）：节流 fire-and-forget，不阻塞 /me
    instances = await agent_teams.list_by_owner(user["user_id"])
    active = [i for i in instances if i["status"] == "active"]
    return {
        "user_id": user["user_id"],
        "display_name": user["display_name"],
        "role": user["role"],
        "whitelisted": user["whitelisted"],
        "has_instances": len(active) > 0,
        "recent_instance_id": str(active[-1]["agent_team_instance_id"]) if active else None,
    }


# ---- 管理台：用户与白名单（router 不直连 users repo，走本服务） ----
async def list_users() -> list[dict[str, Any]]:
    return [dict(r) for r in await users.list_users_with_whitelist()]


# ---- 开放查询面（免鉴权 GET /whitelist，老项目 1cd7ef0 口径）：外部系统判断
#      是否展示 OpenOps 跳转入口；只读、只出 user_id/display_name/是否开通 ----
async def whitelist_overview() -> list[dict[str, Any]]:
    return [dict(r) for r in await users.list_active_whitelist()]


async def check_whitelist(user_id: str) -> bool:
    return await users.is_whitelisted(user_id)


async def add_whitelist(user_id: str, display_name: str, role: str, by: str) -> None:
    await users.upsert_user(user_id, display_name or user_id, role)
    await users.add_whitelist(user_id, by)
    await audit.insert_event(  # 管理面动作必审计（B7·三）
        audit_trace_id=str(uuid.uuid4()), event_type="whitelist.granted", user_id=by,
        action="grant", actor_type="user", payload_redacted={"target_user_id": user_id},
    )


async def revoke_whitelist(user_id: str, by: str) -> None:
    """移出白名单（B7·三）：软删 active 行；管理员不能移除自己（防锁死管理面）。"""
    if user_id == by:
        raise ApiError(Err.VALIDATION_FAILED, "不能移除自己的白名单（防止管理面锁死）")
    n = await users.revoke_whitelist(user_id, by)
    if n == 0:
        raise ApiError(Err.NOT_FOUND, "该用户不在白名单中")
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="whitelist.revoked", user_id=by,
        action="revoke", actor_type="user", payload_redacted={"target_user_id": user_id},
    )
