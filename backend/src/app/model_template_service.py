"""模型模板（主/子 Agent 槽位模型编排；管理台「模型模板」页 + 用户初始化「选一套模板」）。

- 模板 = 主 Agent 模型 + 子 Agent 模型两个槽位（全部子 Agent 共用 sub 槽位），引用 sre_model_asset。
- 简单可变实体（无草稿/发布版本化）：管理员改动对后续新 Run 生效——start_task 每次重查库，无缓存需失效。
- fail-closed：用户可见（list_available）/可绑（ensure_bindable）均要求主、子两槽位模型都在其授权
  集合内（B7 三闸门口径）；运行时第三闸门在 model_gateway.resolve_template_models（槽位失效
  回退平台默认 + model.template_degraded 留痕，fail-safe 不阻断任务）。
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
    槽位对象带 missing=True，admin 列表可标注而非 500。"""
    d = row_json(r)
    out = {k: d[k] for k in ("model_template_id", "display_name", "description",
                             "is_default", "status", "creation_date", "last_update_date")}
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
    )
    tid = str(row["model_template_id"])
    if req.is_default:
        await model_templates.set_default(tid, by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="model_template.created", user_id=by,
        action="create",
        payload_redacted={"model_template_id": tid, "name": req.display_name,
                          "is_default": bool(req.is_default)},
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


async def _authorized_asset_ids(user_id: str) -> set[str]:
    return {str(r["model_asset_id"]) for r in await model_assets.list_available_for_user(user_id)}


async def list_available(user: dict[str, Any]) -> list[dict[str, Any]]:
    """用户选单（InitWizard「选一套模型模板」数据源）：active 模板 ∩ 主、子槽位模型
    都在该用户授权集合内（fail-closed——restricted 槽位未授权则整套模板隐身）。"""
    avail = await _authorized_asset_ids(user["user_id"])
    return [_dto(r) for r in await model_templates.list_active()
            if str(r["main_model_asset_id"]) in avail and str(r["sub_model_asset_id"]) in avail]


async def ensure_bindable(user_id: str, model_template_id: str) -> None:
    """实例绑定服务端复校（防越权直调，同 _ensure_platform_model_authorized 口径）：
    模板存在 + active + 主/子槽位模型都对该用户授权。"""
    tpl = await model_templates.get(model_template_id)
    if tpl is None:
        raise ApiError(Err.NOT_FOUND, "模型模板不存在")
    if tpl["status"] != "active":
        raise ApiError(Err.TEMPLATE_DISABLED, "模型模板已停用，请重新选择")
    avail = await _authorized_asset_ids(user_id)
    if str(tpl["main_model_asset_id"]) not in avail or str(tpl["sub_model_asset_id"]) not in avail:
        raise ApiError(Err.MODEL_NOT_AUTHORIZED,
                       "模板内模型未对你授权（主/子槽位需全部授权），请联系管理员申请白名单")
