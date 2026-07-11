"""应用目录客户端（初始化「从应用创建系统范围」的选源）：按 `OPENOPS_APPTREE=mock`（默认）|`real` 切换。

real 走内网 verification 服务 `userid_search_appid`（按 W3 账号搜该用户可见的 APPID），返回**平铺**应用列表
（`app_id=dimension_code` / `name=current_name_zh` / `type=dimension_type`），按 app_id 去重（一人多角色→同
appid 多行）。出站硬化与 console/omodel 同口径（TLS 三档 + trust_env 默认 off + 可选 IAM cookie）。

⚠对端拼写照抄勿改：URL 段 `unifieduery`、请求体键 `uesrId` 都是上游真实拼写（curl 实证），非笔误请勿"修正"。
"""
from __future__ import annotations

import os
from typing import Any

# {enterprise}/{project} 由 env 配置（联调环境用默认值）；path/键的拼写是对端真实契约（见模块 docstring）
_PATH_TMPL = "/observe/unifieduery/verification/api/v1/{enterprise}/{project}/userid_search_appid"
_TIMEOUT = float(os.environ.get("OPENOPS_APPTREE_TIMEOUT_S", "8"))
_DEFAULT_ENTERPRISE = "88888888888888888888888888888888"
_DEFAULT_PROJECT = "00000000000000000000000000000425"

# mock 应用集（前端 mock 模式 + real facade 未配端点时的兜底，让向导无真环境也能演示平铺选择）
_MOCK_APPS: list[dict[str, str]] = [
    {"app_id": "00000000000000000000000000000423", "name": "日志管理分析(多租)", "type": "HIS-OP"},
    {"app_id": "00000000000000000000000000000425", "name": "统一查询服务", "type": "HIS-OP"},
    {"app_id": "00000000000000000000000000000601", "name": "支付核心交易", "type": "HIS-OP"},
    {"app_id": "00000000000000000000000000000602", "name": "订单履约中心", "type": "HIS-OP"},
]


def _is_real() -> bool:
    return os.environ.get("OPENOPS_APPTREE", "mock").strip().lower() == "real"


def _base() -> str:
    # 剥 URL fragment（用户常把浏览器地址栏整串贴进来，`#` 后是前端路由不是路径），与 omodel_real 同口径
    return os.environ.get("OPENOPS_APPTREE_BASE_URL", "").split("#", 1)[0].rstrip("/")


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
    base = _base()
    if not base:
        raise RuntimeError("OPENOPS_APPTREE=real 但未配置 OPENOPS_APPTREE_BASE_URL")

    from infra.external.mcp_registry_client import (
        console_cookie,
        console_tls_verify,
        http_trust_env,
        raise_with_body,
    )

    enterprise = os.environ.get("OPENOPS_APPTREE_ENTERPRISE_ID", _DEFAULT_ENTERPRISE)
    project = os.environ.get("OPENOPS_APPTREE_PROJECT_ID", _DEFAULT_PROJECT)
    # 联调缝：mock 登录头里的 user_id 未必是 W3 账号，允许 env 覆盖（对齐 OPENOPS_SCOPE_OVERRIDE_APPIDS 模式）
    w3 = os.environ.get("OPENOPS_APPTREE_USER_ID", "").strip() or user_id
    path = _PATH_TMPL.format(enterprise=enterprise, project=project)

    kwargs: dict[str, Any] = {"base_url": base, "timeout": _TIMEOUT,
                              "verify": console_tls_verify(), "trust_env": http_trust_env()}
    cookie = console_cookie("OPENOPS_APPTREE_COOKIE")  # 专属 > 共享 OPENOPS_CONSOLE_COOKIE
    if cookie:
        kwargs["headers"] = {"Cookie": cookie}

    import httpx

    async with httpx.AsyncClient(**kwargs) as c:
        r = await c.post(path, json={"uesrId": w3})  # 上游 body 键拼写即 "uesrId"（勿改）
        raise_with_body(r)  # 非 2xx 带响应体前 300 字（401=cookie 失效、404=enterprise/project 段错）
        text = (r.text or "").lstrip()
        if text.startswith("<"):  # 登录页/门户 HTML：cookie 失效或 base_url 填成了前端页
            raise RuntimeError(f"应用目录返回 HTML 而非 JSON（cookie 失效或 BASE_URL 填错）：{text[:120]}")
        payload = r.json() or {}
    status = str(payload.get("status", "")).upper()
    if status and status != "OK":  # 200+错误信封（如账号无权限/参数错）——不得静默吞成空列表
        raise RuntimeError(f"应用目录返回 status={payload.get('status')}：{str(payload.get('message', ''))[:200]}")
    rows = _map_rows(payload.get("data") or {})
    print(f"[OpenOps][apptree] POST {base}{path} uesrId={w3} -> rows={len(rows)}", flush=True)
    return rows
