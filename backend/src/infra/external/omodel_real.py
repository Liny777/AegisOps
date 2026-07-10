"""oModel 真实 HTTP 客户端（29.5 workspace 契约）：`OPENOPS_OMODEL=real` 启用。

- host root `OPENOPS_OMODEL_BASE_URL`；client 拼 `/api/v1/workspaces...`；`httpx` 惰性导入（默认 mock 路径无需 httpx）。
- `resolve_scope` 走 29.5 端点 4「列出工作空间关联项目」（`GET /{ws}/projects`）：`effective_appids` = `project_id` 列表。
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
_PREFIX = "/api/v1/workspaces"


def _base() -> str:
    return os.environ.get("OPENOPS_OMODEL_BASE_URL", "").rstrip("/")


def _derive_rev(effective_appids: list[str]) -> str:
    """OpenOps 私有 scope_revision：范围内容 hash（内容变才变）。避开 umodel 同名列/resourceVersion 的过度失效（29.6 §三）。"""
    joined = "\0".join(sorted(effective_appids))
    return "sc-" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def _failed(scope_revision: str) -> dict[str, Any]:
    return {"status": "failed", "effective_appids": [], "scope_revision": scope_revision, "omodel_request_id": ""}


def _map_metadata(md: dict[str, Any]) -> dict[str, Any]:
    """umodel `WorkspaceMetadata` → OpenOps workspace 词汇（键与 mock 一致：workspace_id/name/scope_revision/sync_status/app_ids/updated）。"""
    scopes = (((md.get("config") or {}).get("workspace_ui") or {}).get("scopes")) or []
    app_ids = [s["projectId"] for s in scopes if isinstance(s, dict) and s.get("projectId")]
    status = md.get("status", "active")
    return {
        "workspace_id": md.get("id"),
        "name": md.get("name") or md.get("id"),
        "scope_revision": _derive_rev(app_ids),  # 私有派生，不用 umodel 的 scope_revision/resourceVersion
        "sync_status": "ready" if status == "active" else status,  # umodel 无动态就绪态 → 降级（29.6 P2-1）
        "app_ids": app_ids,
        "updated": md.get("updatedAt", ""),
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

        async with httpx.AsyncClient(base_url=base, timeout=_TIMEOUT) as c:
            r = await c.get(f"{_PREFIX}/{workspace_id}/projects")
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

        async with httpx.AsyncClient(base_url=base, timeout=_TIMEOUT) as c:
            r = await c.get(f"{_PREFIX}/{workspace_id}")
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

        async with httpx.AsyncClient(base_url=base, timeout=_TIMEOUT) as c:
            r = await c.get(_PREFIX)
            r.raise_for_status()
            page = r.json() or {}
            return [_map_metadata(md) for md in page.get("items", [])]  # 解 Page<WorkspaceMetadata>.items
    except Exception:
        return []


async def create_workspace(name: str, app_ids: list[str]) -> dict[str, Any]:
    base = _base()
    if not base:
        raise RuntimeError("OPENOPS_OMODEL=real 但未配置 OPENOPS_OMODEL_BASE_URL")
    import httpx

    # app_ids → 29.5 项目级 scopes（P1-1 粒度落差：OpenOps 的 appid 直接当 projectId，待 umodel 明确展开口径）
    body = {"name": name, "config": {"workspace_ui": {"scopes": [{"projectId": a} for a in app_ids]}}}
    async with httpx.AsyncClient(base_url=base, timeout=_TIMEOUT) as c:
        r = await c.post(_PREFIX, json=body)
        r.raise_for_status()  # 409 ALREADY_EXISTS / 400 INVALID_ARGUMENT 直接抛（调用方收口）
        return _map_metadata(r.json())
