"""Asset Registry：用户 Skill / HTTP MCP 资产 + 实例绑定（30.5 语义）。"""
from __future__ import annotations

import hashlib
from typing import Any

from app import agent_team_service
from domain.errors import ApiError, Err
from infra.db import row_json
from infra.repositories import agent_teams, assets


async def list_skills(user: dict[str, Any]) -> list[dict[str, Any]]:
    return [row_json(r) for r in await assets.list_skills(user["user_id"])]


async def upload_skill(user: dict[str, Any], req: Any) -> dict[str, Any]:
    checksum = req.checksum_sha256 or hashlib.sha256(req.display_name.encode()).hexdigest()
    key = req.display_name.strip().lower().replace(" ", "-")
    return await assets.create_skill(user["user_id"], "user", req.display_name, key, req.manifest_json, checksum)


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
    await assets.delete_mcp(mcp_id, user["user_id"])


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
