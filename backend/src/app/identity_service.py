"""Identity & Admission：/me 聚合（白名单事实在 PG，不看请求头声称）。"""
from __future__ import annotations

from typing import Any

from infra.repositories import agent_teams, users


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


async def add_whitelist(user_id: str, display_name: str, role: str, by: str) -> None:
    await users.upsert_user(user_id, display_name or user_id, role)
    await users.add_whitelist(user_id, by)
