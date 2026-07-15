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
    cached = await idempotency.begin(uid, "create_agent_team", req.client_request_id,
                                     req.model_dump(exclude={"client_request_id"}))
    if cached is not None:
        return cached  # INIT-006 幂等重放
    try:
        return await _create_body(user, req)
    except Exception:
        await idempotency.rollback(uid, "create_agent_team", req.client_request_id)
        raise


async def _create_body(user: dict[str, Any], req: Any) -> dict[str, Any]:
    uid = user["user_id"]
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
    return await idempotency.commit(uid, "create_agent_team", req.client_request_id, result)


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


async def update(user: dict[str, Any], instance_id: str, req: Any) -> dict[str, Any]:
    """编辑实例（编辑向导）：改名 / 换 workspace / 换模型；模板不可换。"""
    uid = user["user_id"]
    # hash 载荷带 path 上的 instance_id：同 crid 打不同实例 → IDEMPOTENCY_KEY_CONFLICT 而非误重放
    payload = {"instance_id": instance_id, **req.model_dump(exclude={"client_request_id"})}
    cached = await idempotency.begin(uid, "update_agent_team", req.client_request_id, payload)
    if cached is not None:
        return cached
    try:
        return await _update_body(user, instance_id, req)
    except Exception:
        await idempotency.rollback(uid, "update_agent_team", req.client_request_id)
        raise


async def _update_body(user: dict[str, Any], instance_id: str, req: Any) -> dict[str, Any]:
    uid = user["user_id"]
    row = _owner_check(await agent_teams.get_instance(instance_id), uid)
    rename = req.name != row["instance_name"]
    ws_change = req.workspace_id != row["workspace_id"]

    # 先全量校验后落库，避免半更新（如改名已生效、换 workspace 才失败）
    if rename and await agent_teams.name_exists(uid, req.name, exclude_instance_id=instance_id):
        raise ApiError(Err.VALIDATION_FAILED, "同名实例已存在")
    ws = None
    if ws_change:
        # 换范围 = 换安全边界，与 disable/delete 同守卫；改名/换模型不拦（模型在任务启动时装配）
        if _has_running_task(instance_id):
            raise ApiError(Err.INSTANCE_BUSY, "仍有任务运行中，请先取消任务")
        ws = await omodel_client.get_workspace(req.workspace_id)
        if ws is None:
            raise ApiError(Err.NOT_FOUND, "workspace 不存在")
        if ws["sync_status"] != "ready":
            raise ApiError(Err.WORKSPACE_NOT_READY, "workspace 未就绪，暂不能激活")  # INIT-003 口径

    # 模型 overlay：save_config 是整体替换，这里必须读旧值合并、只动 user_llm_config_id（保 main_role_append）
    prev = await agent_teams.get_config_version(str(row["active_config_version_id"]))
    prev_overlay = dict((prev or {}).get("overlay_json") or {})
    new_overlay = {k: v for k, v in prev_overlay.items() if k != "user_llm_config_id"}
    if req.user_llm_config_id:
        new_overlay["user_llm_config_id"] = req.user_llm_config_id

    changed: dict[str, Any] = {}
    if rename:
        await agent_teams.set_name(instance_id, req.name, uid)
        changed["name"] = req.name
    if ws_change and ws is not None:
        await agent_teams.update_workspace(instance_id, req.workspace_id, ws["scope_revision"], uid)
        changed["workspace_id"] = req.workspace_id
        changed["scope_revision"] = ws["scope_revision"]
    if new_overlay != prev_overlay:  # 纯改名/换范围不涨配置版本
        await derive_config_version(row, uid, "edit: 模型变更", overlay=new_overlay)
        changed["user_llm_config_id"] = req.user_llm_config_id
    if changed:  # payload 只含变化字段的纯标识符（SEC-001）
        await audit.insert_event(
            audit_trace_id=instance_id, event_type="instance.updated",
            user_id=uid, instance_id=instance_id,
            action="update", payload_redacted=changed, actor_type="user",
        )
    fresh = _owner_check(await agent_teams.get_instance(instance_id), uid)
    result = {"instance": _dto(fresh)}
    return await idempotency.commit(uid, "update_agent_team", req.client_request_id, result)


async def available_skills(user: dict[str, Any], instance_id: str) -> list[dict[str, Any]]:
    """composer「/」列表数据源：与执行门禁**同源**（run_state_service.resolve_available_skills
    再过 filter_main_skills 模板白名单，平台 active ∪ 本实例绑定的用户 Skill）——保证前端展示的
    即后端可执行的，键=skill_key。"""
    from app.run_state_service import filter_main_skills, resolve_available_skills

    row = _owner_check(await agent_teams.get_instance(instance_id), user["user_id"])
    sk = await resolve_available_skills(user["user_id"], str(row["active_config_version_id"]))
    tpl_ver = await templates.get_version(str(row["template_version_id"]))
    sk = filter_main_skills(sk, (tpl_ver or {}).get("content_json") or {})
    return [{"skill_key": k, "display_name": v.get("display_name") or k, "source_type": v.get("source_type")}
            for k, v in sk.items()]


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


async def derive_config_version(
    inst: dict[str, Any], by: str, reason: str, *,
    overlay: dict[str, Any] | None = None,
    add_binding: dict[str, Any] | None = None,
    drop_binding_id: str | None = None,
    template_version_id: str | None = None,
) -> dict[str, Any]:
    """派生新配置版本（不可变链，28.7）：沿用/替换 overlay + **结转上一版绑定**（±增删）。

    历史版本的绑定行不做任何原地改写（unbind = 新版本少一行，旧版本行保留供审计回放）。
    save_config / bind / unbind / 模板升级派生统一走本函数（升级时传新 template_version_id）。
    """
    instance_id = str(inst["agent_team_instance_id"])
    active_cv = str(inst["active_config_version_id"])
    prev = await agent_teams.get_config_version(active_cv)
    new_overlay = overlay if overlay is not None else ((prev or {}).get("overlay_json") or {})
    cfg = await agent_teams.create_config_version(
        instance_id, template_version_id or str(inst["template_version_id"]), new_overlay, by, reason
    )
    new_cv = str(cfg["config_version_id"])
    for b in await agent_teams.list_bindings_incl_muted(active_cv):  # 结转（含 muted 标记，可丢弃指定一条）
        if drop_binding_id and str(b["binding_id"]) == drop_binding_id:
            continue
        await agent_teams.create_binding(
            instance_id, new_cv, by, b["asset_type"],
            str(b["skill_id"]) if b["skill_id"] else None, str(b["skill_version_id"]) if b["skill_version_id"] else None,
            str(b["mcp_id"]) if b["mcp_id"] else None, str(b["mcp_version_id"]) if b["mcp_version_id"] else None,
            status=b["status"],  # 保留 muted（个人 skill 解绑）——否则派生冲掉解绑
        )
    binding = None
    if add_binding is not None:
        binding = await agent_teams.create_binding(
            instance_id, new_cv, by, add_binding["asset_type"],
            add_binding.get("skill_id"), add_binding.get("skill_version_id"),
            add_binding.get("mcp_id"), add_binding.get("mcp_version_id"),
            status=add_binding.get("status", "active"),
        )
    return {"config_version": cfg, "binding": binding}


async def save_config(user: dict[str, Any], instance_id: str, req: Any) -> dict[str, Any]:
    row = _owner_check(await agent_teams.get_instance(instance_id), user["user_id"])
    if req.base_config_version_id and req.base_config_version_id != str(row["active_config_version_id"]):
        raise ApiError(Err.CONFIG_VERSION_INVALID, "配置已被更新，请刷新后重试")
    overlay = {k: v for k, v in (req.overlay_json or {}).items() if k in _OVERLAY_ALLOWED}
    out = await derive_config_version(row, user["user_id"], req.change_reason or "update", overlay=overlay)
    return {"config_version": row_json(out["config_version"])}


async def list_configs(user: dict[str, Any], instance_id: str) -> list[dict[str, Any]]:
    _owner_check(await agent_teams.get_instance(instance_id), user["user_id"])
    return [row_json(c) for c in await agent_teams.list_config_versions(instance_id)]
