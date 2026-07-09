"""AgentTeam Instance：实例化 / 归属校验 / 配置版本 / 绑定 / 启停删。"""
from __future__ import annotations

from typing import Any

from domain.errors import ApiError, Err
from infra import idempotency
from infra.db import row_json
from infra.external import omodel_client
from infra.repositories import agent_teams, audit, templates
from runtime import task_registry

# 初始化 payload 白名单（INIT-004：非 V1 字段忽略）
_OVERLAY_ALLOWED = {"main_role_append", "user_llm_config_id"}


def _owner_check(row: dict[str, Any] | None, user_id: str) -> dict[str, Any]:
    if row is None:
        raise ApiError(Err.NOT_FOUND, "AgentTeam 实例不存在")
    if row["owner_user_id"] != user_id:
        raise ApiError(Err.FORBIDDEN, "无权访问该 AgentTeam")  # IAM-004
    return row


async def create(user: dict[str, Any], req: Any) -> dict[str, Any]:
    uid = user["user_id"]
    cached = idempotency.get(uid, "create_agent_team", req.client_request_id)
    if cached is not None:
        return cached  # INIT-006 幂等重放

    tv = await templates.get_version(req.template_version_id)
    if tv is None:
        raise ApiError(Err.NOT_FOUND, "模板版本不存在")
    tpl = await templates.get_template(str(tv["template_id"]))
    if tpl is None or tpl["status"] != "active":
        raise ApiError(Err.TEMPLATE_DISABLED, "模板不可用")

    ws = await omodel_client.get_workspace(req.workspace_id)
    if ws is None:
        raise ApiError(Err.NOT_FOUND, "workspace 不存在")
    if ws["sync_status"] != "ready":
        raise ApiError(Err.WORKSPACE_NOT_READY, "workspace 未就绪，暂不能激活")  # INIT-003

    if await agent_teams.name_exists(uid, req.name):
        raise ApiError(Err.VALIDATION_FAILED, "同名实例已存在")

    overlay = {k: v for k, v in (req.initial_overlay_json or {}).items() if k in _OVERLAY_ALLOWED}
    inst = await agent_teams.create_instance(
        uid, str(tv["template_id"]), req.template_version_id, req.name,
        req.workspace_id, req.scope_revision or ws["scope_revision"], overlay,
    )
    await audit.insert_event(
        audit_trace_id=str(inst["agent_team_instance_id"]), event_type="instance.created",
        user_id=uid, instance_id=str(inst["agent_team_instance_id"]),
        action="create", payload_redacted={"name": req.name, "workspace_id": req.workspace_id},
        actor_type="user",
    )
    result = {"instance": _dto(inst)}
    return idempotency.put(uid, "create_agent_team", req.client_request_id, result)


def _dto(row: dict[str, Any]) -> dict[str, Any]:
    d = row_json(row)
    d["instance_id"] = d.pop("agent_team_instance_id")
    return d


async def list_mine(user: dict[str, Any]) -> list[dict[str, Any]]:
    return [_dto(r) for r in await agent_teams.list_by_owner(user["user_id"])]


async def get(user: dict[str, Any], instance_id: str) -> dict[str, Any]:
    row = _owner_check(await agent_teams.get_instance(instance_id), user["user_id"])
    cfg = await agent_teams.get_config_version(str(row["active_config_version_id"]))
    return {"instance": _dto(row), "active_config_version": row_json(cfg) if cfg else None}


async def set_enabled(user: dict[str, Any], instance_id: str, enabled: bool) -> dict[str, Any]:
    row = _owner_check(await agent_teams.get_instance(instance_id), user["user_id"])
    if not enabled and _has_running_task(instance_id):
        raise ApiError(Err.INSTANCE_BUSY, "仍有任务运行中，请先取消任务")
    await agent_teams.set_status(instance_id, "active" if enabled else "disabled", user["user_id"])
    return {"instance_id": instance_id, "status": "active" if enabled else "disabled"}


async def delete(user: dict[str, Any], instance_id: str) -> None:
    _owner_check(await agent_teams.get_instance(instance_id), user["user_id"])
    if _has_running_task(instance_id):
        raise ApiError(Err.INSTANCE_BUSY, "仍有任务运行中，请先取消任务")
    await agent_teams.soft_delete(instance_id, user["user_id"])


def _has_running_task(instance_id: str) -> bool:
    return task_registry.instance_has_running(instance_id)


async def save_config(user: dict[str, Any], instance_id: str, req: Any) -> dict[str, Any]:
    row = _owner_check(await agent_teams.get_instance(instance_id), user["user_id"])
    if req.base_config_version_id and req.base_config_version_id != str(row["active_config_version_id"]):
        raise ApiError(Err.CONFIG_VERSION_INVALID, "配置已被更新，请刷新后重试")
    overlay = {k: v for k, v in (req.overlay_json or {}).items() if k in _OVERLAY_ALLOWED}
    cfg = await agent_teams.create_config_version(
        instance_id, str(row["template_version_id"]), overlay, user["user_id"], req.change_reason or "update"
    )
    return {"config_version": row_json(cfg)}


async def list_configs(user: dict[str, Any], instance_id: str) -> list[dict[str, Any]]:
    _owner_check(await agent_teams.get_instance(instance_id), user["user_id"])
    return [row_json(c) for c in await agent_teams.list_config_versions(instance_id)]
