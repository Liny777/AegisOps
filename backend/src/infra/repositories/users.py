"""sre_openops_user / sre_user_whitelist 仓储（列名按 19 号）。

⚠物理列 `user_role`（GaussDB 保留字 `role` 改名，2026-07-11）：写用 `user_role`，读用
`user_role AS role` 别名回逻辑名——上层（deps/identity_service/API/前端）仍见 `role`。
"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, q_all, q_one


async def get_user(user_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select *, user_role as role from sre_openops_user where user_id=%(u)s and deleted_at is null",
        {"u": user_id},
    )


async def upsert_user(user_id: str, display_name: str, role: str = "user") -> None:
    await exec1(
        """
        insert into sre_openops_user (user_id, user_role, display_name, status, created_by, last_updated_by)
        values (%(u)s, %(r)s, %(d)s, 'active', 'system', 'system')
        on conflict (user_id) do update
          set last_login_at = now(), last_update_date = now()
        """,
        {"u": user_id, "r": role, "d": display_name},
    )


async def set_role(user_id: str, role: str, by: str) -> int:
    return await exec1(
        "update sre_openops_user set user_role=%(r)s, last_updated_by=%(b)s, last_update_date=now() where user_id=%(u)s",
        {"u": user_id, "r": role, "b": by},
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


async def list_users_with_whitelist() -> list[dict[str, Any]]:
    return await q_all(
        """
        select u.user_id, u.display_name, u.user_role as role, u.last_login_at,
               coalesce(w.status, 'none') whitelist_status
        from sre_openops_user u
        left join sre_user_whitelist w
          on w.user_id = u.user_id and w.deleted_at is null
        where u.deleted_at is null
        order by u.user_id
        """
    )
