"""Asset Registry：用户 Skill / HTTP MCP 资产 + 实例绑定（30.5 语义）。"""
from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from typing import Any

from app import agent_team_service
from domain.errors import ApiError, Err
from infra.db import row_json
from infra.repositories import agent_teams, assets, audit, templates

log = logging.getLogger("openops.user_skill_sync")

# 个人 skill「按用户」同步：SkillHub 已按 cookie 把个人 skill 限定为调用者本人，故列表时用 viewer
# cookie 拉到的 source_type='user' 即他自己的 → 归属 viewer 的 user_id（过读过滤、可绑定）。
# 全局 reconcile 只收平台 skill（见 asset_reconcile_service）。per-user 节流避免每次 GET 都打 SkillHub。
_USER_SKILL_TTL_S = float(os.environ.get("OPENOPS_USER_SKILL_TTL_S", os.environ.get("OPENOPS_RECONCILE_TTL_S", "300")))
_user_skill_synced: dict[str, float] = {}  # user_id -> 上次同步的 monotonic 时刻


def _reset_user_skill_sync() -> None:  # 测试隔离（对齐 asset_reconcile_service._reset）
    _user_skill_synced.clear()


def invalidate_user_skill_sync(user_id: str) -> None:
    """使某用户的同步节流失效：下次 list_skills 强制重拉（手动「同步资产」用）。"""
    _user_skill_synced.pop(user_id, None)


async def sync_user_skills(user: dict[str, Any]) -> None:
    """请求内按 viewer 同步个人 skill：用 viewer cookie 拉 SkillHub，把 source_type='user' 的 skill
    upsert 进本地表、归属 viewer 的 user_id。per-user 节流；失败只记日志不阻断列表读（读本地兜底）。"""
    uid = user["user_id"]
    now = time.monotonic()
    if now - _user_skill_synced.get(uid, 0.0) < _USER_SKILL_TTL_S:
        return
    _user_skill_synced[uid] = now  # await 前乐观写入：并发同用户塌缩为一次
    try:
        from infra.external import skill_hub_client

        for s in await skill_hub_client.list_skills(uid):  # viewer cookie 经 console_cookie 请求上下文透传
            if s.get("source") != "openops" or s.get("source_type") != "user":
                continue  # 只收个人 skill；平台 skill 走全局 reconcile
            manifest = {"synced_from": "skill_hub_user", "latest_version": s.get("latest_version"),
                        "category": s.get("category")}
            existing = await assets.get_user_skill_by_key(uid, s["skill_key"])
            if existing is None:
                await assets.create_skill(uid, "user", s["display_name"], s["skill_key"],
                                          manifest, s["checksum_sha256"])
                continue
            latest = await assets.latest_skill_version(str(existing["skill_id"]))
            if latest is None or latest["checksum_sha256"] != s["checksum_sha256"]:
                await assets.add_skill_version(
                    str(existing["skill_id"]), (latest["version_no"] if latest else 0) + 1,
                    manifest, s["checksum_sha256"], uid)
            else:
                cur = latest.get("manifest_json") or {}
                if cur.get("latest_version") != s.get("latest_version") or cur.get("category") != s.get("category"):
                    await assets.update_skill_version_manifest(
                        str(latest["skill_version_id"]),
                        {**cur, "latest_version": s.get("latest_version"), "category": s.get("category")})
    except Exception as e:  # noqa: BLE001 —— SkillHub 挂/cookie 失效不阻断列表读
        log.warning("user skill sync failed (%s): %s", uid, str(e)[:200])


async def list_skills(user: dict[str, Any]) -> list[dict[str, Any]]:
    await sync_user_skills(user)  # 读本地前先按 viewer 同步个人 skill（节流内为 no-op）
    out: list[dict[str, Any]] = []
    for r in await assets.list_skills(user["user_id"]):
        d = row_json(r)
        # §2.2 semver 与分类存在最新版本的 manifest_json 里（reconcile/upload 落库）；抽到行顶层供 UI 展示
        manifest = d.pop("manifest_json", None) or {}  # 抽完即弃，不把内部 manifest 透给前端
        if isinstance(manifest, dict):
            d["latest_version"] = manifest.get("latest_version")  # None → 前端回退 v{version_no}
            d["category"] = manifest.get("category")
        out.append(d)
    return out


async def upload_skill(user: dict[str, Any], req: Any) -> dict[str, Any]:
    checksum = req.checksum_sha256 or hashlib.sha256(req.display_name.encode()).hexdigest()
    key = req.display_name.strip().lower().replace(" ", "-")
    return await assets.create_skill(user["user_id"], "user", req.display_name, key, req.manifest_json, checksum)


async def upload_skill_package(user: dict[str, Any], filename: str, zip_bytes: bytes,
                               category: str, tags: list[str]) -> dict[str, Any]:
    """上传 Skill ZIP（29.3 §2.1）：转发 SkillHub（real 真传 / mock 合成）+ 写本地目录即时可见。
    本地按 skill_key 幂等（已存在→加版本）；reconcile 后续按 skill_key 收敛，避免重复行。"""
    from infra.external import skill_hub_client

    try:
        meta = skill_hub_client.parse_skill_meta(zip_bytes)
    except ValueError as e:
        raise ApiError(Err.SKILL_PACKAGE_INVALID, str(e)) from None
    checksum = hashlib.sha256(zip_bytes).hexdigest()  # ZIP 原始字节 sha256（真 checksum，非名称假造）
    try:
        result = await skill_hub_client.upload_skill(
            filename, zip_bytes, category, tags, source="openops", is_system=False)
    except RuntimeError as e:
        raise ApiError(Err.IAM_UPSTREAM, f"SkillHub 上传失败：{e}") from None

    skill_key = meta["skill_key"]
    manifest = {"entrypoint": meta.get("entrypoint") or "python3 run.py",
                "category": category or None, "tags": tags, "synced_from": "upload",  # 分类/标签可空（上传流程已移除该输入）→ 列表回退「—」
                "latest_version": result.get("version")}  # §2.1 上传响应 version → 上传后即刻可展示 semver
    existing = await assets.get_user_skill_by_key(user["user_id"], skill_key)  # owner 作用域：同 (owner, key) 才认作同一 skill
    if existing is None:
        row = await assets.create_skill(user["user_id"], "user", meta["name"], skill_key, manifest, checksum)
        action = "created"
    else:
        latest = await assets.latest_skill_version(str(existing["skill_id"]))
        next_no = int(latest["version_no"]) + 1 if latest else 1
        vid = await assets.add_skill_version(str(existing["skill_id"]), next_no, manifest, checksum, user["user_id"])
        row = {"skill_id": str(existing["skill_id"]), "skill_version_id": vid}
        action = "version_updated"
    # action 以本地目录实际动作为准（UI 反映的就是本地行）；SkillHub 侧动作另附参考
    return {**row, "skill_key": skill_key, "display_name": meta["name"],
            "action": action, "skillhub_action": result.get("action")}


async def delete_skill(user: dict[str, Any], skill_id: str) -> None:
    row = await assets.get_skill(skill_id)
    if row is None:
        raise ApiError(Err.NOT_FOUND, "Skill 不存在")
    if row["source_type"] == "user" and row["owner_user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权删除该 Skill")
    if await agent_teams.asset_in_use("skill", skill_id):
        raise ApiError(Err.ASSET_IN_USE, "该资产仍被 active 配置引用，请先解绑")  # CFG-006
    await assets.delete_skill(skill_id, user["user_id"])


async def list_mcps(user: dict[str, Any]) -> list[dict[str, Any]]:
    rows = await assets.list_mcps(user["user_id"])
    out = []
    for r in rows:
        d = row_json(r)
        # endpoint 脱敏（30.5：展示时必须脱敏）
        cfg = d.get("endpoint_config_json") or {}
        if isinstance(cfg, dict) and cfg.get("endpoint"):
            cfg = {**cfg, "endpoint": str(cfg["endpoint"])[:12] + "…"}
        d["endpoint_config_redacted"] = cfg
        d.pop("endpoint_config_json", None)
        out.append(d)
    return out


async def register_mcp(user: dict[str, Any], req: Any) -> dict[str, Any]:
    if req.transport != "http":
        raise ApiError(Err.VALIDATION_FAILED, "V1 仅支持 HTTP MCP")  # MCP-007
    return await assets.create_mcp(
        user["user_id"], "user", req.display_name, "http", {"endpoint": req.endpoint}, req.manifest_json
    )


async def delete_mcp(user: dict[str, Any], mcp_id: str) -> None:
    row = await assets.get_mcp(mcp_id)
    if row is None:
        raise ApiError(Err.NOT_FOUND, "MCP 不存在")
    if row["source_type"] == "user" and row["owner_user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权删除该 MCP")
    if await agent_teams.asset_in_use("mcp", mcp_id):
        raise ApiError(Err.ASSET_IN_USE, "该资产仍被 active 配置引用，请先解绑")
    tool_names = await assets.tool_names_for_mcp(mcp_id)  # 删前取工具名（catalog 随 asset 保留）
    await assets.delete_mcp(mcp_id, user["user_id"])
    # 级联清理：把该 server 的工具从模板 draft 绑定里摘掉，防成幽灵绑定卡编辑（published 不可变，编辑时自愈）
    scrubbed = await templates.scrub_tools_from_versions(tool_names)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="mcp.deleted", user_id=user["user_id"], action="delete",
        payload_redacted={"mcp_id": mcp_id, "tool_names": tool_names, "templates_scrubbed": scrubbed},
    )


async def bind(user: dict[str, Any], instance_id: str, req: Any) -> dict[str, Any]:
    inst = await agent_teams.get_instance(instance_id)
    if inst is None or inst["owner_user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权操作该 AgentTeam")
    # 绑定=派生新配置版本（不可变链：沿用 overlay + 结转已有绑定 + 加新绑定；绑定目标固定 main）
    out = await agent_team_service.derive_config_version(
        inst, user["user_id"], f"bind {req.asset_type}",
        add_binding={"asset_type": req.asset_type, "skill_id": req.skill_id, "skill_version_id": req.skill_version_id,
                     "mcp_id": req.mcp_id, "mcp_version_id": req.mcp_version_id},
    )
    return {"binding_id": out["binding"]["binding_id"],
            "config_version_id": str(out["config_version"]["config_version_id"])}


async def list_instance_bindings(user: dict[str, Any], instance_id: str) -> list[dict[str, Any]]:
    inst = await agent_teams.get_instance(instance_id)
    if inst is None or inst["owner_user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权查看该 AgentTeam 绑定")
    rows = await agent_teams.list_binding_details(str(inst["active_config_version_id"]))
    out: list[dict[str, Any]] = []
    for r in rows:
        d = row_json(r)
        if d["asset_type"] == "skill":
            d["display_name"] = d.get("skill_display_name") or "Skill"
            d["version_no"] = d.get("skill_version_no") or 1
            d["asset_status"] = d.get("skill_status") or "unknown"
            d["source_type"] = d.get("skill_source_type") or "user"
        else:
            d["display_name"] = d.get("mcp_display_name") or "HTTP MCP"
            d["version_no"] = d.get("mcp_version_no") or 1
            d["asset_status"] = d.get("mcp_status") or "unknown"
            d["source_type"] = d.get("mcp_source_type") or "user"
        out.append(d)
    return out


async def unbind(user: dict[str, Any], binding_id: str) -> dict[str, Any]:
    """解绑=派生新版本（少这一条绑定）；历史版本的绑定行不原地改写（28.7 不可变链）。"""
    b = await agent_teams.get_binding(binding_id)
    if b is None:
        raise ApiError(Err.NOT_FOUND, "绑定不存在")
    inst = await agent_teams.get_instance(str(b["agent_team_instance_id"]))
    if inst is None or inst["owner_user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权操作该绑定")
    if str(b["config_version_id"]) != str(inst["active_config_version_id"]):
        raise ApiError(Err.CONFIG_VERSION_INVALID, "绑定不在当前 active 配置上，请刷新后重试")
    out = await agent_team_service.derive_config_version(
        inst, user["user_id"], "unbind", drop_binding_id=binding_id,
    )
    return {"config_version_id": str(out["config_version"]["config_version_id"])}
