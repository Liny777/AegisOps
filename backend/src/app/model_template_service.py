"""模型模板（主/子 Agent 槽位模型编排；管理台「模型模板」页 + 用户初始化「选一套模板」）。

- 模板 = 主 Agent 模型 + 子 Agent 模型两个槽位（全部子 Agent 共用 sub 槽位），引用 sre_model_asset。
- 简单可变实体（无草稿/发布版本化）：管理员改动对后续新 Run 生效——start_task 每次重查库，无缓存需失效。
- 授权在模板维度（38.1：access_scope all/restricted + sre_model_template_grant 按人白名单，
  接替已废弃的资产级授权）。fail-closed 三闸门：用户可见（list_available 的 SQL 过滤）、
  可绑（ensure_bindable）、运行时第三闸门（model_gateway.resolve_template_models——
  绑定后撤销授权/模板失效 → 回退平台默认 + model.template_degraded 留痕，fail-safe 不阻断任务）。
  不再看槽位资产授权（资产路径已放开，槽位只看资产存活）。
- 幂等对齐 model_asset_service 惯例：DTO 收 client_request_id 不接 idempotency，
  重名靠 ux_model_template_name 唯一索引 + 服务层重名检查兜底。
"""
from __future__ import annotations

import uuid
from typing import Any

from domain.errors import ApiError, Err
from infra.db import row_json
from infra.repositories import audit, model_assets, model_templates


def _dto(r: dict[str, Any]) -> dict[str, Any]:
    """join 行折叠：main_model / sub_model 嵌套对象。资产已软删（left join 悬挂）时
    槽位对象带 missing=True，admin 列表可标注而非 500。
    grant_count 只在 admin 行源（list_all）存在——条件注入，用户侧 payload 不漏授权人数。"""
    d = row_json(r)
    out = {k: d[k] for k in ("model_template_id", "display_name", "description",
                             "access_scope", "is_default", "status", "creation_date", "last_update_date")}
    if "grant_count" in d:
        out["grant_count"] = d["grant_count"]
    for slot in ("main", "sub"):
        asset_id = d.get(f"{slot}_model_asset_id")
        model_id = d.get(f"{slot}_model_id")
        out[f"{slot}_model"] = {
            "model_asset_id": asset_id,
            "model_id": model_id,
            "display_name": d.get(f"{slot}_model_name"),
            "status": d.get(f"{slot}_model_status"),
            **({"missing": True} if model_id is None else {}),
        }
    return out


async def _ensure_asset_usable(asset_id: str, slot: str) -> None:
    row = await model_assets.get(asset_id)
    if row is None:
        raise ApiError(Err.VALIDATION_FAILED, f"{slot}模型资产不存在或已删除")
    if row["status"] != "active":
        raise ApiError(Err.VALIDATION_FAILED, f"{slot}模型资产已禁用，不能编入模板")


async def admin_list() -> list[dict[str, Any]]:
    return [_dto(r) for r in await model_templates.list_all()]


async def create(req: Any, by: str) -> dict[str, Any]:
    if await model_templates.get_by_name(req.display_name):
        raise ApiError(Err.VALIDATION_FAILED, f"同名模型模板已存在：{req.display_name}")
    # 主子可为同一资产（同一模型跑双槽位是合法配置），逐槽校验存在 + active
    await _ensure_asset_usable(req.main_model_asset_id, "主 Agent ")
    await _ensure_asset_usable(req.sub_model_asset_id, "子 Agent ")
    row = await model_templates.create(
        req.display_name, req.description or None,
        req.main_model_asset_id, req.sub_model_asset_id, by,
        access_scope=req.access_scope,
    )
    tid = str(row["model_template_id"])
    if req.is_default:
        await model_templates.set_default(tid, by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="model_template.created", user_id=by,
        action="create",
        payload_redacted={"model_template_id": tid, "name": req.display_name,
                          "access_scope": req.access_scope, "is_default": bool(req.is_default)},
    )
    return _dto(await _joined_or_raise(tid))


async def _joined_or_raise(model_template_id: str) -> dict[str, Any]:
    for r in await model_templates.list_all():
        if str(r["model_template_id"]) == model_template_id:
            return r
    raise ApiError(Err.NOT_FOUND, "模型模板不存在")


async def update(model_template_id: str, req: Any, by: str) -> dict[str, Any]:
    """局部更新（PATCH：只改显式提供的键）。改槽位只影响后续新 Run
    （同 model_asset_service.update 口径）；已在跑的 Run 沿用 TaskState 已解析的 spec 到结束。"""
    if await model_templates.get(model_template_id) is None:
        raise ApiError(Err.NOT_FOUND, "模型模板不存在")
    fields = req.model_dump(exclude_unset=True)
    fields.pop("client_request_id", None)
    if not fields:
        raise ApiError(Err.VALIDATION_FAILED, "未提供任何可更新字段")
    if "display_name" in fields:
        name = (fields["display_name"] or "").strip()
        if not name:
            raise ApiError(Err.VALIDATION_FAILED, "display_name 不可置空")
        dup = await model_templates.get_by_name(name)
        if dup and str(dup["model_template_id"]) != model_template_id:
            raise ApiError(Err.VALIDATION_FAILED, f"同名模型模板已存在：{name}")
        fields["display_name"] = name
    for key, slot in (("main_model_asset_id", "主 Agent "), ("sub_model_asset_id", "子 Agent ")):
        if key in fields:
            if not fields[key]:
                raise ApiError(Err.VALIDATION_FAILED, f"{key} 不可置空")
            await _ensure_asset_usable(fields[key], slot)
    await model_templates.update_fields(model_template_id, fields, by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="model_template.updated", user_id=by,
        action="update",
        payload_redacted={"model_template_id": model_template_id, "fields": sorted(fields)},
    )
    return _dto(await _joined_or_raise(model_template_id))


async def set_status(model_template_id: str, status: str, by: str) -> None:
    """disabled 不清 is_default：用户列表按 status='active' 过滤，默认行隐身即视同无默认
    （前端选单回退首行）；重新启用后默认标记原样恢复。"""
    n = await model_templates.set_status(model_template_id, status, by)
    if n == 0:
        raise ApiError(Err.NOT_FOUND, "模型模板不存在")
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="model_template.status_changed", user_id=by,
        action=status, payload_redacted={"model_template_id": model_template_id},
    )


async def delete(model_template_id: str, by: str) -> None:
    """软删模板（38.2）。允许删默认模板（is_default 只影响选单预选，删后前端回退首个 active）；
    已绑实例不阻断——下次 start_task 走 TEMPLATE_UNAVAILABLE 降级回平台默认 + 留痕（既有链路）。"""
    n = await model_templates.soft_delete(model_template_id, by)
    if n == 0:
        raise ApiError(Err.NOT_FOUND, "模型模板不存在")
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="model_template.deleted", user_id=by,
        action="delete", payload_redacted={"model_template_id": model_template_id},
    )


async def set_default(model_template_id: str, by: str) -> dict[str, Any]:
    tpl = await model_templates.get(model_template_id)
    if tpl is None:
        raise ApiError(Err.NOT_FOUND, "模型模板不存在")
    if tpl["status"] != "active":
        raise ApiError(Err.VALIDATION_FAILED, "已停用的模板不能设为默认，请先启用")
    await model_templates.set_default(model_template_id, by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="model_template.default_changed", user_id=by,
        action="set_default", payload_redacted={"model_template_id": model_template_id},
    )
    return {"model_template_id": model_template_id, "is_default": True}


async def list_available(user: dict[str, Any]) -> list[dict[str, Any]]:
    """用户选单（InitWizard「选一套模型模板」数据源）：active ∩（scope=all ∪ 有 active grant）
    ——38.1 模板维度 ACL 第一闸门，fail-closed；不再要求槽位资产授权（资产路径已放开）。"""
    return [_dto(r) for r in await model_templates.list_available_for_user(user["user_id"])]


async def ensure_bindable(user_id: str, model_template_id: str) -> None:
    """实例绑定服务端复校（第二闸门，防越权直调）：模板存在 + active +
    （scope=all 或该用户有 active grant）。"""
    tpl = await model_templates.get(model_template_id)
    if tpl is None:
        raise ApiError(Err.NOT_FOUND, "模型模板不存在")
    if tpl["status"] != "active":
        raise ApiError(Err.TEMPLATE_DISABLED, "模型模板已停用，请重新选择")
    if tpl["access_scope"] != "all" and not await model_templates.has_grant(model_template_id, user_id):
        raise ApiError(Err.MODEL_NOT_AUTHORIZED, "该模型模板未对你授权，请联系管理员申请白名单")


async def get_grants(model_template_id: str) -> dict[str, Any]:
    tpl = await model_templates.get(model_template_id)
    if tpl is None:
        raise ApiError(Err.NOT_FOUND, "模型模板不存在")
    return {
        "model_template_id": model_template_id,
        "access_scope": tpl["access_scope"],
        "user_ids": [g["user_id"] for g in await model_templates.list_grants(model_template_id)],
    }


async def save_grants(model_template_id: str, req: Any, by: str) -> dict[str, Any]:
    """保存授权：scope + 人员集合（软删+插新）；all 时忽略 user_ids。写审计（含人数不含逐人全文）。
    改动对后续新 Run 生效（第三闸门 resolve_template_models 每次重查）；已在跑任务沿用已解析 spec。"""
    tpl = await model_templates.get(model_template_id)
    if tpl is None:
        raise ApiError(Err.NOT_FOUND, "模型模板不存在")
    await model_templates.set_access_scope(model_template_id, req.access_scope, by)
    user_ids = [] if req.access_scope == "all" else list(req.user_ids)
    await model_templates.replace_grants(model_template_id, user_ids, by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="model_template.grants_updated", user_id=by,
        action="save_grants",
        payload_redacted={"model_template_id": model_template_id, "access_scope": req.access_scope,
                          "granted_count": len(user_ids)},
    )
    return await get_grants(model_template_id)
