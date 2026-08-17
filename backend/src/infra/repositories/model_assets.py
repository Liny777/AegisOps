"""sre_model_asset 仓储（B7 模型资产；38.1 起授权迁模板维度，sre_model_access_grant 废弃不消费）。

API Key 密文（`secret_ciphertext` / `secret_key_version`）**只经 [[get_secret_material]] 一处 select**
（SEC-001，2026-08-17 Key 入库）：本模块其余查询一律走 `_PUBLIC_COLS` 显式列清单，绝不 `select *`——
资产行会经 `row_json` 直接流进管理台响应，`select *` 等于把密文推到前端。
"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, jsonb, q_all, q_one

# 可外泄列（管理台/网关都读这一份）：密文两列刻意不在内；`secret_fingerprint` 是不可逆指纹，
# 是唯一允许回显的密钥信息。`has_secret` 让调用方无需触碰密文即可判断「这模型能不能跑」
# （model_gateway._default_spec / model_asset_service._default_model_id 的可用性判据）。
_PUBLIC_COLS = """
  model_asset_id, display_name, protocol, model_id, base_url, secret_env_var,
  secret_fingerprint, (secret_ciphertext is not null) as has_secret,
  context_window_tokens, extra_params_json, access_scope, status, registered_by,
  creation_date, last_update_date, created_by, last_updated_by
"""


async def list_all() -> list[dict[str, Any]]:
    """管理台清单（38.1：授权列已迁模板维度，不再算 grant_count）。"""
    return await q_all(
        f"select {_PUBLIC_COLS} from sre_model_asset where deleted_at is null order by creation_date"
    )


async def get(model_asset_id: str) -> dict[str, Any] | None:
    return await q_one(
        f"select {_PUBLIC_COLS} from sre_model_asset where model_asset_id=%(i)s and deleted_at is null",
        {"i": model_asset_id},
    )


async def get_by_model_id(model_id: str) -> dict[str, Any] | None:
    return await q_one(
        f"select {_PUBLIC_COLS} from sre_model_asset where model_id=%(m)s and deleted_at is null",
        {"m": model_id},
    )


async def list_active() -> list[dict[str, Any]]:
    """全部 active 平台模型（38.1：资产级授权废弃，资产池对全员一致；授权在模板维度）。
    也是 model_gateway 平台默认回退的候选池——刻意不受任何授权约束。"""
    return await q_all(
        f"select {_PUBLIC_COLS} from sre_model_asset "
        "where deleted_at is null and status='active' order by creation_date"
    )


async def get_secret_material(model_asset_id: str) -> dict[str, Any] | None:
    """取该资产的 API Key 密文（SEC-001：本模块唯一 select 出密文的函数）。

    **只允许在调用边界瞬时解密时使用**——runtime 构建模型（agentscope_runtime._decrypt_asset_secret）
    与管理台「测试连接」探测。任何要把资产行返回给客户端的路径都必须走 `_PUBLIC_COLS`。
    与用户侧 [[secrets.get_secret]] 同定位。
    """
    return await q_one(
        "select model_asset_id, secret_ciphertext, secret_key_version, secret_fingerprint, status "
        "from sre_model_asset where model_asset_id=%(i)s and deleted_at is null",
        {"i": model_asset_id},
    )


async def create(
    display_name: str, protocol: str, model_id: str, base_url: str | None,
    secret_env_var: str | None, status: str, by: str,
    context_window_tokens: int = 128000,
    extra_headers: dict[str, str] | None = None,
    secret: dict[str, str] | None = None,
) -> dict[str, Any]:
    """注册资产。`secret` 为服务层加密好的 {ciphertext,key_version,fingerprint} 三元组
    （明文 Key 不进本层）；None 表示该模型无 Key（走平台网关或尚未配置）。"""
    # access_scope 列已废弃（38.1 授权迁模板维度）：insert 不再含该列，DEFAULT 'all' 兜底
    mid = str(uuid.uuid4())
    sec = secret or {}
    await exec1(
        """
        insert into sre_model_asset
          (model_asset_id, display_name, protocol, model_id, base_url, secret_env_var,
           secret_ciphertext, secret_key_version, secret_fingerprint,
           context_window_tokens, extra_params_json, status, registered_by, created_by, last_updated_by)
        values (%(i)s, %(d)s, %(p)s, %(m)s, %(u)s, %(e)s, %(sc)s, %(sk)s, %(sf)s,
                %(cw)s, %(x)s, %(s)s, %(b)s, %(b)s, %(b)s)
        """,
        {"i": mid, "d": display_name, "p": protocol, "m": model_id, "u": base_url,
         "e": secret_env_var, "cw": context_window_tokens, "s": status, "b": by,
         "sc": sec.get("secret_ciphertext"), "sk": sec.get("secret_key_version"),
         "sf": sec.get("secret_fingerprint"),
         "x": jsonb({"extra_headers": extra_headers} if extra_headers else {})},
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


async def soft_delete(model_asset_id: str, by: str) -> int:
    """软删（38.2）：model_id 唯一索引带 WHERE deleted_at IS NULL，删后同 model_id 可重注册。
    服务层先做模板引用检查（被槽位引用则拒删）；legacy overlay 引用不拦（运行时 fail-safe 回默认）。"""
    return await exec1(
        """
        update sre_model_asset set deleted_at=now(), last_updated_by=%(b)s, last_update_date=now()
        where model_asset_id=%(i)s and deleted_at is null
        """,
        {"i": model_asset_id, "b": by},
    )


# 可经 PUT /model-assets/{id} 改写的列。model_id 不在内（实例 overlay.platform_model_id 的绑定键）；
# status 亦不在（专用 :status 端点）；access_scope 已废弃（38.1 授权迁模板维度）。
# 密钥三列同进同出（服务层要么三个都给、要么三个都给 None 表示清除），不可能只改其中一列。
_UPDATABLE = ("display_name", "base_url", "secret_env_var", "context_window_tokens",
              "extra_params_json", "secret_ciphertext", "secret_key_version", "secret_fingerprint")
_JSONB_COLS = frozenset({"extra_params_json"})


async def update_fields(model_asset_id: str, fields: dict[str, Any], by: str) -> int:
    """按提供的键局部改写连接配置（PATCH 语义：未出现的列原样不动）。

    SET 子句由 `_UPDATABLE` **字面量白名单**驱动，列名绝不来自请求体原文；值仍走 %(x)s 绑定。
    同 [[agent_teams.asset_in_use]] 的 f-string 拼列名口径。
    jsonb 列的 dict 值必须经 jsonb() 适配，直接绑 dict 会被 psycopg 拒。
    """
    cols = [c for c in _UPDATABLE if c in fields]
    if not cols:
        return 0
    sets = ", ".join(f"{c}=%({c})s" for c in cols)
    vals = {c: (jsonb(fields[c]) if c in _JSONB_COLS else fields[c]) for c in cols}
    return await exec1(
        f"""
        update sre_model_asset set {sets}, last_updated_by=%(b)s, last_update_date=now()
        where model_asset_id=%(i)s and deleted_at is null
        """,
        {**vals, "i": model_asset_id, "b": by},
    )


# 38.1：资产级授权函数（set_access_scope / list_grants / replace_grants / has_grant /
# list_available_for_user）已整体移除——授权迁至模板维度，见 repositories/model_templates.py 同名函数。
