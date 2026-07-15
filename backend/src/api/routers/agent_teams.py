from __future__ import annotations

from fastapi import APIRouter

from api.deps import User
from api.responses import ok
from app import agent_team_service, asset_registry_service
from domain.schemas import AssetBindingRequest, CreateAgentTeamRequest, SaveConfigRequest, UpdateAgentTeamRequest

router = APIRouter(prefix="/api/openops/v1", tags=["agent-teams"])


@router.get("/agent-teams")
async def list_mine(user: User):
    return ok(await agent_team_service.list_mine(user))


@router.post("/agent-teams")
async def create(req: CreateAgentTeamRequest, user: User):
    return ok(await agent_team_service.create(user, req))


@router.get("/agent-teams/{instance_id}")
async def get(instance_id: str, user: User):
    return ok(await agent_team_service.get(user, instance_id))


@router.post("/agent-teams/{instance_id}:update")
async def update(instance_id: str, req: UpdateAgentTeamRequest, user: User):
    """编辑实例：改名 / 换 workspace（刷新 scope 快照）/ 换模型（派生新配置版本）。"""
    return ok(await agent_team_service.update(user, instance_id, req))


@router.post("/agent-teams/{instance_id}:disable")
async def disable(instance_id: str, user: User):
    return ok(await agent_team_service.set_enabled(user, instance_id, False))


@router.post("/agent-teams/{instance_id}:enable")
async def enable(instance_id: str, user: User):
    return ok(await agent_team_service.set_enabled(user, instance_id, True))


@router.delete("/agent-teams/{instance_id}")
async def delete(instance_id: str, user: User):
    await agent_team_service.delete(user, instance_id)
    return ok({"deleted": True})


@router.get("/agent-teams/{instance_id}/available-skills")
async def available_skills(instance_id: str, user: User):
    """composer「/」列表：与执行门禁同源的可执行 Skill 装配集（平台 active ∪ 绑定的用户 Skill）。"""
    return ok(await agent_team_service.available_skills(user, instance_id))


@router.get("/agent-teams/{instance_id}/config-versions")
async def list_configs(instance_id: str, user: User):
    return ok(await agent_team_service.list_configs(user, instance_id))


@router.post("/agent-teams/{instance_id}/config-versions")
async def save_config(instance_id: str, req: SaveConfigRequest, user: User):
    return ok(await agent_team_service.save_config(user, instance_id, req))


@router.post("/agent-teams/{instance_id}/asset-bindings")
async def bind(instance_id: str, req: AssetBindingRequest, user: User):
    return ok(await asset_registry_service.bind(user, instance_id, req))


@router.get("/agent-teams/{instance_id}/asset-bindings")
async def list_bindings(instance_id: str, user: User):
    return ok(await asset_registry_service.list_instance_bindings(user, instance_id))


@router.post("/agent-teams/{instance_id}/skill-mutes")
async def mute_skill(instance_id: str, req: AssetBindingRequest, user: User):
    """个人 skill「解绑」（默认自动挂载的 opt-out）；重新绑定走 DELETE /asset-bindings/{mute_id}。"""
    return ok(await asset_registry_service.mute_skill(user, instance_id, req))


@router.delete("/asset-bindings/{binding_id}")
async def unbind(binding_id: str, user: User):
    await asset_registry_service.unbind(user, binding_id)
    return ok({"deleted": True})
