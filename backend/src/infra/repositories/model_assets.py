"""sre_model_asset 仓储（B7 模型资产；38.1 起授权迁模板维度，sre_model_access_grant 废弃不消费）。"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, q_all, q_one


async def list_all() -> list[dict[str, Any]]:
    """管理台清单（38.1：授权列已迁模板维度，不再算 grant_count）。"""
    return await q_all(
        "select * from sre_model_asset where deleted_at is null order by creation_date"
    )


async def get(model_asset_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_model_asset where model_asset_id=%(i)s and deleted_at is null", {"i": model_asset_id}
    )


async def get_by_model_id(model_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_model_asset where model_id=%(m)s and deleted_at is null", {"m": model_id}
    )


async def list_active() -> list[dict[str, Any]]:
    """全部 active 平台模型（38.1：资产级授权废弃，资产池对全员一致；授权在模板维度）。
    也是 model_gateway 平台默认回退的候选池——刻意不受任何授权约束。"""
    return await q_all(
        "select * from sre_model_asset where deleted_at is null and status='active' order by creation_date"
    )


async def create(
    display_name: str, protocol: str, model_id: str, base_url: str | None,
    secret_env_var: str | None, status: str, by: str,
    context_window_tokens: int = 128000,
) -> dict[str, Any]:
    # access_scope 列已废弃（38.1 授权迁模板维度）：insert 不再含该列，DEFAULT 'all' 兜底
    mid = str(uuid.uuid4())
    await exec1(
        """
        insert into sre_model_asset
          (model_asset_id, display_name, protocol, model_id, base_url, secret_env_var,
           context_window_tokens, status, registered_by, created_by, last_updated_by)
        values (%(i)s, %(d)s, %(p)s, %(m)s, %(u)s, %(e)s, %(cw)s, %(s)s, %(b)s, %(b)s, %(b)s)
        """,
        {"i": mid, "d": display_name, "p": protocol, "m": model_id, "u": base_url,
         "e": secret_env_var, "cw": context_window_tokens, "s": status, "b": by},
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


# 可经 PUT /model-assets/{id} 改写的列。model_id 不在内（实例 overlay.platform_model_id 的绑定键）；
# status 亦不在（专用 :status 端点）；access_scope 已废弃（38.1 授权迁模板维度）。
_UPDATABLE = ("display_name", "base_url", "secret_env_var", "context_window_tokens")


async def update_fields(model_asset_id: str, fields: dict[str, Any], by: str) -> int:
    """按提供的键局部改写连接配置（PATCH 语义：未出现的列原样不动）。

    SET 子句由 `_UPDATABLE` **字面量白名单**驱动，列名绝不来自请求体原文；值仍走 %(x)s 绑定。
    同 [[agent_teams.asset_in_use]] 的 f-string 拼列名口径。
    """
    cols = [c for c in _UPDATABLE if c in fields]
    if not cols:
        return 0
    sets = ", ".join(f"{c}=%({c})s" for c in cols)
    return await exec1(
        f"""
        update sre_model_asset set {sets}, last_updated_by=%(b)s, last_update_date=now()
        where model_asset_id=%(i)s and deleted_at is null
        """,
        {**{c: fields[c] for c in cols}, "i": model_asset_id, "b": by},
    )


# 38.1：资产级授权函数（set_access_scope / list_grants / replace_grants / has_grant /
# list_available_for_user）已整体移除——授权迁至模板维度，见 repositories/model_templates.py 同名函数。
