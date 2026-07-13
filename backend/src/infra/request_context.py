"""请求上下文（B9）：把登录用户的 IAM cookie 带给出站的 console 系调用。

IAM 开启时 deps.current_user 每请求写入；console_cookie() 优先取它——用户在请求内
触发的 omodel/apptree/registry/skillhub 调用用**用户自己的登录态**（umodel 校验的正是
这份 cookie，权限口径天然对齐），无请求上下文的后台路径（reconcile 循环、启动收敛）
回退 env 服务态 cookie。contextvars 随 create_task 快照继承——start_task 派生的任务
循环内 scope resolve 同样带发起者 cookie（per-user 化的第一步）。
"""
from __future__ import annotations

from contextvars import ContextVar

_user_cookie: ContextVar[str] = ContextVar("openops_user_cookie", default="")


def set_user_cookie(cookie: str) -> None:
    _user_cookie.set(cookie or "")


def user_cookie() -> str:
    return _user_cookie.get()
