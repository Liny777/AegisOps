"""platform_runtime_config 仓储：沙箱容量配置 + 平台模型注册（config_domain 区分）。"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, jsonb, q_all, q_one

DOMAIN_SANDBOX = "sandbox"
DOMAIN_MODEL = "platform_model"  # 已废弃（B7 起模型迁 model_asset 表），保留常量供旧数据语义追溯


async def get_domain(domain: str) -> list[dict[str, Any]]:
    return await q_all(
        """
        select * from platform_runtime_config
        where config_domain=%(d)s and deleted_at is null order by config_key
        """,
        {"d": domain},
    )


async def upsert(domain: str, key: str, value: Any, *, value_type: str = "json",
                 description: str = "", reason: str = "", by: str = "system") -> None:
    row = await q_one(
        """
        select config_id from platform_runtime_config
        where config_domain=%(d)s and config_key=%(k)s and deleted_at is null
        """,
        {"d": domain, "k": key},
    )
    if row:
        await exec1(
            """
            update platform_runtime_config
            set config_value_json=%(v)s, updated_reason=%(r)s, last_updated_by=%(b)s, last_update_date=now()
            where config_id=%(id)s
            """,
            {"id": row["config_id"], "v": jsonb(value), "r": reason, "b": by},
        )
    else:
        await exec1(
            """
            insert into platform_runtime_config
              (config_id, config_domain, config_key, config_value_json, value_type, status,
               description, updated_reason, created_by, last_updated_by)
            values (%(id)s, %(d)s, %(k)s, %(v)s, %(t)s, 'active', %(desc)s, %(r)s, %(b)s, %(b)s)
            """,
            {"id": str(uuid.uuid4()), "d": domain, "k": key, "v": jsonb(value),
             "t": value_type, "desc": description, "r": reason, "b": by},
        )
