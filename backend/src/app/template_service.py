"""Template Admin：读侧 + 版本写闭环（B7·二：草稿/发布/禁用，发布后不可变）。"""
from __future__ import annotations

import uuid
from typing import Any

from app import mcp_tool_annotation_service
from domain.errors import ApiError, Err
from infra.db import row_json
from infra.repositories import audit, templates


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


# ---- B7·二：模板版本写闭环 ----
async def _validate_content(content: dict[str, Any]) -> None:
    """保存/发布校验（30.6 规则）：结构 + 绑定的平台 tool 必须已 allowed 标注（未 allowed 不可绑）。"""
    main = content.get("main")
    if not isinstance(main, dict) or not str(main.get("role", "")).strip():
        raise ApiError(Err.VALIDATION_FAILED, "content_json.main.role 必填")
    tools = main.get("default_tools", [])
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise ApiError(Err.VALIDATION_FAILED, "main.default_tools 必须是工具名数组")
    if not isinstance(content.get("sub_agents", []), list):
        raise ApiError(Err.VALIDATION_FAILED, "sub_agents 必须是数组")
    anns = await mcp_tool_annotation_service.runtime_annotations()  # 全部已标注工具（含 blocked，运行时另拦）
    allowed = {k for k, v in anns.items() if v.get("status") == "allowed"}  # 绑定校验只认 allowed（B7-TEST-001 暴露：曾误放行 blocked）
    bad = [t for t in tools if t not in allowed]
    if bad:
        raise ApiError(Err.VALIDATION_FAILED, f"以下平台 tool 未 allowed 标注，不可绑定：{', '.join(bad)}")


async def admin_detail(template_id: str) -> dict[str, Any]:
    """编辑器数据源：模板 + active 版本 + 当前草稿（如有）。"""
    tpl = await templates.get_template(template_id)
    if tpl is None:
        raise ApiError(Err.NOT_FOUND, "模板不存在")
    active = await templates.get_version(str(tpl["active_template_version_id"])) if tpl["active_template_version_id"] else None
    draft = await templates.get_draft(template_id)
    return {
        "template": row_json(tpl),
        "active_version": row_json(active) if active else None,
        "draft_version": row_json(draft) if draft else None,
    }


async def save_draft(template_id: str, content: dict[str, Any], by: str) -> dict[str, Any]:
    """保存草稿（另存新版本；草稿可反复改，发布后不可原地改）。"""
    if await templates.get_template(template_id) is None:
        raise ApiError(Err.NOT_FOUND, "模板不存在")
    await _validate_content(content)
    ver = await templates.save_draft(template_id, content, by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="template.version.saved", user_id=by,
        action="save_draft", payload_redacted={"template_id": template_id, "version_no": ver["version_no"]},
    )
    return row_json(ver)


async def publish(template_version_id: str, by: str) -> dict[str, Any]:
    """发布草稿为 active（不可变）；重校验内容（防草稿期后标注变化）。"""
    ver = await templates.get_version(template_version_id)
    if ver is None:
        raise ApiError(Err.NOT_FOUND, "模板版本不存在")
    if ver["status"] != "draft":
        raise ApiError(Err.CONFIG_VERSION_INVALID, "仅 draft 版本可发布（发布后的版本不可原地修改）")
    await _validate_content(ver["content_json"])
    out = await templates.publish_version(template_version_id, by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="template.version.published", user_id=by,
        action="publish", payload_redacted={"template_version_id": template_version_id, "version_no": out["version_no"]},
    )
    return row_json(out)


async def disable_version(template_version_id: str, by: str) -> None:
    ver = await templates.get_version(template_version_id)
    if ver is None:
        raise ApiError(Err.NOT_FOUND, "模板版本不存在")
    await templates.disable_version(template_version_id, by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="template.version.disabled", user_id=by,
        action="disable", payload_redacted={"template_version_id": template_version_id},
    )
