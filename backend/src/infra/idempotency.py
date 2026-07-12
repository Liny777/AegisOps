"""幂等：client_request_id 去重（P 块：内存 L1 + PG 跨进程；INIT-006）。

同 (user_id, op, client_request_id) 重放返回首个结果。PG 故障时降级为进程内幂等
（get 当 miss、put 仅内存），不阻断创建路径；并发撞唯一索引=首个赢（吞冲突）。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from infra.db import exec1, jsonb, q_one

log = logging.getLogger("openops.idempotency")

_seen: dict[tuple[str, str, str], Any] = {}  # L1：热路径 + PG 故障兜底


async def get(user_id: str, op: str, crid: str) -> Any | None:
    hit = _seen.get((user_id, op, crid))
    if hit is not None:
        return hit
    try:
        row = await q_one(
            "select result_json from sre_idempotency_key "
            "where user_id=%(u)s and op=%(o)s and client_request_id=%(c)s "
            "and deleted_at is null and expire_at > now()",
            {"u": user_id, "o": op, "c": crid},
        )
    except Exception:  # noqa: BLE001 —— PG 抖动降级进程内幂等，不阻断创建
        log.warning("[OpenOps][idem] PG 读失败，降级进程内幂等（op=%s）", op)
        return None
    if row is None:
        return None
    _seen[(user_id, op, crid)] = row["result_json"]
    return row["result_json"]


async def put(user_id: str, op: str, crid: str, result: Any) -> Any:
    _seen[(user_id, op, crid)] = result
    try:
        await exec1(
            """
            insert into sre_idempotency_key
              (idempotency_id, user_id, op, client_request_id, result_json, created_by, last_updated_by)
            values (%(i)s, %(u)s, %(o)s, %(c)s, %(r)s, %(u)s, %(u)s)
            """,
            {"i": str(uuid.uuid4()), "u": user_id, "o": op, "c": crid, "r": jsonb(result)},
        )
    except Exception as e:  # noqa: BLE001 —— 唯一冲突（并发首个赢）/PG 抖动都不阻断
        if "unique" not in str(e).lower() and "duplicate" not in str(e).lower():
            log.warning("[OpenOps][idem] PG 写失败，本进程幂等仍生效（op=%s）：%s", op, type(e).__name__)
    return result


async def purge_expired() -> int:
    """启动清理：物理删过期/软删行（幂等键非业务数据，无审计保留要求）。"""
    try:
        n = await exec1("delete from sre_idempotency_key where expire_at <= now() or deleted_at is not null")
        if n:
            log.info("[OpenOps][idem] 清理过期幂等键 %d 行", n)
        return n
    except Exception:  # noqa: BLE001
        return 0


def clear() -> None:  # 测试用（内存面；PG 面由 conftest reset_database TRUNCATE）
    _seen.clear()
