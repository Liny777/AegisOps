"""请求上下文（B9）：把登录用户的 IAM cookie 带给出站的 console 系调用。

IAM 开启时 deps.current_user 每请求写入用户 cookie + user_id（contextvar），并按 user_id
缓存原始 cookie。console_cookie() 的取值序里「用户透传」优先于共享 env（同域部署 umodel/
console 校验的正是用户这份登录态，权限口径天然对齐，免维护服务态 cookie）；专属 env 仍最高
（跨域部署显式覆盖），共享 env 垫底（后台无用户路径兜底：reconcile 循环、启动收敛）。

按用户缓存（cached_user_cookie）供**无请求上下文但已知 user_id** 的路径复用登录态——当前
所有 omodel 调用都在请求内（contextvar 即可），缓存为 per-user 自主 Agent 的任务循环内
scope resolve 预留；contextvars 也随 create_task 快照继承。

⚠安全：原始 cookie 按 user 短暂驻留内存（TTL 内）——绝不入日志/错误信息（SEC-001 同规矩）。
这与 iam_client TokenCache「只存 SHA256(cookie)」是相反取舍，代价换「无上下文路径可复用」。
"""
from __future__ import annotations

import os
import time
from contextvars import ContextVar

_user_cookie: ContextVar[str] = ContextVar("openops_user_cookie", default="")
_user_id: ContextVar[str] = ContextVar("openops_user_id", default="")

# user_id → (expire_at_monotonic, raw_cookie)
_cookie_cache: dict[str, tuple[float, str]] = {}
_CACHE_MAX = 1024


def _ttl() -> float:
    try:
        return float(os.getenv("OPENOPS_IAM_CACHE_TTL_S", "300"))
    except ValueError:
        return 300.0


def set_request_user(user_id: str, cookie: str) -> None:
    """每请求写入（deps.current_user）：contextvar（本请求透传）+ 按用户缓存原始 cookie。"""
    _user_cookie.set(cookie or "")
    _user_id.set(user_id or "")
    if user_id and cookie:
        if len(_cookie_cache) >= _CACHE_MAX:  # 简单逐出：清最早过期的一批
            for k in sorted(_cookie_cache, key=lambda x: _cookie_cache[x][0])[: _CACHE_MAX // 4]:
                _cookie_cache.pop(k, None)
        _cookie_cache[user_id] = (time.monotonic() + _ttl(), cookie)


def user_cookie() -> str:
    """当前请求的用户 cookie（contextvar）。"""
    return _user_cookie.get()


def current_user_id() -> str:
    return _user_id.get()


def cached_user_cookie(user_id: str) -> str:
    """按 user_id 反查缓存的原始 cookie（无上下文路径用）；过期/未命中返回 ""。"""
    if not user_id:
        return ""
    hit = _cookie_cache.get(user_id)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    return ""


def clear() -> None:
    """测试钩子：清缓存（contextvar 由 pytest 各用例自然隔离）。"""
    _cookie_cache.clear()
