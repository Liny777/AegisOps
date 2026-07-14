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
    # main 直连技能白名单（编排对称化）：类型校验同 sub.skills；skills 无标注体系不做门禁
    # （执行另有装配校验）。语义：空/缺省=不限（存量模板兼容），非空=白名单交集。
    ms = main.get("skills", [])
    if not isinstance(ms, list) or not all(isinstance(x, str) for x in ms):
        raise ApiError(Err.VALIDATION_FAILED, "main.skills 必须是字符串数组")
    subs = content.get("sub_agents", [])
    if not isinstance(subs, list):
        raise ApiError(Err.VALIDATION_FAILED, "sub_agents 必须是数组")
    # D 块：sub 画像校验（key/role 必填；skills/mcp_tools 字符串数组；max_iters 1..200；key 唯一）
    seen_keys: set[str] = set()
    for s in subs:
        if not isinstance(s, dict) or not str(s.get("key", "")).strip() or not str(s.get("role", "")).strip():
            raise ApiError(Err.VALIDATION_FAILED, "sub_agents 每项须含 key 与 role")
        if s["key"] in seen_keys:
            raise ApiError(Err.VALIDATION_FAILED, f"sub_agents key 重复：{s['key']}")
        seen_keys.add(s["key"])
        for f in ("skills", "mcp_tools"):
            if not isinstance(s.get(f, []), list) or not all(isinstance(x, str) for x in s.get(f, [])):
                raise ApiError(Err.VALIDATION_FAILED, f"sub_agents[{s['key']}].{f} 必须是字符串数组")
        mi = s.get("max_iters", 20)
        if not isinstance(mi, int) or not (1 <= mi <= 200):
            raise ApiError(Err.VALIDATION_FAILED, f"sub_agents[{s['key']}].max_iters 须为 1..200 整数")
        trl = s.get("tool_result_limit", 24000)
        if not isinstance(trl, int) or not (1000 <= trl <= 200000):  # E4：须 < 模型窗口（经验 ≤1/3）
            raise ApiError(Err.VALIDATION_FAILED, f"sub_agents[{s['key']}].tool_result_limit 须为 1000..200000 整数")
    for f, lo, hi in (("max_children", 1, 10), ("delegation_max_spawns", 1, 100)):
        v = main.get(f)
        if v is not None and (not isinstance(v, int) or not (lo <= v <= hi)):
            raise ApiError(Err.VALIDATION_FAILED, f"main.{f} 须为 {lo}..{hi} 整数")
    anns = await mcp_tool_annotation_service.runtime_annotations()  # 全部已标注工具（含 blocked，运行时另拦）
    allowed = {k for k, v in anns.items() if v.get("status") == "allowed"}  # 绑定校验只认 allowed（B7-TEST-001 暴露：曾误放行 blocked）
    sub_tools = [t for s in subs for t in s.get("mcp_tools", [])]
    bad = [t for t in [*tools, *sub_tools] if t not in allowed]
    if bad:
        raise ApiError(Err.VALIDATION_FAILED, f"以下平台 tool 未 allowed 标注，不可绑定：{', '.join(sorted(set(bad)))}")


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
