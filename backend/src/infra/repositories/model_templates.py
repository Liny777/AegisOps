"""sre_model_template 仓储：模型模板（主/子 Agent 槽位）——管理面 CRUD + 用户侧/运行时读。"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, q_all, q_one

# 主/子槽位资产展示投影。left join 容悬挂引用：资产被软删时展示列为 NULL，
# 由服务层折叠为 missing 槽位（admin 列表可标注），不 500。
_JOINED = """
select t.*,
       ma.model_id main_model_id, ma.display_name main_model_name, ma.status main_model_status,
       sa.model_id sub_model_id, sa.display_name sub_model_name, sa.status sub_model_status
from sre_model_template t
left join sre_model_asset ma on ma.model_asset_id = t.main_model_asset_id and ma.deleted_at is null
left join sre_model_asset sa on sa.model_asset_id = t.sub_model_asset_id and sa.deleted_at is null
"""


async def list_all() -> list[dict[str, Any]]:
    return await q_all(_JOINED + " where t.deleted_at is null order by t.creation_date")


async def list_active() -> list[dict[str, Any]]:
    return await q_all(_JOINED + " where t.deleted_at is null and t.status='active' order by t.creation_date")


async def get(model_template_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_model_template where model_template_id=%(i)s and deleted_at is null",
        {"i": model_template_id},
    )


async def get_by_name(display_name: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_model_template where display_name=%(d)s and deleted_at is null",
        {"d": display_name},
    )


async def create(display_name: str, description: str | None,
                 main_model_asset_id: str, sub_model_asset_id: str, by: str) -> dict[str, Any]:
    """恒 is_default=false 落库：默认切换必须走 set_default 两步，
    带 true 直插会撞 ux_model_template_default 部分唯一索引。"""
    tid = str(uuid.uuid4())
    await exec1(
        """
        insert into sre_model_template
          (model_template_id, display_name, description, main_model_asset_id, sub_model_asset_id,
           is_default, status, created_by, last_updated_by)
        values (%(i)s, %(d)s, %(desc)s, %(m)s, %(s)s, false, 'active', %(b)s, %(b)s)
        """,
        {"i": tid, "d": display_name, "desc": description, "m": main_model_asset_id,
         "s": sub_model_asset_id, "b": by},
    )
    return (await get(tid))  # type: ignore[return-value]


async def set_default(model_template_id: str, by: str) -> int:
    """两步切换：先清旧默认再置新。顺序保证不撞部分唯一索引（行序即时校验）；
    两步间短暂「无默认」只影响选单预选，运行时解析不吃 is_default。"""
    await exec1(
        """
        update sre_model_template set is_default=false, last_updated_by=%(b)s, last_update_date=now()
        where is_default and deleted_at is null and model_template_id <> %(i)s
        """,
        {"i": model_template_id, "b": by},
    )
    return await exec1(
        """
        update sre_model_template set is_default=true, last_updated_by=%(b)s, last_update_date=now()
        where model_template_id=%(i)s and deleted_at is null
        """,
        {"i": model_template_id, "b": by},
    )


async def set_status(model_template_id: str, status: str, by: str) -> int:
    return await exec1(
        """
        update sre_model_template set status=%(s)s, last_updated_by=%(b)s, last_update_date=now()
        where model_template_id=%(i)s and deleted_at is null
        """,
        {"i": model_template_id, "s": status, "b": by},
    )


# 可经 PUT /admin/model-templates/{id} 改写的列。is_default / status 不在内（各有专用端点）。
_UPDATABLE = ("display_name", "description", "main_model_asset_id", "sub_model_asset_id")


async def update_fields(model_template_id: str, fields: dict[str, Any], by: str) -> int:
    """PATCH 语义局部改写。SET 子句由 `_UPDATABLE` **字面量白名单**驱动，
    列名绝不来自请求体原文；值仍走 %(x)s 绑定（同 model_assets.update_fields 口径）。"""
    cols = [c for c in _UPDATABLE if c in fields]
    if not cols:
        return 0
    sets = ", ".join(f"{c}=%({c})s" for c in cols)
    return await exec1(
        f"""
        update sre_model_template set {sets}, last_updated_by=%(b)s, last_update_date=now()
        where model_template_id=%(i)s and deleted_at is null
        """,
        {**{c: fields[c] for c in cols}, "i": model_template_id, "b": by},
    )
