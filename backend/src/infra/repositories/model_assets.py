"""sre_model_asset / sre_model_access_grant 仓储（B7：模型资产 + 按人白名单授权）。"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, q_all, q_one


async def list_all() -> list[dict[str, Any]]:
    """管理台清单：含每模型 active 授权人数。"""
    return await q_all(
        """
        select m.*, coalesce(g.cnt, 0) grant_count
        from sre_model_asset m
        left join lateral (
          select count(*) cnt from sre_model_access_grant ga
          where ga.model_asset_id = m.model_asset_id and ga.deleted_at is null and ga.status='active'
        ) g on true
        where m.deleted_at is null
        order by m.creation_date
        """
    )


async def get(model_asset_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_model_asset where model_asset_id=%(i)s and deleted_at is null", {"i": model_asset_id}
    )


async def get_by_model_id(model_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_model_asset where model_id=%(m)s and deleted_at is null", {"m": model_id}
    )


async def create(
    display_name: str, protocol: str, model_id: str, base_url: str | None,
    secret_env_var: str | None, access_scope: str, status: str, by: str,
    context_window_tokens: int = 128000,
) -> dict[str, Any]:
    mid = str(uuid.uuid4())
    await exec1(
        """
        insert into sre_model_asset
          (model_asset_id, display_name, protocol, model_id, base_url, secret_env_var,
           context_window_tokens, access_scope, status, registered_by, created_by, last_updated_by)
        values (%(i)s, %(d)s, %(p)s, %(m)s, %(u)s, %(e)s, %(cw)s, %(a)s, %(s)s, %(b)s, %(b)s, %(b)s)
        """,
        {"i": mid, "d": display_name, "p": protocol, "m": model_id, "u": base_url,
         "e": secret_env_var, "cw": context_window_tokens, "a": access_scope, "s": status, "b": by},
    )
    return (await get(mid))  # type: ignore[return-value]


async def set_status(model_asset_id: str, status: str, by: str) -> int:
    return await exec1(
        """
        update sre_model_asset set status=%(s)s, last_updated_by=%(b)s, last_update_date=now()
        where model_asset_id=%(i)s and deleted_at is null
        """,
        {"i": model_asset_id, "s": status, "b": by},
    )


async def set_access_scope(model_asset_id: str, access_scope: str, by: str) -> int:
    return await exec1(
        """
        update sre_model_asset set access_scope=%(a)s, last_updated_by=%(b)s, last_update_date=now()
        where model_asset_id=%(i)s and deleted_at is null
        """,
        {"i": model_asset_id, "a": access_scope, "b": by},
    )


async def list_grants(model_asset_id: str) -> list[dict[str, Any]]:
    return await q_all(
        """
        select * from sre_model_access_grant
        where model_asset_id=%(i)s and deleted_at is null and status='active'
        order by creation_date
        """,
        {"i": model_asset_id},
    )


async def replace_grants(model_asset_id: str, user_ids: list[str], by: str) -> None:
    """整体替换授权：软删全部现行 active 行 + 插入新集合（不原地改写，授权变迁可审计回放）。"""
    await exec1(
        """
        update sre_model_access_grant set deleted_at=now(), status='revoked',
               last_updated_by=%(b)s, last_update_date=now()
        where model_asset_id=%(i)s and deleted_at is null
        """,
        {"i": model_asset_id, "b": by},
    )
    for uid in dict.fromkeys(user_ids):  # 去重保序
        await exec1(
            """
            insert into sre_model_access_grant
              (grant_id, model_asset_id, user_id, granted_by, status, created_by, last_updated_by)
            values (%(g)s, %(i)s, %(u)s, %(b)s, 'active', %(b)s, %(b)s)
            """,
            {"g": str(uuid.uuid4()), "i": model_asset_id, "u": uid, "b": by},
        )


async def has_grant(model_asset_id: str, user_id: str) -> bool:
    row = await q_one(
        """
        select 1 ok from sre_model_access_grant
        where model_asset_id=%(i)s and user_id=%(u)s and deleted_at is null and status='active'
        """,
        {"i": model_asset_id, "u": user_id},
    )
    return row is not None


async def list_available_for_user(user_id: str) -> list[dict[str, Any]]:
    """该用户可用的 active 模型：scope=all ∪ 有 active 授权（fail-closed 的正向集合）。"""
    return await q_all(
        """
        select m.* from sre_model_asset m
        where m.deleted_at is null and m.status='active'
          and ( m.access_scope='all'
             or exists (select 1 from sre_model_access_grant g
                        where g.model_asset_id=m.model_asset_id and g.user_id=%(u)s
                          and g.deleted_at is null and g.status='active') )
        order by m.creation_date
        """,
        {"u": user_id},
    )
