"""公司 IAM 双步鉴权（B9；老项目 openOps-Dev D4 `iam.py`/上游 openclaw-docker auth_deps 机制迁移）。

流程（照老口径，字段可配）：
  请求 Cookie 整串 → GET OPENOPS_IAM_ACCESS_TOKEN_URL（headers=Cookie + IAM-Client-Ip=XFF 首跳，
  判 JSON `code=="201"` 取 access_token/accessToken）→ GET OPENOPS_IAM_USERINFO_URL
  （`Authorization: <裸token>`，无 "Bearer " 前缀）→ `_dot_get` 点分路径取
  login_key（strip+lower，OPENOPS_IAM_LOGIN_KEY_FIELD 默认 `id`，支持嵌套如 `data.user.id`）
  / display_name（OPENOPS_IAM_DISPLAY_NAME_FIELD 默认 `name`，缺失回退 login_key）。

会话模型：无自签 session cookie——每请求都校验，进程内 TokenCache
（key=SHA-256(cookie)，TTL OPENOPS_IAM_CACHE_TTL_S 默认 300s，上限 1024 条）内免打 IAM。
失败语义：401（code≠201 / 未返回用户标识）/ 502（IAM 不可达或非 200）——白名单 403 在 deps 层裁决。
OPENOPS_IAM_ENABLED=false（默认）时本模块不被调用（deps 走 X-OpenOps-Mock-* 零回归）。
Cookie / token 绝不入日志与错误信息（SEC-001 同规矩）。
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from infra.external.mcp_registry_client import console_tls_verify, http_trust_env

log = logging.getLogger("openops.iam")

_CACHE: dict[str, tuple[float, dict[str, str]]] = {}  # sha256(cookie) → (expire_at, identity)
_CACHE_MAX = 1024


class IamError(Exception):
    """IAM 校验失败：status 401（鉴权拒）或 502（上游不可用）。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def enabled() -> bool:
    return os.getenv("OPENOPS_IAM_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def login_url(host: str = "") -> str | None:
    """401 引导跳转地址。支持 `{host}` 占位符——运行时替换为请求域名（老项目 D5.11 口径：
    测试/生产双域名共用一份配置，按用户进来的域名回跳）。"""
    url = os.getenv("OPENOPS_IAM_LOGIN_URL", "").strip()
    if not url:
        return None
    return url.replace("{host}", host) if host else url  # 无 host 上下文保持原样（老项目口径）


def browser_host(request) -> str:
    """浏览器要跳转的 URL（login/signout）的 {host} 替换源：**只信 X-Forwarded-Host**——
    网关直连后端时 Host 常被改写成 ip:port，用它替换会产出不可访问的登录地址；
    没有 XFH 就返回空 → URL 保留 {host} 占位符，由前端用 window.location.host 兜底替换
    （前端永远知道用户实际所在域名，两种网关行为下都正确）。"""
    xfh = request.headers.get("x-forwarded-host", "")
    return xfh.split(",")[0].strip() if xfh else ""


def _ttl() -> float:
    try:
        return float(os.getenv("OPENOPS_IAM_CACHE_TTL_S", "300"))
    except ValueError:
        return 300.0


def _dot_get(obj: Any, path: str) -> Any:
    """点分路径取嵌套字段（老项目 iam.py:_dot_get 口径）：`data.user.id` → obj["data"]["user"]["id"]。"""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def clear_cache(cookie_header: str | None = None) -> None:
    """登出/测试钩子：清指定 cookie 的缓存（None=全清）。"""
    if cookie_header is None:
        _CACHE.clear()
    else:
        _CACHE.pop(hashlib.sha256(cookie_header.encode()).hexdigest(), None)


def _valid_hostname(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    hostname = hostname.rstrip(".")
    if not hostname or len(hostname) > 253 or not hostname.isascii():
        return False
    label = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
    return all(label.fullmatch(part) for part in hostname.split("."))


def _fixed_https_target(env_name: str) -> str:
    """读取并验证会携带 Cookie/token 的 IAM 出站目标，配置漂移时运行时也 fail-closed。"""
    value = os.getenv(env_name, "").strip()
    try:
        parsed = urlsplit(value)
        _port = parsed.port
        valid = (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and _valid_hostname(parsed.hostname or "")
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and not any(ch in value for ch in "{}\\")
            and not any(ch.isspace() for ch in value)
        )
    except ValueError:
        valid = False
    if not valid:
        raise IamError(502, f"IAM 配置无效（{env_name} 必须是固定 HTTPS 地址）")
    return value


async def verify(cookie_header: str, client_ip: str | None) -> dict[str, str]:
    """双步校验；返回 {login_key, display_name}。TTL 内同 cookie 直接命中缓存不打 IAM。
    token/userinfo URL 必须是固定 HTTPS 地址；只有浏览器 login/signout 导航允许 `{host}`。"""
    # 先验配置再查缓存：配置漂移必须立即 fail-closed，不能被旧身份缓存暂时掩盖。
    token_url = _fixed_https_target("OPENOPS_IAM_ACCESS_TOKEN_URL")
    userinfo_url = _fixed_https_target("OPENOPS_IAM_USERINFO_URL")
    key = hashlib.sha256(cookie_header.encode()).hexdigest()
    hit = _CACHE.get(key)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]

    base_headers = {"Accept": "application/json", "Cookie": cookie_header}
    if client_ip:
        base_headers["IAM-Client-Ip"] = client_ip
    try:
        async with httpx.AsyncClient(timeout=10, verify=console_tls_verify(), trust_env=http_trust_env()) as cli:
            # 步 1：cookie 换 access_token（GET，无 body；code 必须是字符串 "201"）
            r1 = await cli.get(token_url, headers=base_headers)
            if r1.status_code != 200:
                raise IamError(502, f"IAM access_token 接口异常（HTTP {r1.status_code}）")
            d1 = r1.json()
            if str(d1.get("code")) != "201":
                raise IamError(401, "IAM 会话无效或已过期，请重新登录")
            token = d1.get("access_token") or d1.get("accessToken")
            if not token:
                raise IamError(502, "IAM 未返回 access_token")
            # 步 2：token 换 userinfo（Authorization 裸 token，无 Bearer 前缀——老项目实测口径）
            r2 = await cli.get(userinfo_url, headers={**base_headers, "Authorization": str(token)})
            if r2.status_code in (401, 403):
                raise IamError(401, "IAM 用户信息校验拒绝，请重新登录")
            if r2.status_code != 200:
                raise IamError(502, f"IAM userinfo 接口异常（HTTP {r2.status_code}）")
            userinfo = r2.json()
    except IamError:
        raise
    except Exception as e:  # noqa: BLE001 —— 网络/解析异常统一 502，不带敏感细节
        log.warning("[OpenOps][iam] IAM 请求失败：%s", type(e).__name__)
        raise IamError(502, "IAM 服务不可达") from None

    key_field = os.getenv("OPENOPS_IAM_LOGIN_KEY_FIELD", "id").strip() or "id"
    name_field = os.getenv("OPENOPS_IAM_DISPLAY_NAME_FIELD", "name").strip() or "name"
    raw_key = _dot_get(userinfo, key_field)
    if raw_key is None or not str(raw_key).strip():
        raise IamError(401, "IAM 未返回用户标识（检查 OPENOPS_IAM_LOGIN_KEY_FIELD）")
    login_key = str(raw_key).strip().lower()
    display_name = _dot_get(userinfo, name_field)
    ident = {"login_key": login_key,
             "display_name": str(display_name).strip() if display_name else login_key}

    if len(_CACHE) >= _CACHE_MAX:  # 简单逐出：清最早过期的一批
        for k in sorted(_CACHE, key=lambda x: _CACHE[x][0])[: _CACHE_MAX // 4]:
            _CACHE.pop(k, None)
    _CACHE[key] = (now + _ttl(), ident)
    return ident
