"""oModel(umodel) 真实 HTTP 客户端（29.7 最终契约，2026-07-10）：`OPENOPS_OMODEL=real` 启用。

- host root `OPENOPS_OMODEL_BASE_URL`；client 拼 `/api/v1/workspaces...`；`httpx` 惰性导入（默认 mock 路径无需 httpx）。
- 出站三件套同 console（内网教训）：TLS 三档 `console_tls_verify` + 代理 `http_trust_env`（默认不信任
  env/注册表代理）+ 可选 `OPENOPS_OMODEL_COOKIE`（umodel 开 IAM `omodel.iam.validation.enable=true` 时带
  session cookie；29.7 显示 workspace 端点匿名可用「未登录=system」，默认不带）。
- `resolve_scope` 走 29.7 端点「列出工作空间关联项目」（`GET /{ws}/projects`）：`effective_appids` = `project_id` 列表。
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
import os
import uuid
from typing import Any

_TIMEOUT = float(os.environ.get("OPENOPS_OMODEL_TIMEOUT_S", "8"))


def _prefix() -> str:
    """workspace API 文根：默认 29.7 的 `/api/v1/workspaces`；测试/生产网关文根不同或对端改文根时
    设 `OPENOPS_OMODEL_API_PREFIX` 覆盖，免改码（host 差异走 BASE_URL，文根差异走本变量）。"""
    p = os.environ.get("OPENOPS_OMODEL_API_PREFIX", "/api/v1/workspaces").strip()
    return "/" + p.strip("/")


def _base() -> str:
    # 剥 URL fragment：用户常把浏览器地址栏整串贴进来（如 .../omodel/#），`#` 后是前端路由不是路径
    return os.environ.get("OPENOPS_OMODEL_BASE_URL", "").split("#", 1)[0].rstrip("/")


def _client_kwargs(base: str) -> dict[str, Any]:
    """umodel 出站 httpx 客户端参数：TLS/代理与 console 同口径（内网教训），可选 IAM session cookie
    （OPENOPS_OMODEL_COOKIE，未设回退共享 OPENOPS_CONSOLE_COOKIE——三面同一登录态，过期只换一处）。"""
    from infra.external.mcp_registry_client import console_cookie, console_tls_verify, http_trust_env

    kwargs: dict[str, Any] = {"base_url": base, "timeout": _TIMEOUT,
                              "verify": console_tls_verify(), "trust_env": http_trust_env()}
    cookie = console_cookie("OPENOPS_OMODEL_COOKIE")
    if cookie:
        kwargs["headers"] = {"Cookie": cookie}
    return kwargs


def _derive_rev(effective_appids: list[str]) -> str:
    """OpenOps 私有 scope_revision：范围内容 hash（内容变才变）。避开 umodel 同名列/resourceVersion 的过度失效（29.6 §三）。"""
    joined = "\0".join(sorted(effective_appids))
    return "sc-" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def _failed(scope_revision: str) -> dict[str, Any]:
    return {"status": "failed", "effective_appids": [], "scope_revision": scope_revision, "omodel_request_id": ""}


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
    """
    base = _base()
    if not base:
        return _failed(scope_revision)
    try:
        import httpx

        async with httpx.AsyncClient(**_client_kwargs(base)) as c:
            r = await c.get(f"{_prefix()}/{workspace_id}/projects")
            if r.status_code == 404:
                return _failed(scope_revision)  # workspace 不存在/已删除
            r.raise_for_status()
            projects = r.json() or []
            appids = sorted(p["project_id"] for p in projects if isinstance(p, dict) and p.get("project_id"))
            rid = r.headers.get("X-Request-Id") or "req_" + uuid.uuid4().hex[:10]
            return {"status": "ok", "effective_appids": appids,
                    "scope_revision": _derive_rev(appids), "omodel_request_id": rid}
    except Exception:
        return _failed(scope_revision)  # fail-closed（含超时/连接错/解析错）


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


async def list_workspaces() -> list[dict[str, Any]]:
    base = _base()
    if not base:
        return []
    try:
        import httpx

        async with httpx.AsyncClient(**_client_kwargs(base)) as c:
            r = await c.get(_prefix())
            r.raise_for_status()
            page = r.json() or {}
            return [_map_metadata(md) for md in page.get("items", [])]  # 解 Page<WorkspaceMetadata>.items
    except Exception:
        return []


async def create_workspace(name: str, app_ids: list[str], *,
                           app_names: dict[str, str] | None = None, owner: str = "") -> dict[str, Any]:
    base = _base()
    if not base:
        raise RuntimeError("OPENOPS_OMODEL=real 但未配置 OPENOPS_OMODEL_BASE_URL")
    import httpx

    # 请求体镜像 umodel UI 实抓包（2026-07-11 F12，比 29.7 文档权威）：
    # - labels 与 workspace_ui 的 tenantId/projectId 都是字面量 "default"（UI 原样；tenant 可 env 覆盖）；
    # - scopes=object[]（{projectId, projectCn}；UI 还带 per-项目 tenantId，响应即剥掉，故省略）；
    # - status:"running" + owner（服务端会按登录态改写 owner，带上以镜像 UI）。
    # - **id 不传**（拍板 2026-07-11：workspace id 由 umodel 服务端生成，调用方不得自造——对端已
    #   从 create 契约移除该字段并部署）。
    names = app_names or {}
    tenant = os.environ.get("OPENOPS_OMODEL_TENANT_ID", "").strip() or "default"
    ui: dict[str, Any] = {
        "tenantId": tenant, "projectId": "default",
        "scopes": [{"projectId": a, "projectCn": names.get(a) or a} for a in app_ids],
        "status": "running",
    }
    if owner:
        ui["owner"] = owner
    body: dict[str, Any] = {
        "name": name, "description": "",
        "labels": {"tenantId": tenant, "projectId": "default"},
        "config": {"workspace_ui": ui},
    }
    async with httpx.AsyncClient(**_client_kwargs(base)) as c:
        r = await c.post(_prefix(), json=body)
        if r.status_code >= 400:
            # umodel 错误信封 {code,message} 在响应体里——400 INVALID_ARGUMENT 必须透出原因（吞掉没法定位）
            raise RuntimeError(f"umodel 创建 workspace HTTP {r.status_code}：{(r.text or '')[:300]}")
        return _map_metadata(r.json())
