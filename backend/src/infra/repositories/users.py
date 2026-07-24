"""sre_openops_user / sre_user_whitelist 仓储（列名按 19 号）。

⚠物理列 `user_role`（GaussDB 保留字 `role` 改名，2026-07-11）：写用 `user_role`，读用
`user_role AS role` 别名回逻辑名——上层（deps/identity_service/API/前端）仍见 `role`。
"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, jsonb, q_all, q_one


async def get_user(user_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select *, user_role as role from sre_openops_user where user_id=%(u)s and deleted_at is null",
        {"u": user_id},
    )


async def get_user_any(user_id: str) -> dict[str, Any] | None:
    """含软删行读（resolve_user 判「已删用户」用：已删不复活、不落库，见 identity_service）。"""
    return await q_one(
        "select *, user_role as role from sre_openops_user where user_id=%(u)s",
        {"u": user_id},
    )


async def upsert_user(user_id: str, display_name: str, role: str = "user") -> None:
    # 软删行复活：user_id 是 PK，管理员对已删用户重新加白会撞冲突分支——不清 deleted_at
    # 会让 get_user 永远拿不到行。复活按「新用户」对待（role/display_name 取传入值）；
    # 未删的活行保持只刷 last_login_at（防重复授权误降级）。登录链路对已删用户不会走到
    # 这里（resolve_user 先判 get_user_any，已删不复活）。
    await exec1(
        """
        insert into sre_openops_user (user_id, user_role, display_name, status, created_by, last_updated_by)
        values (%(u)s, %(r)s, %(d)s, 'active', 'system', 'system')
        on conflict (user_id) do update
          set last_login_at = now(), last_update_date = now(),
              user_role = case when sre_openops_user.deleted_at is not null then %(r)s else sre_openops_user.user_role end,
              display_name = case when sre_openops_user.deleted_at is not null then %(d)s else sre_openops_user.display_name end,
              status = 'active', deleted_at = null
        """,
        {"u": user_id, "r": role, "d": display_name},
    )


async def set_role(user_id: str, role: str, by: str) -> int:
    return await exec1(
        "update sre_openops_user set user_role=%(r)s, last_updated_by=%(b)s, last_update_date=now() where user_id=%(u)s",
        {"u": user_id, "r": role, "b": by},
    )


async def set_user_tags(user_id: str, tags: list[str], by: str) -> int:
    """领域标签整体替换（管理台维护；[] 即清空）。登录链路的 upsert_user 不触碰本列，不会被冲掉。"""
    return await exec1(
        "update sre_openops_user set tags_json=%(t)s, last_updated_by=%(b)s, last_update_date=now() "
        "where user_id=%(u)s and deleted_at is null",
        {"u": user_id, "t": jsonb(tags), "b": by},
    )


async def is_whitelisted(user_id: str) -> bool:
    row = await q_one(
        """
        select 1 ok from sre_user_whitelist
        where user_id=%(u)s and status='active' and deleted_at is null
          and (expire_at is null or expire_at > now())
        """,
        {"u": user_id},
    )
    return row is not None


async def list_active_whitelist() -> list[dict[str, Any]]:
    """开放查询面（免鉴权 GET /whitelist）用：仅 active 未过期行，仅 user_id/display_name 两列
    ——不带 role/last_login 等内部字段（与 is_whitelisted 同口径）。"""
    return await q_all(
        """
        select w.user_id, coalesce(u.display_name, w.user_id) display_name
        from sre_user_whitelist w
        left join sre_openops_user u on u.user_id = w.user_id and u.deleted_at is null
        where w.status='active' and w.deleted_at is null
          and (w.expire_at is null or w.expire_at > now())
        order by w.user_id
        """
    )


async def add_whitelist(user_id: str, by: str) -> None:
    # 幂等（B7·三）：已有 active 行则不再插——重复授权会让 list 的 LEFT JOIN 出重行。
    # 不用 ON CONFLICT（表无唯一索引，且 GaussDB 偏索引 target 有兼容坑），insert…select 通用。
    await exec1(
        """
        insert into sre_user_whitelist (whitelist_id, user_id, status, granted_by, granted_at, created_by, last_updated_by)
        select %(id)s, %(u)s, 'active', %(b)s, now(), %(b)s, %(b)s
        where not exists (
            select 1 from sre_user_whitelist
            where user_id=%(u)s and status='active' and deleted_at is null
        )
        """,
        {"id": str(uuid.uuid4()), "u": user_id, "b": by},
    )


async def revoke_whitelist(user_id: str, by: str) -> int:
    """移出白名单（B7·三）：软删所有 active 行；返回影响行数（0=本就不在白名单）。"""
    return await exec1(
        """
        update sre_user_whitelist
        set status='revoked', deleted_at=now(), last_updated_by=%(b)s, last_update_date=now()
        where user_id=%(u)s and status='active' and deleted_at is null
        """,
        {"u": user_id, "b": by},
    )


# 管理台用户列表 FROM/WHERE 片段：count 与分页页共用，避免两处漂移（同 assets 仓储做法）。
# tag 过滤用标准函数 jsonb_array_elements_text + EXISTS（GaussDB 安全；不用 @>/? 操作符，
# 后者与参数占位符冲突）；空数组/NULL 行产出 0 元素，自然不匹配。
_USERS_FROM = """
        from sre_openops_user u
        left join sre_user_whitelist w
          on w.user_id = u.user_id and w.deleted_at is null
        where u.deleted_at is null
          and (%(qlike)s::text is null or u.user_id ilike %(qlike)s or u.display_name ilike %(qlike)s)
          and (%(tag)s::text is null or exists (
                select 1 from jsonb_array_elements_text(u.tags_json) e where e = %(tag)s))
"""


async def list_users_with_whitelist(
    *, q: str | None = None, tag: str | None = None, limit: int = 20, offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """管理台分页列表 → (rows, total)：q 按 user_id/display_name 模糊搜、tag 按领域标签精确过滤
    （均服务端过滤，29.3 口径；tag 与 q 为 AND 组合）。"""
    p: dict[str, Any] = {"qlike": f"%{q}%" if q else None, "tag": tag}
    total = int(((await q_one(f"select count(*) as n{_USERS_FROM}", p)) or {}).get("n") or 0)
    rows = await q_all(
        f"""
        select u.user_id, u.display_name, u.user_role as role, u.tags_json, u.last_login_at,
               coalesce(w.status, 'none') whitelist_status
        {_USERS_FROM}
        order by u.user_id limit %(lim)s offset %(off)s
        """,
        {**p, "lim": limit, "off": offset},
    )
    return rows, total


async def list_all_tags() -> list[str]:
    """所有未删用户已用的领域标签（去重、排序）——管理台标签下拉候选。
    作用域与用户列表一致（deleted_at is null）；空数组/NULL 行不产标签。"""
    rows = await q_all(
        """
        select distinct e as tag
        from sre_openops_user u, jsonb_array_elements_text(u.tags_json) e
        where u.deleted_at is null and u.tags_json is not null
        order by tag
        """
    )
    return [str(r["tag"]) for r in rows]


async def soft_delete_user(user_id: str, by: str) -> int:
    """删除用户（管理台）：软删 sre_openops_user 行；返回影响行数（0=不存在或已删）。"""
    return await exec1(
        """
        update sre_openops_user
        set deleted_at=now(), status='deleted', last_updated_by=%(b)s, last_update_date=now()
        where user_id=%(u)s and deleted_at is null
        """,
        {"u": user_id, "b": by},
    )
