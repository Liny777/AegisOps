"""幂等：client_request_id 去重（P 块：内存 L1 + PG 跨进程；INIT-006）。

DEF-3 重构为「先占位抢锁、成功后回填结果」：
- begin()：L1/PG 双层判定——重放命中返回缓存结果；同 key **不同请求体** → 409
  IDEMPOTENCY_KEY_CONFLICT（request_hash 比对，INIT-006 补实现）；同 key 进行中 →
  409（retryable，防并发窗口重复建资源——旧「先业务后 INSERT」两并发各建一个 run）。
- commit()：业务成功后 UPDATE 回填 result_json。
- rollback()：业务失败删占位行，放行重试。
占位 INSERT 用 insert…select where not exists（唯一索引是偏索引，避 ON CONFLICT
target 的 GaussDB 兼容坑）。PG 故障仍降级进程内幂等，不阻断创建路径。
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any

from domain.errors import ApiError, Err
from infra.db import exec1, jsonb, q_one

log = logging.getLogger("openops.idempotency")

# L1：key → (request_hash, result|None)。result None=本进程占位进行中（PG 故障兜底也走它）
_seen: dict[tuple[str, str, str], tuple[str, Any]] = {}


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


async def begin(user_id: str, op: str, crid: str, payload: Any) -> Any | None:
    """占位/判重。返回缓存结果（重放命中）或 None（本请求成为赢家，业务照跑）。"""
    h = _hash(payload)
    k = (user_id, op, crid)
    l1 = _seen.get(k)
    if l1 is not None:
        if l1[0] != h:
            raise ApiError(Err.IDEMPOTENCY_KEY_CONFLICT, "同 client_request_id 的请求体不一致")
        if l1[1] is not None:
            return l1[1]
        raise ApiError(Err.IDEMPOTENCY_KEY_CONFLICT, "相同请求正在处理中，请稍后重试", retryable=True)
    try:
        won = await exec1(
            """
            insert into sre_idempotency_key
              (idempotency_id, user_id, op, client_request_id, request_hash, created_by, last_updated_by)
            select %(i)s, %(u)s, %(o)s, %(c)s, %(h)s, %(u)s, %(u)s
            where not exists (
                select 1 from sre_idempotency_key
                where user_id=%(u)s and op=%(o)s and client_request_id=%(c)s and deleted_at is null
            )
            """,
            {"i": str(uuid.uuid4()), "u": user_id, "o": op, "c": crid, "h": h},
        )
        row = await q_one(
            "select request_hash, result_json from sre_idempotency_key "
            "where user_id=%(u)s and op=%(o)s and client_request_id=%(c)s "
            "and deleted_at is null and expire_at > now()",
            {"u": user_id, "o": op, "c": crid},
        )
    except Exception:  # noqa: BLE001 —— PG 抖动降级进程内幂等，不阻断创建
        log.warning("[OpenOps][idem] PG 占位失败，降级进程内幂等（op=%s）", op)
        _seen[k] = (h, None)
        return None
    if row is None:  # 刚插的行被并发清理/过期视作全新请求
        _seen[k] = (h, None)
        return None
    if row["request_hash"] not in (None, h):  # None=升级前旧行（无 hash），按同体放行
        raise ApiError(Err.IDEMPOTENCY_KEY_CONFLICT, "同 client_request_id 的请求体不一致")
    if won:
        _seen[k] = (h, None)
        return None  # 我是赢家
    if row["result_json"] is not None:
        _seen[k] = (h, row["result_json"])
        return row["result_json"]  # 重放命中首个结果
    raise ApiError(Err.IDEMPOTENCY_KEY_CONFLICT, "相同请求正在处理中，请稍后重试", retryable=True)


async def commit(user_id: str, op: str, crid: str, result: Any) -> Any:
    """业务成功后回填结果（此后同 key 重放命中它）。"""
    k = (user_id, op, crid)
    h = _seen.get(k, ("", None))[0]
    _seen[k] = (h, result)
    try:
        await exec1(
            "update sre_idempotency_key set result_json=%(r)s, last_update_date=now() "
            "where user_id=%(u)s and op=%(o)s and client_request_id=%(c)s and deleted_at is null",
            {"u": user_id, "o": op, "c": crid, "r": jsonb(result)},
        )
    except Exception as e:  # noqa: BLE001 —— PG 抖动不阻断（本进程幂等仍生效）
        log.warning("[OpenOps][idem] PG 回填失败，本进程幂等仍生效（op=%s）：%s", op, type(e).__name__)
    return result


async def rollback(user_id: str, op: str, crid: str) -> None:
    """业务失败释放占位：删无结果行，放行用户重试。"""
    _seen.pop((user_id, op, crid), None)
    try:
        await exec1(
            "delete from sre_idempotency_key "
            "where user_id=%(u)s and op=%(o)s and client_request_id=%(c)s and result_json is null",
            {"u": user_id, "o": op, "c": crid},
        )
    except Exception:  # noqa: BLE001
        pass


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
