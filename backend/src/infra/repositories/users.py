"""openops_user / user_whitelist 仓储（列名按 19 号）。"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, q_all, q_one


async def get_user(user_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from openops_user where user_id=%(u)s and deleted_at is null", {"u": user_id}
    )


async def upsert_user(user_id: str, display_name: str, role: str = "user") -> None:
    await exec1(
        """
        insert into openops_user (user_id, role, display_name, status, created_by, last_updated_by)
        values (%(u)s, %(r)s, %(d)s, 'active', 'system', 'system')
        on conflict (user_id) do update
          set last_login_at = now(), last_update_date = now()
        """,
        {"u": user_id, "r": role, "d": display_name},
    )


async def set_role(user_id: str, role: str, by: str) -> int:
    return await exec1(
        "update openops_user set role=%(r)s, last_updated_by=%(b)s, last_update_date=now() where user_id=%(u)s",
        {"u": user_id, "r": role, "b": by},
    )


async def is_whitelisted(user_id: str) -> bool:
    row = await q_one(
        """
        select 1 ok from user_whitelist
        where user_id=%(u)s and status='active' and deleted_at is null
          and (expire_at is null or expire_at > now())
        """,
        {"u": user_id},
    )
    return row is not None


async def add_whitelist(user_id: str, by: str) -> None:
    await exec1(
        """
        insert into user_whitelist (whitelist_id, user_id, status, granted_by, granted_at, created_by, last_updated_by)
        values (%(id)s, %(u)s, 'active', %(b)s, now(), %(b)s, %(b)s)
        """,
        {"id": str(uuid.uuid4()), "u": user_id, "b": by},
    )


async def list_users_with_whitelist() -> list[dict[str, Any]]:
    return await q_all(
        """
        select u.user_id, u.display_name, u.role, u.last_login_at,
               coalesce(w.status, 'none') whitelist_status
        from openops_user u
        left join user_whitelist w
          on w.user_id = u.user_id and w.deleted_at is null
        where u.deleted_at is null
        order by u.user_id
        """
    )
