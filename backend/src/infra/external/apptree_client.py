"""应用目录客户端（初始化「从应用创建系统范围」的选源）：按 `OPENOPS_APPTREE=mock`（默认）|`real` 切换。

real 走内网 verification 服务 `userid_search_appid`（按 W3 账号搜该用户可见的 APPID），返回**平铺**应用列表
（`app_id=dimension_code` / `name=current_name_zh` / `type=dimension_type`），按 app_id 去重（一人多角色→同
appid 多行）。出站硬化与 console/omodel 同口径（TLS 三档 + trust_env 默认 off + 可选 IAM cookie）。

⚠对端拼写照抄勿改：URL 段 `unifieduery`、请求体键 `uesrId` 都是上游真实拼写（curl 实证），非笔误请勿"修正"。
"""
from __future__ import annotations

import os
import re
from typing import Any

# {enterprise}/{project} 由 env 配置（联调环境用默认值）；path/键的拼写是对端真实契约（见模块 docstring）
_PATH_TMPL = "/observe/unifieduery/verification/api/v1/{enterprise}/{project}/userid_search_appid"
_TIMEOUT = float(os.environ.get("OPENOPS_APPTREE_TIMEOUT_S", "8"))
_DEFAULT_ENTERPRISE = "88888888888888888888888888888888"
_DEFAULT_PROJECT = "00000000000000000000000000000425"
# 整条 curl URL 直贴的识别（实测坑：BASE_URL 填了完整 URL → 客户端再拼一遍路径 → 双拼 → 网关 200+status=ERROR）
_FULL_URL_RE = re.compile(
    r"^(?P<root>.+?)/observe/unifieduery/verification/api/v1/(?P<ent>[^/]+)/(?P<proj>[^/]+)/userid_search_appid$",
    re.IGNORECASE,
)

# mock 应用集（前端 mock 模式 + real facade 未配端点时的兜底，让向导无真环境也能演示平铺选择）
_MOCK_APPS: list[dict[str, str]] = [
    {"app_id": "00000000000000000000000000000423", "name": "日志管理分析(多租)", "type": "HIS-OP"},
    {"app_id": "00000000000000000000000000000425", "name": "统一查询服务", "type": "HIS-OP"},
    {"app_id": "00000000000000000000000000000601", "name": "支付核心交易", "type": "HIS-OP"},
    {"app_id": "00000000000000000000000000000602", "name": "订单履约中心", "type": "HIS-OP"},
]


def _is_real() -> bool:
    return os.environ.get("OPENOPS_APPTREE", "mock").strip().lower() == "real"


def resolve_url() -> tuple[str, str]:
    """(最终端点 URL, 装配说明)。两种配置法：
    ① `OPENOPS_APPTREE_URL`=整条端点 URL **原样使用**（最高优先，推荐——对端改文根/路径只改这一个 env，
       测试/生产不同环境各配各的，代码零改动）；
    ② `OPENOPS_APPTREE_BASE_URL`（host 根）+ 内置模板 + enterprise/project 段组装（贴整条 URL 也会被识别）。
    未配任一 → ("", "")，调用方报缺配置。"""
    verbatim = os.environ.get("OPENOPS_APPTREE_URL", "").split("#", 1)[0].strip().rstrip("/")
    if verbatim:
        return verbatim, "url(原样)"
    base, enterprise, project = _endpoint()
    if not base:
        return "", ""
    return base + _PATH_TMPL.format(enterprise=enterprise, project=project), \
        f"base+模板(enterprise={enterprise} project={project})"


def _endpoint() -> tuple[str, str, str]:
    """解析 (host 根, enterprise, project)。BASE_URL 三种填法都收：
    ① host 根（推荐）；② 整条 curl URL——自动截回 host 根并提取 enterprise/project 两段（否则路径双拼，
    网关 200+status=ERROR，实测坑）；③ 带部分路径——截到 /observe/unifieduery 前。#fragment 照剥。
    env OPENOPS_APPTREE_ENTERPRISE_ID/_PROJECT_ID 优先级最高，其次 URL 提取段，最后内置默认。"""
    raw = os.environ.get("OPENOPS_APPTREE_BASE_URL", "").split("#", 1)[0].strip().rstrip("/")
    url_ent = url_proj = ""
    m = _FULL_URL_RE.match(raw)
    if m:
        raw, url_ent, url_proj = m.group("root"), m.group("ent"), m.group("proj")
    else:
        i = raw.lower().find("/observe/unifieduery")
        if i > 0:
            raw = raw[:i]
    enterprise = os.environ.get("OPENOPS_APPTREE_ENTERPRISE_ID", "").strip() or url_ent or _DEFAULT_ENTERPRISE
    project = os.environ.get("OPENOPS_APPTREE_PROJECT_ID", "").strip() or url_proj or _DEFAULT_PROJECT
    return raw.rstrip("/"), enterprise, project


def _map_rows(data: dict[str, Any]) -> list[dict[str, str]]:
    """verification 响应 `data.datas[]` → 平铺应用列表，按 app_id 去重、丢空 app_id。"""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in data.get("datas") or []:
        if not isinstance(row, dict):
            continue
        app_id = str(row.get("dimension_code") or "").strip()
        if not app_id or app_id in seen:
            continue
        seen.add(app_id)
        out.append({
            "app_id": app_id,
            "name": str(row.get("current_name_zh") or app_id),
            "type": str(row.get("dimension_type") or ""),
        })
    return out


async def list_user_apps(user_id: str) -> list[dict[str, str]]:
    """列出该用户可见的应用（平铺）。mock/未配端点 → 兜底 mock 集；real → 打 verification 服务。

    诊断口径（联调教训：任何失败不得静默变空列表）：非 2xx 带响应体报错；HTML 响应=登录页/地址填错；
    信封 status 非 OK 也报错（曾见 200+错误信封被吞成空）。每次调用打一行 `[OpenOps][apptree]`（不含 cookie）。
    """
    if not _is_real():
        return _MOCK_APPS
    url, _how = resolve_url()
    if not url:
        raise RuntimeError("OPENOPS_APPTREE=real 但未配置端点（OPENOPS_APPTREE_URL 整条原样用，或 OPENOPS_APPTREE_BASE_URL 组装）")

    from infra.external.mcp_registry_client import (
        console_cookie,
        console_tls_verify,
        http_trust_env,
        raise_with_body,
    )

    # 联调缝：mock 登录头里的 user_id 未必是 W3 账号，允许 env 覆盖（对齐 OPENOPS_SCOPE_OVERRIDE_APPIDS 模式）
    w3 = os.environ.get("OPENOPS_APPTREE_USER_ID", "").strip() or user_id

    kwargs: dict[str, Any] = {"timeout": _TIMEOUT,
                              "verify": console_tls_verify(), "trust_env": http_trust_env()}
    cookie = console_cookie("OPENOPS_APPTREE_COOKIE")  # 专属 > 共享 OPENOPS_CONSOLE_COOKIE
    if cookie:
        kwargs["headers"] = {"Cookie": cookie}

    import httpx

    async with httpx.AsyncClient(**kwargs) as c:
        r = await c.post(url, json={"uesrId": w3})  # 上游 body 键拼写即 "uesrId"（勿改）
        raise_with_body(r)  # 非 2xx 带响应体前 300 字（401=cookie 失效、404=文根/enterprise/project 段错）
        text = (r.text or "").lstrip()
        if text.startswith("<"):  # 登录页/门户 HTML：cookie 失效或 URL 填成了前端页
            raise RuntimeError(f"应用目录返回 HTML 而非 JSON（cookie 失效或 URL 填错）：{text[:120]}")
        payload = r.json() or {}
    status = str(payload.get("status", "")).upper()
    if status and status != "OK":  # 200+错误信封（如账号无权限/参数错）——不得静默吞成空列表
        raise RuntimeError(f"应用目录返回 status={payload.get('status')}：{str(payload.get('message', ''))[:200]}")
    rows = _map_rows(payload.get("data") or {})
    print(f"[OpenOps][apptree] POST {url} uesrId={w3} -> rows={len(rows)}", flush=True)
    return rows
