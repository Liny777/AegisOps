"""sre_user_secret / sre_user_llm_config 仓储。Secret 永不返回明文（SEC-001）。"""
from __future__ import annotations

import uuid
from typing import Any

from infra import crypto
from infra.db import exec1, jsonb, q_all, q_one


async def create_secret(
    user_id: str, secret_name: str, secret_type: str, provider: str, ciphertext: str, fp: str
) -> dict[str, Any]:
    sid = str(uuid.uuid4())
    await exec1(
        """
        insert into sre_user_secret
          (secret_ref_id, user_id, secret_name, secret_type, provider, ciphertext, nonce, key_version,
           fingerprint, status, created_by, last_updated_by)
        values (%(s)s, %(u)s, %(n)s, %(t)s, %(p)s, %(c)s, '', %(kv)s, %(f)s, 'active', %(u)s, %(u)s)
        """,
        {"s": sid, "u": user_id, "n": secret_name, "t": secret_type, "p": provider, "c": ciphertext,
         "f": fp, "kv": crypto.current_key_version()},
    )
    return {"secret_ref_id": sid, "fingerprint": fp}


async def list_secrets_masked(user_id: str) -> list[dict[str, Any]]:
    return await q_all(
        """
        select secret_ref_id, secret_name, secret_type, provider, fingerprint, status, creation_date
        from sre_user_secret where user_id=%(u)s and deleted_at is null order by creation_date
        """,
        {"u": user_id},
    )


async def get_secret(secret_ref_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_user_secret where secret_ref_id=%(s)s and deleted_at is null", {"s": secret_ref_id}
    )


async def create_llm_config(user_id: str, req: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
    lid = str(uuid.uuid4())
    await exec1(
        """
        insert into sre_user_llm_config
          (llm_config_id, user_id, display_name, provider, base_url, model_name, secret_ref_id,
           context_window_tokens, max_output_tokens, timeout_ms, max_retries,
           supports_tool_calling, supports_streaming, extra_params_json, status, created_by, last_updated_by)
        values (%(l)s, %(u)s, %(d)s, %(p)s, %(b)s, %(m)s, %(s)s,
                %(cw)s, %(mo)s, %(to)s, %(mr)s, %(tc)s, %(st)s, %(x)s, 'active', %(u)s, %(u)s)
        """,
        {"l": lid, "u": user_id, "d": req["display_name"], "p": req["provider"], "b": req["base_url"],
         "m": req["model_name"], "s": req.get("secret_ref_id"),
         "cw": req["context_window_tokens"], "mo": req["max_output_tokens"],
         "to": req["timeout_ms"], "mr": req["max_retries"],
         "tc": probe["supports_tool_calling"], "st": probe["supports_streaming"],
         # extra_headers 存 extra_params_json（Provider 差异化参数位）：schema 层已禁 Authorization 等
         # 保留头，密钥类凭据仍走 sre_user_secret 加密链，此处只放路由/租户类明文头
         "x": jsonb({"extra_headers": req.get("extra_headers") or {}} if req.get("extra_headers") else {})},
    )
    return {"llm_config_id": lid}


async def list_llm_configs(user_id: str) -> list[dict[str, Any]]:
    return await q_all(
        """
        select llm_config_id, display_name, provider, base_url, model_name, secret_ref_id,
               context_window_tokens, max_output_tokens, supports_tool_calling, status, creation_date,
               extra_params_json
        from sre_user_llm_config where user_id=%(u)s and deleted_at is null order by creation_date
        """,
        {"u": user_id},
    )


async def get_llm_config(llm_config_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_user_llm_config where llm_config_id=%(l)s and deleted_at is null", {"l": llm_config_id}
    )


# 可经 PATCH /llm-configs/{id} 改写的列。llm_config_id 不在内（实例 overlay.user_llm_config_id
# 的绑定键）；status 亦不在（本迭代无启停端点）；provider 固定 openai_compatible。
# extra_params_json 承载 extra_headers，值需过 jsonb() 包装——见 update_fields。
_UPDATABLE = ("display_name", "base_url", "model_name", "secret_ref_id",
              "context_window_tokens", "extra_params_json")
_JSONB_COLS = frozenset({"extra_params_json"})


async def update_fields(llm_config_id: str, fields: dict[str, Any], by: str) -> int:
    """按提供的键局部改写自带模型配置（PATCH 语义：未出现的列原样不动）。

    SET 子句由 `_UPDATABLE` **字面量白名单**驱动，列名绝不来自请求体原文；值仍走 %(x)s 绑定
    （同 [[model_assets.update_fields]] 口径）。jsonb 列的 dict 值必须经 jsonb() 适配，
    直接绑 dict 会被 psycopg 拒。
    """
    cols = [c for c in _UPDATABLE if c in fields]
    if not cols:
        return 0
    sets = ", ".join(f"{c}=%({c})s" for c in cols)
    vals = {c: (jsonb(fields[c]) if c in _JSONB_COLS else fields[c]) for c in cols}
    return await exec1(
        f"""
        update sre_user_llm_config set {sets}, last_updated_by=%(b)s, last_update_date=now()
        where llm_config_id=%(l)s and deleted_at is null
        """,
        {**vals, "l": llm_config_id, "b": by},
    )


async def soft_delete_llm_config(llm_config_id: str, by: str) -> int:
    """软删自带模型配置。软删而非硬删：历史 config_version.overlay_json 里的 UUID 仍指得回一行，
    审计回放能显示「当时用的是哪个模型」；且 secret_ref_id NOT NULL 的关联不被打断。
    服务层先做 active 实例引用检查（被绑定则拒删——运行时对失效自带 LLM 是硬失败不是降级）。"""
    return await exec1(
        """
        update sre_user_llm_config set deleted_at=now(), last_updated_by=%(b)s, last_update_date=now()
        where llm_config_id=%(l)s and deleted_at is null
        """,
        {"l": llm_config_id, "b": by},
    )


async def soft_delete_secret(secret_ref_id: str, by: str) -> int:
    """连带软删该配置专属的 Secret（submitCustomLlm 每建一个配置就建一条同名 Secret，
    生命周期绑定；不连带删会在密钥清单里留下永远无人认领的孤儿）。"""
    return await exec1(
        """
        update sre_user_secret set deleted_at=now(), status='revoked',
               last_updated_by=%(b)s, last_update_date=now()
        where secret_ref_id=%(s)s and deleted_at is null
        """,
        {"s": secret_ref_id, "b": by},
    )


async def list_instances_using_llm_config(llm_config_id: str) -> list[dict[str, Any]]:
    """仍以该自带模型为**当前生效配置**的实例（删除前的引用检查）。

    只看 active_config_version（历史版本的 overlay 引用不该拦删除，同
    [[agent_teams.asset_in_use]] 的 cv.status='active' 判据）。命中即拒删：
    真删了会让这些实例每次 start_task 都 403（model_gateway 对失效自带 LLM 抛
    MODEL_NOT_AUTHORIZED，run_state_service 未接住），Agent 直接不可用。
    """
    return await q_all(
        """
        select i.agent_team_instance_id, i.instance_name
        from sre_agent_team_instance i
        join sre_agent_team_config_version cv on cv.config_version_id = i.active_config_version_id
        where cv.overlay_json->>'user_llm_config_id' = %(l)s
          and i.deleted_at is null and cv.deleted_at is null
        order by i.creation_date
        """,
        {"l": llm_config_id},
    )
