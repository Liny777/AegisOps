"""幂等：client_request_id 去重（进程内，V1 骨架；INIT-006）。

同 (user_id, op, client_request_id) 重放返回首个结果。
TODO(后续块)：落 PG 幂等表跨进程。
"""
from __future__ import annotations

from typing import Any

_seen: dict[tuple[str, str, str], Any] = {}


def get(user_id: str, op: str, crid: str) -> Any | None:
    return _seen.get((user_id, op, crid))


def put(user_id: str, op: str, crid: str, result: Any) -> Any:
    _seen[(user_id, op, crid)] = result
    return result


def clear() -> None:  # 测试用
    _seen.clear()
