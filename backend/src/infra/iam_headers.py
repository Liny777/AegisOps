"""内网 IAM 服务态鉴权头（出站 omodel / 平台 MCP 共用取数缝）。

`infra/j2c_utils.py` 是**仅内网存在**的文件（不进本仓库、不进 pyproject）：内含
`_get_iam_headers()`（Content-Type + Authorization=get_iam_token()）。内网把该文件
落进 src/infra/ 即自动启用；本仓库/CI 没有它 → 恒返回空 dict，一切行为零变化。

为什么需要服务态头（2026-08-12）：omodel/MCP 出站此前只有 Cookie 三档（请求上下文
→ 按 user_id 缓存 TTL 300s → env），告警 dispatcher 等**后台协程无请求上下文**，
夜间 cookie 过期 → omodel 401 → SCOPE_RESOLVE_FAILED 诊断起不来；IAM token 由
程序自取不依赖用户登录态，正治此症。Cookie 通路原样保留（用户态语义不变）。

形状对齐 host_ip.ec2_ip_headers()：取不到返回**空 dict**，绝不发空值头。
只挑 Authorization 键——j2c 的 Content-Type 丢弃（httpx 自管，避免污染 GET/握手）。

缓存（2026-08-15）：此处原假定 j2c_utils 内部自带 token 缓存，并留了「若实测每次出站
都打 IAM 就在此加 TTL」的口子。内网日志证伪了那个假定——每次出站都伴随一条
`InsecureRequestWarning ... iam.his-op-beta.huawei.com`，即 j2c 每次真打 IAM。它走
requests（同步），在 async 出站路径上**直接阻塞事件循环**。故按预留方案加进程内 TTL 缓存。
"""
from __future__ import annotations

import importlib
import logging
import os
import time

log = logging.getLogger("openops.iam_headers")

_warned_missing = False  # ModuleNotFoundError 只 debug 一次（本仓库/CI 常态）
_warned_failed = False   # 模块在但取 token 失败只 warning 一次（内网异常须可见，风暴期不刷屏）

_DEFAULT_TTL_S = 300.0   # 与 iam_client 的 OPENOPS_IAM_CACHE_TTL_S 默认值对齐
_cache: tuple[float, dict[str, str]] | None = None  # (过期时刻 monotonic, headers)


def _ttl_s() -> float:
    """`OPENOPS_IAM_TOKEN_TTL_S`；非法值回退默认。0 = 不缓存（每次现取，仅排查用）。"""
    raw = os.getenv("OPENOPS_IAM_TOKEN_TTL_S", "").strip()
    if not raw:
        return _DEFAULT_TTL_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_TTL_S


def _reset_cache() -> None:  # 测试隔离（同 scope_service._reset_cache）
    global _cache
    _cache = None


def iam_auth_headers() -> dict[str, str]:
    """出站 IAM Authorization 头片段；j2c_utils 缺失/取不到时返回空 dict。

    命中缓存直接返回**副本**——调用方（_client_kwargs）会把它 update 进自己的 headers 再改，
    返回内部 dict 等于把缓存交出去随便改。
    """
    global _warned_missing, _warned_failed, _cache
    now = time.monotonic()
    if _cache is not None and _cache[0] > now:
        return dict(_cache[1])
    try:
        j2c = importlib.import_module("infra.j2c_utils")
    except ModuleNotFoundError:
        if not _warned_missing:
            _warned_missing = True
            log.debug("[iam] infra.j2c_utils 不存在（内网独有文件）——出站不带 IAM 头")
        return {}
    try:
        auth = str((j2c._get_iam_headers() or {}).get("Authorization") or "").strip()
    except Exception:  # noqa: BLE001 —— 取 token 失败绝不放大为出站失败，降级回 Cookie 通路
        if not _warned_failed:
            _warned_failed = True
            log.warning("[iam] j2c_utils 取 IAM token 失败——出站暂不带 Authorization"
                        "（仅提示一次，恢复后自动带上）", exc_info=True)
        return {}
    if not auth:
        if not _warned_failed:
            _warned_failed = True
            log.warning("[iam] j2c_utils 返回空 Authorization——出站暂不带（仅提示一次）")
        return {}
    _warned_failed = False  # 恢复后允许下一次失败再提示
    headers = {"Authorization": auth}
    # 只缓存成功结果：失败缓存会把「IAM 短暂抖动」放大成整个 TTL 窗口的无鉴权出站，
    # 与本模块「取不到就降级回 Cookie 通路」的语义相冲。
    ttl = _ttl_s()
    _cache = (now + ttl, dict(headers)) if ttl > 0 else None
    return headers
