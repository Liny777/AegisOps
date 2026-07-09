"""user_secret / user_llm_config 仓储。Secret 永不返回明文（SEC-001）。"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, jsonb, q_all, q_one


async def create_secret(
    user_id: str, secret_name: str, secret_type: str, provider: str, ciphertext: str, fp: str
) -> dict[str, Any]:
    sid = str(uuid.uuid4())
    await exec1(
        """
        insert into user_secret
          (secret_ref_id, user_id, secret_name, secret_type, provider, ciphertext, nonce, key_version,
           fingerprint, status, created_by, last_updated_by)
        values (%(s)s, %(u)s, %(n)s, %(t)s, %(p)s, %(c)s, '', 'v1', %(f)s, 'active', %(u)s, %(u)s)
        """,
        {"s": sid, "u": user_id, "n": secret_name, "t": secret_type, "p": provider, "c": ciphertext, "f": fp},
    )
    return {"secret_ref_id": sid, "fingerprint": fp}


async def list_secrets_masked(user_id: str) -> list[dict[str, Any]]:
    return await q_all(
        """
        select secret_ref_id, secret_name, secret_type, provider, fingerprint, status, creation_date
        from user_secret where user_id=%(u)s and deleted_at is null order by creation_date
        """,
        {"u": user_id},
    )


async def get_secret(secret_ref_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from user_secret where secret_ref_id=%(s)s and deleted_at is null", {"s": secret_ref_id}
    )


async def create_llm_config(user_id: str, req: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
    lid = str(uuid.uuid4())
    await exec1(
        """
        insert into user_llm_config
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
         "tc": probe["supports_tool_calling"], "st": probe["supports_streaming"], "x": jsonb({})},
    )
    return {"llm_config_id": lid}


async def list_llm_configs(user_id: str) -> list[dict[str, Any]]:
    return await q_all(
        """
        select llm_config_id, display_name, provider, base_url, model_name, secret_ref_id,
               context_window_tokens, max_output_tokens, supports_tool_calling, status, creation_date
        from user_llm_config where user_id=%(u)s and deleted_at is null order by creation_date
        """,
        {"u": user_id},
    )


async def get_llm_config(llm_config_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from user_llm_config where llm_config_id=%(l)s and deleted_at is null", {"l": llm_config_id}
    )
