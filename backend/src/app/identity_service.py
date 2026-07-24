"""Identity & Admission：/me 聚合（白名单事实在 PG，不看请求头声称）。"""
from __future__ import annotations

import uuid
from typing import Any

from domain.errors import ApiError, Err
from infra.repositories import agent_teams, audit, users


async def resolve_user(user_id: str, display_name: str | None) -> dict[str, Any]:
    """登录态解析：首登 upsert（角色以 DB 为准，默认 user）。已删用户不复活、不落库——
    否则被删用户随便发一个请求就重建行、回到管理台列表；按未开通普通用户对待（白名单
    闸自然拦截），仅管理员重新加白名单才复活（add_whitelist → upsert_user 清 deleted_at）。"""
    row = await users.get_user(user_id)
    if row is None:
        existing = await users.get_user_any(user_id)
        if existing is not None:  # 软删行：只读身份，不写库
            return {
                "user_id": user_id,
                "display_name": existing["display_name"] or display_name or user_id,
                "role": "user",  # 不沿用删除前角色（删除即收回全部授权）
                "whitelisted": False,
            }
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
async def list_users(*, page: int = 1, page_size: int = 20, q: str | None = None,
                     tag: str | None = None) -> dict[str, Any]:
    """分页 + 关键字搜索（q 按 user_id/display_name 模糊）+ 标签过滤（tag 精确，与 q 为 AND）
    → {items,total,page,page_size}。均服务端过滤。"""
    rows, total = await users.list_users_with_whitelist(
        q=(q.strip() or None) if q else None, tag=(tag.strip() or None) if tag else None,
        limit=page_size, offset=(page - 1) * page_size,
    )
    return {"items": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


async def list_user_tags() -> list[str]:
    """管理台标签下拉候选：所有未删用户已用的领域标签（去重、排序）。"""
    return await users.list_all_tags()


# ---- 开放查询面（免鉴权 GET /whitelist，老项目 1cd7ef0 口径）：外部系统判断
#      是否展示 OpenOps 跳转入口；只读、只出 user_id/display_name/是否开通 ----
async def whitelist_overview() -> list[dict[str, Any]]:
    return [dict(r) for r in await users.list_active_whitelist()]


async def check_whitelist(user_id: str) -> bool:
    return await users.is_whitelisted(user_id)


async def set_role(user_id: str, role: str, by: str) -> dict[str, Any]:
    """改角色（B7·三补链）：升/降级已有用户。此前 set_role 仓储无人调用——
    加白名单的 upsert 对已存在用户不改 role（防重复授权时误降级），升级老用户
    只能直改库且绕审计。守卫：不能改自己的角色（唯一管理员自降会锁死管理面，
    与 revoke_whitelist 同思路）。"""
    if user_id == by:
        raise ApiError(Err.VALIDATION_FAILED, "不能修改自己的角色（防止管理面锁死）")
    row = await users.get_user(user_id)
    if row is None:
        raise ApiError(Err.NOT_FOUND, f"用户不存在：{user_id}（先加入白名单会自动建行）")
    old_role = row["role"]
    if old_role != role:
        await users.set_role(user_id, role, by)
        await audit.insert_event(  # 管理面动作必审计（B7·三）
            audit_trace_id=str(uuid.uuid4()), event_type="role.changed", user_id=by,
            action="set_role", actor_type="user",
            payload_redacted={"target_user_id": user_id, "from": old_role, "to": role},
        )
    return {"user_id": user_id, "role": role, "changed": old_role != role}


def _norm_tags(tags: list[str]) -> list[str]:
    """标签规整：strip、去空、按序去重（管理台输入逗号分隔切出来的原料）。"""
    out: list[str] = []
    for t in tags:
        t = t.strip()
        if t and t not in out:
            out.append(t)
    return out


async def add_whitelist(user_id: str, display_name: str, role: str, by: str,
                        tags: list[str] | None = None) -> None:
    await users.upsert_user(user_id, display_name or user_id, role)
    await users.add_whitelist(user_id, by)
    if tags is not None:  # None=不动（老调用方/重复加白不冲已有标签）；[] 即清空
        await users.set_user_tags(user_id, _norm_tags(tags), by)
    await audit.insert_event(  # 管理面动作必审计（B7·三）
        audit_trace_id=str(uuid.uuid4()), event_type="whitelist.granted", user_id=by,
        action="grant", actor_type="user",
        payload_redacted={"target_user_id": user_id, **({"tags": _norm_tags(tags)} if tags is not None else {})},
    )


async def set_tags(user_id: str, tags: list[str], by: str) -> dict[str, Any]:
    """改领域标签（整体替换，[] 清空）。标签无权限含义，改自己不设防（与 set_role 相反）。"""
    row = await users.get_user(user_id)
    if row is None:
        raise ApiError(Err.NOT_FOUND, f"用户不存在：{user_id}（先加入白名单会自动建行）")
    new = _norm_tags(tags)
    old = row.get("tags_json") or []
    if old != new:
        await users.set_user_tags(user_id, new, by)
        await audit.insert_event(  # 管理面动作必审计（对齐 role.changed）
            audit_trace_id=str(uuid.uuid4()), event_type="user.tags_changed", user_id=by,
            action="set_tags", actor_type="user",
            payload_redacted={"target_user_id": user_id, "from": old, "to": new},
        )
    return {"user_id": user_id, "tags": new, "changed": old != new}


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


async def delete_user(user_id: str, by: str) -> None:
    """删除用户（管理台）：软删用户行 + 连带撤销白名单（不撤的话 is_whitelisted 仍放行）。
    守卫同 revoke：不能删自己（防管理面锁死）。已删/不存在 → 404。删除后该用户再登录
    **不复活**（resolve_user 按未开通普通用户对待），仅管理员重新加白名单才复活。"""
    if user_id == by:
        raise ApiError(Err.VALIDATION_FAILED, "不能删除自己（防止管理面锁死）")
    n = await users.soft_delete_user(user_id, by)
    if n == 0:
        raise ApiError(Err.NOT_FOUND, f"用户不存在：{user_id}")
    revoked = await users.revoke_whitelist(user_id, by)  # 可能本就不在白名单，0 行不算错
    await audit.insert_event(  # 管理面动作必审计（B7·三）
        audit_trace_id=str(uuid.uuid4()), event_type="user.deleted", user_id=by,
        action="delete", actor_type="user",
        payload_redacted={"target_user_id": user_id, "whitelist_revoked": revoked > 0},
    )
