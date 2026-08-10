"""告警平台 real 实现：服务态 Bearer token 出站（契约 ALERT-PLATFORM-CONTRACT.md）。

与 omodel_real 的关键差异：本客户端在**后台循环**里运行，无请求上下文——不透传用户
cookie、不带 IAM-Client-Ip，鉴权只认 `OPENOPS_ALERT_TOKEN`。BASE_URL 必须是固定绝对
地址（禁 `{host}` 占位/凭据/query，fail-closed），TLS 与代理沿用平台统一三档。
失败不静默：每次调用一行日志，非 2xx 带响应体前 300 字。
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from infra.external.alert_platform_client import AlertPlatformError
from infra.external.mcp_registry_client import console_tls_verify, http_trust_env

log = logging.getLogger("openops.alerts.platform")


def _timeout() -> float:
    try:
        return float(os.environ.get("OPENOPS_ALERT_TIMEOUT_S", "15"))
    except ValueError:
        return 15.0


def _prefix() -> str:
    """API 文根：`实际 URL = BASE_URL + 本文根 + 操作尾段`（env 管挂在哪，代码管契约是什么）。"""
    p = os.environ.get("OPENOPS_ALERT_API_PREFIX", "/openapi/alerts/v1").strip()
    return "/" + p.strip("/")


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


def _base() -> str:
    """固定绝对地址，fail-closed（携带服务凭证出站，目标域绝不能由配置模板/请求头决定）。"""
    raw = os.environ.get("OPENOPS_ALERT_BASE_URL", "").strip().rstrip("/")
    if not raw or any(ch in raw for ch in "{}\\") or any(ch.isspace() for ch in raw):
        raise AlertPlatformError(
            "config", "OPENOPS_ALERT_BASE_URL 未配置或含模板占位——real 档必须写固定绝对地址")
    try:
        target = urlparse(raw)
        _ = target.port
    except ValueError as e:
        raise AlertPlatformError("config", "OPENOPS_ALERT_BASE_URL 端口无效") from e
    if (target.scheme not in ("http", "https") or not target.hostname
            or not _valid_hostname(target.hostname)
            or target.username is not None or target.password is not None
            or target.query or target.fragment or target.params):
        raise AlertPlatformError(
            "config", "OPENOPS_ALERT_BASE_URL 必须是无凭据、查询或片段的固定绝对地址")
    return raw


def _token() -> str:
    tok = os.environ.get("OPENOPS_ALERT_TOKEN", "").strip()
    if not tok:
        raise AlertPlatformError(
            "config", "OPENOPS_ALERT=real 但 OPENOPS_ALERT_TOKEN 未配置——绝不静默降级")
    return tok


def _client_kwargs() -> dict[str, Any]:
    return {
        "base_url": _base(),
        "timeout": _timeout(),
        "verify": console_tls_verify(),
        "trust_env": http_trust_env(),
        "headers": {"Authorization": f"Bearer {_token()}"},
    }


def _safe_json(r: Any) -> dict[str, Any]:
    try:
        body = r.json()
        return body if isinstance(body, dict) else {}
    except Exception:  # noqa: BLE001 —— 非 JSON 响应体走 text 摘录路径
        return {}


def _check(r: Any, what: str) -> None:
    rid = str((r.headers or {}).get("x-request-id", ""))
    code = r.status_code
    if code in (401, 403):
        raise AlertPlatformError("auth", f"{what}: 告警平台鉴权失败 HTTP {code}（检查 OPENOPS_ALERT_TOKEN）",
                                 status_code=code, request_id=rid)
    if code == 410:
        body = _safe_json(r)
        raise AlertPlatformError("cursor_expired", f"{what}: 游标超出保留窗（HTTP 410）",
                                 status_code=code, request_id=rid,
                                 detail={"earliest_cursor": str(body.get("earliest_cursor", ""))})
    if code == 429:
        retry_after = str((r.headers or {}).get("Retry-After", ""))
        raise AlertPlatformError("rate_limited", f"{what}: 对端限流 HTTP 429 Retry-After={retry_after or '?'}",
                                 status_code=code, request_id=rid,
                                 detail={"retry_after_s": retry_after})
    if code >= 400:
        excerpt = (getattr(r, "text", "") or "")[:300]
        raise AlertPlatformError("http", f"{what}: HTTP {code} {excerpt}", status_code=code, request_id=rid)


async def list_changes(cursor: str = "", limit: int = 200) -> dict[str, Any]:
    params: dict[str, Any] = {"cursor": cursor or "", "limit": int(limit)}
    try:
        async with httpx.AsyncClient(**_client_kwargs()) as client:
            r = await client.get(f"{_prefix()}/changes", params=params)
    except AlertPlatformError:
        raise
    except Exception as e:  # noqa: BLE001 —— 传输层失败统一收口为 network，否则上层会漏成 500
        raise AlertPlatformError("network", f"list_changes: 告警平台不可达 {type(e).__name__}: {e}") from e
    _check(r, "list_changes")
    body = _safe_json(r)
    alerts = body.get("alerts")
    if not isinstance(alerts, list):
        raise AlertPlatformError("http", "list_changes: 响应缺少 alerts 数组（契约漂移？）",
                                 status_code=r.status_code)
    out = {"alerts": alerts,
           "next_cursor": str(body.get("next_cursor", cursor or "")),
           "has_more": bool(body.get("has_more", False))}
    log.info("[alerts][pull] cursor=%s got=%d next=%s has_more=%s",
             (cursor or "(empty)")[:24], len(alerts), out["next_cursor"][:24], out["has_more"])
    return out


async def get_alert(alert_id: str) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(**_client_kwargs()) as client:
            r = await client.get(f"{_prefix()}/alerts/{alert_id}")
    except AlertPlatformError:
        raise
    except Exception as e:  # noqa: BLE001
        raise AlertPlatformError("network", f"get_alert: 告警平台不可达 {type(e).__name__}: {e}") from e
    if r.status_code == 404:
        return None
    _check(r, "get_alert")
    body = _safe_json(r)
    return body or None


def _query_url() -> str:
    """历史查询整条 URL（sit/beta/pro 路径差异大，不做拼装）。与 _base 同级 fail-closed：
    禁模板占位/空白/凭据/query/fragment；允许带 path（29.10 的路径含 enterpriseId 段）。"""
    raw = os.environ.get("OPENOPS_ALERT_QUERY_URL", "").strip().rstrip("/")
    if not raw or any(ch in raw for ch in "{}\\") or any(ch.isspace() for ch in raw):
        raise AlertPlatformError(
            "config", "OPENOPS_ALERT_QUERY_URL 未配置或含模板占位——历史查询必须写固定完整地址")
    try:
        target = urlparse(raw)
        _ = target.port
    except ValueError as e:
        raise AlertPlatformError("config", "OPENOPS_ALERT_QUERY_URL 端口无效") from e
    if (target.scheme not in ("http", "https") or not target.hostname
            or not _valid_hostname(target.hostname)
            or target.username is not None or target.password is not None
            or target.query or target.fragment or target.params):
        raise AlertPlatformError(
            "config", "OPENOPS_ALERT_QUERY_URL 必须是无凭据、查询或片段的固定完整地址")
    return raw


async def list_history(*, start: Any, end: Any, categories: list[str],
                       severities: list[str], project_ids: list[str] | None,
                       page_no: int = 1, page_size: int = 20) -> dict[str, Any]:
    """内网 29.10 alarm_list 查询：内部词表 → wire 词表翻译收敛在此。

    时间按北京时间发（R3：服务端时区解释联调核对，窗口偏 8h 即改 astimezone 一处）；
    enterpriseId 文档已改非必填——env 配了才带。
    """
    from infra.external.alert_inet_contract import CST, SEVERITY_TO_LEVEL, map_history_row

    body: dict[str, Any] = {
        "startTime": start.astimezone(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "endTime": end.astimezone(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "pageNo": int(page_no),
        "pageSize": int(page_size),
    }
    if categories:
        body["moTypeList"] = list(categories)
    if severities:
        body["alarmLevels"] = sorted({SEVERITY_TO_LEVEL[s] for s in severities
                                      if s in SEVERITY_TO_LEVEL})
    if project_ids:
        body["projectIds"] = list(project_ids)
    ent = os.environ.get("OPENOPS_ALERT_ENTERPRISE_ID", "").strip()
    if ent:
        body["enterpriseId"] = ent
    url = _query_url()
    try:
        async with httpx.AsyncClient(timeout=_timeout(), verify=console_tls_verify(),
                                     trust_env=http_trust_env(),
                                     headers={"Authorization": f"Bearer {_token()}"}) as client:
            r = await client.post(url, json=body)  # 整条 URL 直发，不走 base_url 拼装
    except AlertPlatformError:
        raise
    except Exception as e:  # noqa: BLE001
        raise AlertPlatformError("network", f"list_history: 告警平台不可达 {type(e).__name__}: {e}") from e
    _check(r, "list_history")
    env = _safe_json(r)
    if str(env.get("status") or "") != "OK":
        raise AlertPlatformError(
            "http", f"list_history: status={env.get('status')} {str(env.get('message'))[:200]}",
            status_code=r.status_code)
    datas = (env.get("data") or {}).get("datas") or []
    if not isinstance(datas, list):
        raise AlertPlatformError("http", "list_history: 响应缺 data.datas 数组（契约漂移？）",
                                 status_code=r.status_code)
    rows = [map_history_row(d) for d in datas if isinstance(d, dict)]
    total = int((env.get("data") or {}).get("total") or 0) or len(rows)  # total 有无联调确认（R6）
    log.info("[alerts][history] window=%s~%s got=%d", body["startTime"], body["endTime"], len(rows))
    return {"rows": rows, "total": total}
