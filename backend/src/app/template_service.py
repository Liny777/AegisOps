"""Template Admin：读侧 + 版本写闭环（B7·二：草稿/发布/禁用，发布后不可变）。"""
from __future__ import annotations

import copy
import uuid
from typing import Any

from app import mcp_tool_annotation_service
from domain import tool_key
from domain.errors import ApiError, Err
from infra.db import row_json
from infra.repositories import audit, templates


def allowed_index(anns: dict[str, dict[str, Any]]) -> tuple[set[str], dict[str, list[str]]]:
    """runtime_annotations 视图 → allowed 工具的 (复合键集合, 裸名→复合键列表倒排)。
    裸名别名条目（与复合键条目同对象）不重复计入；run_state_service 装配侧共用同一判据。"""
    keys: set[str] = set()
    by_bare: dict[str, list[str]] = {}
    for k, v in anns.items():
        if v.get("status") != "allowed" or not tool_key.is_composite(k):
            continue
        keys.add(k)
        by_bare.setdefault(str(v.get("tool_name") or ""), []).append(k)
    return keys, by_bare


def _normalize_and_scrub(
    content: dict[str, Any], anns: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
    """content_json 工具引用归一化 + 自愈摘除（覆盖 main.default_tools 与每个 sub_agents[].mcp_tools）。

    逐条目：复合键且 allowed → 保留；裸名 → **展开**为全部 allowed 命中的复合键（唯一命中即
    「升级」，多家命中即全展开——裸名白名单本就同时放行各家同名工具，收窄会静默砍能力，
    展开只是把隐式全绑显式化）；解析不到（失效/幽灵/已删 server）→ 摘除。
    返回 (新 content, 被摘条目去重排序)。存量裸名模板「保存一次即迁移」为复合键，无需批量改库
    （published 不可变，读侧由 run_state_service.start_task 做同规则归一）。
    """
    content = copy.deepcopy(content)
    keys, by_bare = allowed_index(anns)
    dropped: list[str] = []

    def _norm(names: Any) -> list[str]:
        if not isinstance(names, list) or not all(isinstance(t, str) for t in names):
            return names  # 结构非法留给 _validate_content 报
        out: list[str] = []
        for t in names:
            hits = [t] if t in keys else (by_bare.get(t, []) if not tool_key.is_composite(t) else [])
            if not hits:
                dropped.append(t)
                continue
            out.extend(h for h in hits if h not in out)
        return out

    main = content.get("main")
    if isinstance(main, dict) and "default_tools" in main:
        main["default_tools"] = _norm(main["default_tools"])
    for s in content.get("sub_agents", []) or []:
        if isinstance(s, dict) and "mcp_tools" in s:
            s["mcp_tools"] = _norm(s["mcp_tools"])
    return content, sorted(set(dropped))


async def renormalize_drafts() -> int:
    """对全部 draft 重跑归一化（删 MCP server 的级联自愈入口）：被删 server 的引用（复合键失配、
    裸名解析不到）被摘除；能在幸存 server 解析到 allowed 的裸名保留并升级为幸存家的复合键。
    published 不可变、不动（下次编辑经 save_draft 自愈）。返回改动的版本数。"""
    anns = await mcp_tool_annotation_service.runtime_annotations()
    changed = 0
    for r in await templates.list_drafts():
        old = r.get("content_json") or {}
        new_c, _ = _normalize_and_scrub(old, anns)
        if new_c != old:
            await templates.update_version_content(str(r["template_version_id"]), new_c)
            changed += 1
    return changed


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
    activity_labels = content.get("activity_labels", {})
    if not isinstance(activity_labels, dict):
        raise ApiError(Err.VALIDATION_FAILED, "activity_labels 必须是对象")
    tool_labels = activity_labels.get("tools", {})
    if not isinstance(tool_labels, dict) or not all(
        isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()
        for k, v in tool_labels.items()
    ):
        raise ApiError(Err.VALIDATION_FAILED, "activity_labels.tools 必须是非空字符串映射")
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
    # 平台工具 allowed 门禁不在此硬报错：save_draft/publish 先经 _normalize_and_scrub 把解析不到 allowed
    # 的存量/失效绑定自动摘除（B7-TEST-001 曾误放行 blocked → 现统一在 scrub 剔除，安全意图不变），
    # 同时把裸工具名升级为「server::tool」复合键（同名跨 server 不再互踩/联动）。


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
    """保存草稿（另存新版本；草稿可反复改，发布后不可原地改）。

    自愈 + 迁移：先做工具引用归一化（裸名升级/展开为复合键）并摘掉解析不到 allowed 标注的
    平台工具（已删 MCP server / 失效存量绑定）——不再硬报错，存量卡住的模板保存一次即恢复
    且完成裸名→复合键迁移；被摘名字回传 dropped_tools 供前端提示。
    """
    if await templates.get_template(template_id) is None:
        raise ApiError(Err.NOT_FOUND, "模板不存在")
    content, dropped = _normalize_and_scrub(content, await mcp_tool_annotation_service.runtime_annotations())
    await _validate_content(content)
    ver = await templates.save_draft(template_id, content, by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="template.version.saved", user_id=by,
        action="save_draft",
        payload_redacted={"template_id": template_id, "version_no": ver["version_no"], "dropped_tools": dropped},
    )
    out = row_json(ver)
    out["dropped_tools"] = dropped
    return out


async def publish(template_version_id: str, by: str) -> dict[str, Any]:
    """发布草稿为 active（不可变）；发布前自愈摘除失效工具（防草稿期后标注变化/存量草稿），保证发布版干净。"""
    ver = await templates.get_version(template_version_id)
    if ver is None:
        raise ApiError(Err.NOT_FOUND, "模板版本不存在")
    if ver["status"] != "draft":
        raise ApiError(Err.CONFIG_VERSION_INVALID, "仅 draft 版本可发布（发布后的版本不可原地修改）")
    content, dropped = _normalize_and_scrub(ver["content_json"], await mcp_tool_annotation_service.runtime_annotations())
    await _validate_content(content)
    if dropped:  # 草稿里有失效绑定 → 发布前把草稿内容更新为 scrubbed，发布版不带幽灵工具
        await templates.update_version_content(template_version_id, content)
    out = await templates.publish_version(template_version_id, by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="template.version.published", user_id=by,
        action="publish",
        payload_redacted={"template_version_id": template_version_id, "version_no": out["version_no"], "dropped_tools": dropped},
    )
    res = row_json(out)
    res["dropped_tools"] = dropped
    return res


async def disable_version(template_version_id: str, by: str) -> None:
    ver = await templates.get_version(template_version_id)
    if ver is None:
        raise ApiError(Err.NOT_FOUND, "模板版本不存在")
    await templates.disable_version(template_version_id, by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="template.version.disabled", user_id=by,
        action="disable", payload_redacted={"template_version_id": template_version_id},
    )
