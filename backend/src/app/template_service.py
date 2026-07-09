"""Template Admin（读侧；管理员编辑发版属后续块 B5）。"""
from __future__ import annotations

from typing import Any

from infra.db import row_json
from infra.repositories import templates


async def available() -> list[dict[str, Any]]:
    """普通用户可实例化模板（active）。"""
    rows = await templates.list_templates("active")
    out = []
    for t in rows:
        v = await templates.get_version(str(t["active_template_version_id"])) if t["active_template_version_id"] else None
        out.append({
            "template_id": str(t["template_id"]),
            "template_key": t["template_key"],
            "display_name": t["display_name"],
            "description": t["description"],
            "template_version_id": str(t["active_template_version_id"]) if t["active_template_version_id"] else None,
            "version_no": v["version_no"] if v else None,
            "capabilities": [s["label"] for s in (v["content_json"].get("sub_agents", []) if v else [])],
        })
    return out


async def admin_list() -> list[dict[str, Any]]:
    return [row_json(t) for t in await templates.list_templates()]
