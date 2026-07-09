"""oModel 真实 HTTP 客户端（29.1 SPL 契约）：`OPENOPS_OMODEL=real` 启用。

- 端点 `OPENOPS_OMODEL_BASE_URL`；`httpx` 惰性导入（默认 mock 路径无需 httpx）。
- 任何错误/超时/未配置 → `status=failed`，由 Scope Service fail-closed（缓存不作失败兜底，28.6）。
- V1 未联真实环境；接口签名与 mock 一致，HTTP 形状待按 29.1 对齐。
"""
from __future__ import annotations

import os
from typing import Any

_TIMEOUT = float(os.environ.get("OPENOPS_OMODEL_TIMEOUT_S", "8"))


def _base() -> str:
    return os.environ.get("OPENOPS_OMODEL_BASE_URL", "").rstrip("/")


def _failed(scope_revision: str) -> dict[str, Any]:
    return {"status": "failed", "effective_appids": [], "scope_revision": scope_revision, "omodel_request_id": ""}


async def resolve_scope(workspace_id: str, scope_revision: str, user_id: str) -> dict[str, Any]:
    base = _base()
    if not base:
        return _failed(scope_revision)
    try:
        import httpx

        async with httpx.AsyncClient(base_url=base, timeout=_TIMEOUT) as c:
            r = await c.post("/scope/resolve", json={
                "workspace_id": workspace_id, "scope_revision": scope_revision, "user_id": user_id,
            })
            r.raise_for_status()
            d = r.json()
            return {
                "status": d.get("status", "ok"),
                "effective_appids": d.get("effective_appids", []),
                "scope_revision": d.get("scope_revision", scope_revision),
                "omodel_request_id": d.get("request_id", d.get("omodel_request_id", "")),
            }
    except Exception:
        return _failed(scope_revision)  # fail-closed


async def get_workspace(workspace_id: str) -> dict[str, Any] | None:
    base = _base()
    if not base:
        return None
    try:
        import httpx

        async with httpx.AsyncClient(base_url=base, timeout=_TIMEOUT) as c:
            r = await c.get(f"/workspaces/{workspace_id}")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


async def list_workspaces() -> list[dict[str, Any]]:
    base = _base()
    if not base:
        return []
    try:
        import httpx

        async with httpx.AsyncClient(base_url=base, timeout=_TIMEOUT) as c:
            r = await c.get("/workspaces")
            r.raise_for_status()
            return r.json()
    except Exception:
        return []


async def create_workspace(name: str, app_ids: list[str]) -> dict[str, Any]:
    base = _base()
    if not base:
        raise RuntimeError("OPENOPS_OMODEL=real 但未配置 OPENOPS_OMODEL_BASE_URL")
    import httpx

    async with httpx.AsyncClient(base_url=base, timeout=_TIMEOUT) as c:
        r = await c.post("/workspaces", json={"name": name, "app_ids": app_ids})
        r.raise_for_status()
        return r.json()
