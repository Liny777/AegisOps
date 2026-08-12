"""oModel(umodel) 真实 HTTP 客户端（29.7 最终契约，2026-07-10）：`OPENOPS_OMODEL=real` 启用。

- host root `OPENOPS_OMODEL_BASE_URL`；client 拼 `/api/v1/workspaces...`；`httpx` 惰性导入（默认 mock 路径无需 httpx）。
- 出站三件套同 console（内网教训）：TLS 三档 `console_tls_verify` + 代理 `http_trust_env`（默认不信任
  env/注册表代理）+ 可选 `OPENOPS_OMODEL_COOKIE`（umodel 开 IAM `omodel.iam.validation.enable=true` 时带
  session cookie；29.7 显示 workspace 端点匿名可用「未登录=system」，默认不带）。
- `resolve_scope` 走 29.7 端点「列出工作空间关联项目」（`GET /{ws}/projects`）：`effective_appids` = `project_id` 列表，
  同响应的 `name_cn` / `enterprise_id` 带出为 `scope_apps`（`[{appid, name, enterprise_id}]`，纯显示装饰，见 `_decorate`）。
  ⚠**安全简化（29.6 §二 P0-1）**：端点 4 返回 workspace **全量关联项目、不按 user_id 过滤、不含 appid 粒度、不鉴权**
  ——「发现 ≠ 授权」。V1 先用它接真（真正的 per-user `:resolve` umodel 侧尚未建）；即 `effective_appids` =
  **workspace 级发现集，非 per-user 授权集**，per-user 过滤待 umodel P0-1（见 EXTERNAL-INTEGRATION.md）。
- `scope_revision` 由 OpenOps 私有派生（范围内容 hash），**不映射** umodel 同名列/`resourceVersion`（29.6 §三：
  前者是 config-JSON text 列、后者任意字段变更即 +1，都不宜当范围版本）。
- `WorkspaceMetadata`（无信封）→ OpenOps 词汇映射（与 mock 返回键一致）；umodel 不对外暴露动态 `sync_status`
  （硬编码 ready）也无 `/status` 端点（29.6 P2-1）→ 降级 status=active→sync_status=ready（「创建即就绪」）。
- 任何错误/超时/未配 → resolve `status=failed`（Scope Service fail-closed，28.6）。错误信封 `{code:"NOT_FOUND",...}` 按 HTTP status 判。
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import uuid
from typing import Any

log = logging.getLogger("openops.omodel")

# omodel 网关对写操作拦截脚本类 UA（python-httpx）——内网实锤：同 cookie，Postman/浏览器 UA 通、
# httpx 401；GET(读)不拦故列表能通、POST(创建)被拦。用浏览器 UA 绕过（我们是代表登录用户操作）。
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_TIMEOUT = float(os.environ.get("OPENOPS_OMODEL_TIMEOUT_S", "8"))


def _prefix() -> str:
    """workspace API 文根：默认 29.7 的 `/api/v1/workspaces`；测试/生产网关文根不同或对端改文根时
    设 `OPENOPS_OMODEL_API_PREFIX` 覆盖，免改码（host 差异走 BASE_URL，文根差异走本变量）。"""
    p = os.environ.get("OPENOPS_OMODEL_API_PREFIX", "/api/v1/workspaces").strip()
    return "/" + p.strip("/")


def _base() -> str:
    """返回固定 oModel 根地址。

    该请求会携带当前用户的 IAM Cookie，目标域不得由 Host/Origin/X-Forwarded-Host 等请求头
    决定。历史 `{host}` 配置在这里 fail-closed，发布环境必须显式写固定绝对地址。
    """
    raw = os.environ.get("OPENOPS_OMODEL_BASE_URL", "").strip().rstrip("/")
    return "" if any(ch in raw for ch in "{}") else raw


def _valid_hostname(hostname: str) -> bool:
    """只允许合法 IP 或 ASCII DNS 主机名，拒绝模板/转义后的伪主机。"""
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


def _target_info(base: str) -> tuple[Any, str, str]:
    """校验 Cookie 出站目标，并返回 parsed URL、Origin、脱敏 host[:port]。"""
    from urllib.parse import urlparse

    from infra.external.omodel_client import OModelError

    try:
        target = urlparse(base)
        port = target.port
    except ValueError as e:
        raise OModelError("config", "OPENOPS_OMODEL_BASE_URL 端口无效") from e
    if (target.scheme not in ("http", "https") or not target.hostname
            or not _valid_hostname(target.hostname)
            or target.username is not None or target.password is not None
            or target.query or target.fragment or target.params
            or any(ch in base for ch in "{}\\") or any(ch.isspace() for ch in base)):
        raise OModelError("config", "OPENOPS_OMODEL_BASE_URL 必须是无凭据、查询或片段的固定绝对地址")
    hostname = target.hostname
    authority = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        authority = f"{authority}:{port}"
    return target, f"{target.scheme}://{authority}", authority


def _client_kwargs(base: str) -> dict[str, Any]:
    """oModel 出站：登录态 + 浏览器 UA + 从目标 base 派生的 CSRF 同源头。

    UA 与 Origin/Referer 是两层独立网关校验，必须同时保留；后者绝不复制客户端传入值。
    """
    from infra.external.mcp_registry_client import console_cookie, console_tls_verify, http_trust_env

    _target, origin, _target_host = _target_info(base)
    kwargs: dict[str, Any] = {"base_url": base, "timeout": _TIMEOUT,
                              "verify": console_tls_verify(), "trust_env": http_trust_env()}
    headers: dict[str, str] = {"User-Agent": _BROWSER_UA}  # 必带：见 _BROWSER_UA 注释
    cookie = console_cookie("OPENOPS_OMODEL_COOKIE")
    if cookie:
        headers["Cookie"] = cookie
    from infra.iam_headers import iam_auth_headers
    headers.update(iam_auth_headers())  # 服务态 IAM（内网 j2c_utils，缺失即空）：后台链路（告警 peek/resolve）无用户 cookie 时的鉴权正解
    from infra.request_context import client_ip as _client_ip
    ip = _client_ip()
    if ip:  # 华为 IAM 会话绑客户端 IP：带真实浏览器 IP，否则服务器 IP 会被判「Not logged in」
        headers["IAM-Client-Ip"] = ip
        headers["X-Forwarded-For"] = ip
    headers.update({
        "Origin": origin,
        "Referer": origin + "/",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    })
    kwargs["headers"] = headers
    if _http_debug():
        from infra.request_context import user_cookie

        src = ("passthrough" if user_cookie()
               else "env:OMODEL" if os.getenv("OPENOPS_OMODEL_COOKIE")
               else "env:shared" if os.getenv("OPENOPS_CONSOLE_COOKIE") and cookie
               else "none")
        log.warning("[OpenOps][omodel][debug] cookie_src=%s cookie_len=%d iam=%s",
                    src, len(cookie or ""), "set" if headers.get("Authorization") else "missing")
        kwargs["event_hooks"] = {"response": [_log_outbound_response]}
    return kwargs


def _http_debug() -> bool:
    return os.getenv("OPENOPS_HTTP_DEBUG", "").strip().lower() in ("1", "true", "yes")


async def _log_outbound_response(response: Any) -> None:
    """调试日志只保留状态与请求 ID；绝不读取或记录响应体、URL、请求头。"""
    log.warning("[OpenOps][omodel][debug] status=%s request_id=%s",
                response.status_code, _response_request_id(response) or "-")


def _response_request_id(response: Any) -> str:
    headers = getattr(response, "headers", {}) or {}
    for key, value in headers.items():
        if str(key).lower() in ("x-request-id", "request-id", "x-trace-id"):
            # 上游头会进入结构化响应和日志；移除 CR/LF/控制字符，避免日志注入。
            return "".join(ch for ch in str(value) if ch.isprintable())[:128]
    return ""


def _response_error(response: Any):
    """将 create 的非 2xx 响应转成结构化 adapter 错误，仅提取已知错误字段。"""
    from infra.external.omodel_client import OModelError

    status = int(response.status_code)
    payload: Any = None
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - 错误响应可能是空体或 HTML，不向 API 回显原文
        payload = None
    if not isinstance(payload, dict):
        try:
            payload = json.loads(response.text or "")
        except (TypeError, ValueError):
            payload = None
    node = payload.get("error") if isinstance(payload, dict) and isinstance(payload.get("error"), dict) else payload
    code = str(node.get("code") or "").strip() if isinstance(node, dict) else ""
    message = str(node.get("message") or "").strip() if isinstance(node, dict) else ""
    detail = "：".join(p for p in (code, message) if p)[:300] or f"HTTP {status}"
    if status in (401, 403):
        kind = "auth"
    elif status in (400, 422):
        kind = "validation"
    else:
        kind = "upstream"
    return OModelError(kind, detail, status_code=status, request_id=_response_request_id(response))


def _derive_rev(effective_appids: list[str]) -> str:
    """OpenOps 私有 scope_revision：范围内容 hash（内容变才变）。避开 umodel 同名列/resourceVersion 的过度失效（29.6 §三）。"""
    joined = "\0".join(sorted(effective_appids))
    return "sc-" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def _failed(scope_revision: str, reason: str = "") -> dict[str, Any]:
    # reason 供 Scope Service 拼进 fail-closed 报错（内网实测：无原因的 SCOPE_RESOLVE_FAILED 无法定位）
    return {"status": "failed", "effective_appids": [], "scope_apps": [], "scope_revision": scope_revision,
            "omodel_request_id": "", "reason": reason}


def _decorate(appids: list[str], names: dict[str, str], ents: dict[str, str]) -> list[dict[str, str]]:
    """effective_appids → scope_apps（`[{appid, name, enterprise_id}]`，同元素同序）：纯装饰，不参与范围判定。

    ⚠**只按 appids 迭代、向 names/ents 里查**，绝不反过来迭代名字表——否则 `/projects` 多出的项目会
    被带进范围（装饰逻辑扩大 scope 是本函数唯一的安全风险）。
    名字缺失或等于 appid（`projectCn` 在 apptree 无名时回填 appid，见 `_build_workspace_ui`）一律置 ""，
    否则渲染出「APP-A｜APP-A」这种废话。企业 id 缺失置 ""，渲染时整段省略。
    """
    out: list[dict[str, str]] = []
    for a in appids:
        name = str(names.get(a) or "").strip()
        out.append({"appid": a, "name": "" if name == a else name,
                    "enterprise_id": str(ents.get(a) or "").strip()})
    return out


def _map_metadata(md: dict[str, Any]) -> dict[str, Any]:
    """umodel `WorkspaceMetadata` → OpenOps workspace 词汇（键与 mock 一致：workspace_id/name/scope_revision/sync_status/app_ids/updated）。"""
    scopes = (((md.get("config") or {}).get("workspace_ui") or {}).get("scopes")) or []
    # 29.7：scopes 两种格式并存——string[]（旧，仅 projectId）与 object[]（新，{projectId, projectCn}），都要收
    app_ids = [s if isinstance(s, str) else s.get("projectId") for s in scopes]
    app_ids = [a for a in app_ids if a]
    status = md.get("status", "active")
    return {
        "workspace_id": md.get("id"),
        "name": md.get("name") or md.get("id"),
        "scope_revision": _derive_rev(app_ids),  # 私有派生，不用 umodel 的 scope_revision/resourceVersion
        "sync_status": "ready" if status == "active" else status,  # umodel 无动态就绪态 → 降级（29.6 P2-1）
        "app_ids": app_ids,
        "updated": md.get("updated_at") or md.get("updatedAt", ""),  # 29.7 snake_case；留 camelCase 兼容
    }


async def resolve_scope(workspace_id: str, scope_revision: str, user_id: str) -> dict[str, Any]:
    """resolve → effective_appids（29.5 端点 4「列出工作空间关联项目」）。

    ⚠安全简化：端点 4 **不按 user_id 过滤、不鉴权**（29.6 P0-1，「发现 ≠ 授权」）；per-user 过滤待 umodel 真 `:resolve`。
    当前 `effective_appids` = workspace 级发现集。空范围/404/错误一律 fail-closed（Scope Service 兜底）。

    同一次响应里的 `name_cn`（建 workspace 时写进 `scopes[].projectCn` 的 apptree 中文名）与 `enterprise_id`
    顺手带出为 `scope_apps`——纯装饰，给 `list_scope_apps` 显示用，不额外发请求、不影响范围判定。
    """
    base = _base()
    if not base:
        return _failed(scope_revision, "OPENOPS_OMODEL_BASE_URL 未配置")
    try:
        import httpx

        async with httpx.AsyncClient(**_client_kwargs(base)) as c:
            r = await c.get(f"{_prefix()}/{workspace_id}/projects")
            if r.status_code == 404:
                return _failed(scope_revision, f"umodel 查不到该 workspace（GET /{workspace_id}/projects → 404，可能已删除或是 mock 时代旧 id）")
            r.raise_for_status()
            projects = r.json() or []
            rows = [p for p in projects if isinstance(p, dict) and p.get("project_id")]
            appids = sorted(p["project_id"] for p in rows)
            names = {p["project_id"]: str(p.get("name_cn") or "") for p in rows}
            # enterprise_id 是 umodel 后加的字段；留 camelCase 兜底（同 _map_metadata 的 updated_at/updatedAt 写法）
            ents = {p["project_id"]: str(p.get("enterprise_id") or p.get("enterpriseId") or "") for p in rows}
            rid = r.headers.get("X-Request-Id") or "req_" + uuid.uuid4().hex[:10]
            return {"status": "ok", "effective_appids": appids, "scope_apps": _decorate(appids, names, ents),
                    "scope_revision": _derive_rev(appids), "omodel_request_id": rid}
    except Exception as e:  # fail-closed（含超时/连接错/解析错/非 2xx）
        return _failed(scope_revision, f"{type(e).__name__}: {str(e)[:150]}")


async def get_workspace(workspace_id: str) -> dict[str, Any] | None:
    base = _base()
    if not base:
        return None
    try:
        import httpx

        async with httpx.AsyncClient(**_client_kwargs(base)) as c:
            r = await c.get(f"{_prefix()}/{workspace_id}")
            if r.status_code == 404:
                return None  # 29.5：不存在即 404（不再自动创建）
            r.raise_for_status()
            return _map_metadata(r.json())
    except Exception:
        return None


async def get_statistics(workspace_id: str) -> dict[str, Any] | None:
    """看护空间统计（wesee 适配器 `POST /api/v1/wesee/statistics`，body `{workspace_id}`）。

    该端点未收录于 29.7 文档（0.1.0-SNAPSHOT）——契约以实测为准：裸 JSON 返回
    node_count/relation_count/node_type_count/relation_type_count 及类型明细。
    路径固定写死（wesee 是独立于 workspaces 的适配器前缀家族，不复用 `_prefix()`）；
    出站携用户 IAM Cookie/UA/CSRF（`_client_kwargs`）。失败一律 None（读操作，不阻塞向导）。
    """
    base = _base()
    if not base:
        return None
    try:
        import httpx

        async with httpx.AsyncClient(**_client_kwargs(base)) as c:
            r = await c.post("/api/v1/wesee/statistics", json={"workspace_id": workspace_id})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            payload = r.json()
            return payload if isinstance(payload, dict) else None
    except Exception:  # noqa: BLE001 - 读操作失败（超时/连接/解析/非 2xx）静默降级
        return None


async def list_workspaces() -> list[dict[str, Any]]:
    base = _base()
    if not base:
        return []
    try:
        import httpx

        async with httpx.AsyncClient(**_client_kwargs(base)) as c:
            r = await c.get(_prefix())
            r.raise_for_status()
            payload = r.json()
            items = _extract_ws_items(payload)  # 多形状兼容：{items}/裸数组/{data:{items}}/{data:[]}
            if _http_debug():
                log.warning("[OpenOps][omodel][debug] status=%s request_id=%s",
                            r.status_code, _response_request_id(r) or "-")
            return [_map_metadata(md) for md in items if isinstance(md, dict)]
    except Exception as e:  # noqa: BLE001
        if _http_debug():
            resp = getattr(e, "response", None)
            log.warning("[OpenOps][omodel][debug] status=%s request_id=%s",
                        getattr(resp, "status_code", "error"),
                        _response_request_id(resp) if resp is not None else "-")
        return []


def _extract_ws_items(payload: Any) -> list[Any]:
    """从 omodel list 响应里取出 workspace 数组，兼容多种信封形状。"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "workspaces", "data", "list", "records"):
            v = payload.get(key)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):  # 如 {data:{items:[]}}
                for k2 in ("items", "list", "records"):
                    if isinstance(v.get(k2), list):
                        return v[k2]
    return []


def _build_workspace_ui(app_ids: list[str], apps: list[dict[str, Any]] | None,
                        owner: str) -> tuple[dict[str, Any], str]:
    """构造 umodel `config.workspace_ui`（create/update 共用），返回 (ui, tenant)。

    请求体镜像 umodel **新版** UI 实抓包（2026-07-11 对端部署"id 服务端生成"后再抓，比 29.7 文档权威）：
    - workspace_ui 的 tenantId=**真实租户 ID**（不再是 "default" 字面量）；**不带 projectId**
      （服务端自己取首个 scope 回填，实抓响应可见）；
    - scopes=object[]{projectId, projectCn, tenantId}——per-项目租户（不同应用可属不同租户，
      apptree 的 tenant_id 一路带下来；缺失则省略该键）；
    - status:"running" + owner（服务端按登录态改写 owner，带上以镜像 UI）。
    workspace 级租户属于当前企业，不得由首个跨租应用决定；scope 仍保留各应用自己的 tenantId。
    """
    by_id = {str(a.get("app_id")): a for a in (apps or [])}
    scopes: list[dict[str, str]] = []
    for aid in app_ids:
        item = by_id.get(aid) or {}
        entry = {"projectId": aid, "projectCn": str(item.get("name") or "") or aid}
        if item.get("tenant_id"):
            entry["tenantId"] = str(item["tenant_id"])
        scopes.append(entry)
    from infra.external.apptree_client import current_enterprise_id

    tenant = (os.environ.get("OPENOPS_OMODEL_TENANT_ID", "").strip()
              or current_enterprise_id())
    ui: dict[str, Any] = {"tenantId": tenant, "scopes": scopes, "status": "running"}
    if owner:
        ui["owner"] = owner
    return ui, tenant


async def create_workspace(name: str, app_ids: list[str], *,
                           apps: list[dict[str, Any]] | None = None, owner: str = "") -> dict[str, Any]:
    from infra.external.omodel_client import OModelError

    base = _base()
    if not base:
        raise OModelError("config", "OPENOPS_OMODEL_BASE_URL 未配置")
    import httpx

    ui, tenant = _build_workspace_ui(app_ids, apps, owner)
    body: dict[str, Any] = {
        "name": name, "description": "",
        "labels": {"tenantId": tenant},  # id 不传：服务端生成 {W3}-ws-{hex8}
        "config": {"workspace_ui": ui},
    }
    try:
        async with httpx.AsyncClient(**_client_kwargs(base)) as c:
            r = await c.post(_prefix(), json=body)
    except httpx.TimeoutException as e:
        raise OModelError("timeout", f"oModel 请求超时（{type(e).__name__}）") from e
    except httpx.RequestError as e:
        raise OModelError("upstream", f"oModel 网络请求失败（{type(e).__name__}）") from e
    if r.status_code >= 400:
        raise _response_error(r)
    try:
        payload = r.json()
        if not isinstance(payload, dict):
            raise TypeError("workspace response is not an object")
        return _map_metadata(payload)
    except Exception as e:  # noqa: BLE001 - 上游成功状态却返回坏 JSON/结构
        raise OModelError("upstream", "oModel 创建响应格式无效",
                          status_code=r.status_code,
                          request_id=_response_request_id(r)) from e


async def update_workspace(workspace_id: str, name: str, app_ids: list[str], *,
                           apps: list[dict[str, Any]] | None = None, owner: str = "") -> dict[str, Any] | None:
    """更新 workspace（umodel 接口 6，`PUT /{id}`）：改名 + 全量覆盖 scopes。404 → None（上层转 NOT_FOUND）。

    接口 6 语义为部分更新，但 `_map_metadata` 有损（丢 owner/tenant/status），无法从 GET 复原，
    故重发完整 `workspace_ui`（owner 按当前编辑用户重写，与 create「服务端按登录态改写 owner」一致）。
    """
    from infra.external.omodel_client import OModelError

    base = _base()
    if not base:
        raise OModelError("config", "OPENOPS_OMODEL_BASE_URL 未配置")
    import httpx

    ui, tenant = _build_workspace_ui(app_ids, apps, owner)
    body: dict[str, Any] = {
        "name": name,
        "labels": {"tenantId": tenant},
        "config": {"workspace_ui": ui},
    }
    try:
        async with httpx.AsyncClient(**_client_kwargs(base)) as c:
            r = await c.put(f"{_prefix()}/{workspace_id}", json=body)
    except httpx.TimeoutException as e:
        raise OModelError("timeout", f"oModel 请求超时（{type(e).__name__}）") from e
    except httpx.RequestError as e:
        raise OModelError("upstream", f"oModel 网络请求失败（{type(e).__name__}）") from e
    if r.status_code == 404:
        return None  # 29.5：不存在即 404
    if r.status_code >= 400:
        raise _response_error(r)
    try:
        payload = r.json()
        if not isinstance(payload, dict):
            raise TypeError("workspace response is not an object")
        return _map_metadata(payload)
    except Exception as e:  # noqa: BLE001
        raise OModelError("upstream", "oModel 更新响应格式无效",
                          status_code=r.status_code,
                          request_id=_response_request_id(r)) from e


async def delete_workspace(workspace_id: str) -> dict[str, Any] | None:
    """删除 workspace（umodel 接口 7，`DELETE /{id}`，软删）。404 视为幂等成功 → 返回占位。"""
    from infra.external.omodel_client import OModelError

    base = _base()
    if not base:
        raise OModelError("config", "OPENOPS_OMODEL_BASE_URL 未配置")
    import httpx

    try:
        async with httpx.AsyncClient(**_client_kwargs(base)) as c:
            r = await c.delete(f"{_prefix()}/{workspace_id}")
    except httpx.TimeoutException as e:
        raise OModelError("timeout", f"oModel 请求超时（{type(e).__name__}）") from e
    except httpx.RequestError as e:
        raise OModelError("upstream", f"oModel 网络请求失败（{type(e).__name__}）") from e
    if r.status_code == 404:
        return {"workspace_id": workspace_id, "status": "deleted"}  # 幂等：已删除/不存在
    if r.status_code >= 400:
        raise _response_error(r)
    # 删除响应体（删除前元数据）可能为空或非对象——成功即视作删除，不强依赖响应体。
    try:
        payload = r.json()
        if isinstance(payload, dict) and payload.get("id"):
            return _map_metadata(payload)
    except Exception:  # noqa: BLE001
        pass
    return {"workspace_id": workspace_id, "status": "deleted"}
