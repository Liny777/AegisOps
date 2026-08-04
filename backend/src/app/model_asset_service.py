"""模型资产（B7，30.6 五；38.1 起授权迁模型模板维度，本服务只管资产 CRUD/探测）。

- 授权见 model_template_service（模板 scope+grant 三闸门）；资产池对全员一致。
- 注册走 DTO 白名单字段（api_key/token 等敏感键天然进不来）；Key 只经 `secret_env_var` 环境变量名。
- is_authorized 退化为「存在 + active」（fail-closed 对未知/禁用仍 False），服务
  select-model 与 legacy platform_model_id 绑定两个旧路径。
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from domain.errors import ApiError, Err
from infra.db import row_json
from infra.repositories import audit, model_assets

# secret_env_var 是环境变量名（非 Key 本身）：大写惯例。实测有管理员把真实 Key 填进来 → 明文落库
# + 日志泄漏（SEC-001），入口处直接拒绝。
_ENV_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
# register 与 update 共用同一道闸、同一段文案：更新路径若绕过它，等于给「真实 Key 落库」开后门。
_ENV_VAR_HINT = ("「API Key 环境变量名」应填变量名（大写字母/数字/下划线，如 OPENOPS_PLATFORM_GLM_API_KEY）"
                 "——看起来填入了 API Key 本身：真实 Key 绝不落库，请把 Key 配到后端进程环境变量"
                 "（run-backend 里 export），此处只填变量名")


async def admin_list() -> list[dict[str, Any]]:
    return [row_json(r) for r in await model_assets.list_all()]


async def register(req: Any, by: str) -> dict[str, Any]:
    if await model_assets.get_by_model_id(req.model_id):
        raise ApiError(Err.VALIDATION_FAILED, f"model_id 已存在：{req.model_id}")
    if req.secret_env_var and not _ENV_VAR_RE.match(req.secret_env_var):
        raise ApiError(Err.VALIDATION_FAILED, _ENV_VAR_HINT)
    row = await model_assets.create(
        req.display_name, req.protocol, req.model_id, req.base_url,
        req.secret_env_var, "active", by,
        context_window_tokens=getattr(req, "context_window_tokens", 128000),
    )
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="model_asset.registered", user_id=by,
        action="register", payload_redacted={"model_id": req.model_id},
    )
    return row_json(row)


async def update(model_asset_id: str, req: Any, by: str) -> dict[str, Any]:
    """局部更新连接配置（PATCH：只改显式提供的键）。

    典型用途：模型的 base_url 指错环境（如生产库里填了测试地址），无改库条件时经本接口纠正。
    改完**新 run 立即生效**——[[model_gateway.resolve_runtime_model]] 每次 run 启动都重查库，
    无缓存需失效；已在跑的 run 沿用 TaskState 里已解析的 spec 到结束。
    """
    if await model_assets.get(model_asset_id) is None:
        raise ApiError(Err.NOT_FOUND, "模型资产不存在")
    fields = req.model_dump(exclude_unset=True)  # 未出现的键不进 SET，与「显式传 null」区分开
    fields.pop("client_request_id", None)
    if not fields:
        raise ApiError(Err.VALIDATION_FAILED, "未提供任何可更新字段")
    if fields.get("secret_env_var") and not _ENV_VAR_RE.match(fields["secret_env_var"]):
        raise ApiError(Err.VALIDATION_FAILED, _ENV_VAR_HINT)
    # 这两列 NOT NULL：显式传 null 会被 DB 拒（整条 500），入口处给出可读原因
    if "display_name" in fields and not (fields["display_name"] or "").strip():
        raise ApiError(Err.VALIDATION_FAILED, "display_name 不可置空")
    if "context_window_tokens" in fields and fields["context_window_tokens"] is None:
        raise ApiError(Err.VALIDATION_FAILED, "context_window_tokens 不可置空")
    await model_assets.update_fields(model_asset_id, fields, by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="model_asset.updated", user_id=by,
        action="update",
        # 只记改了哪些字段名 + 新 base_url；secret_env_var 虽是变量名非 Key，也不必进审计
        payload_redacted={"model_asset_id": model_asset_id, "fields": sorted(fields),
                          "base_url": fields.get("base_url")},
    )
    return row_json(await model_assets.get(model_asset_id))  # type: ignore[arg-type]


async def set_status(model_asset_id: str, status: str, by: str) -> None:
    n = await model_assets.set_status(model_asset_id, status, by)
    if n == 0:
        raise ApiError(Err.NOT_FOUND, "模型资产不存在")
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="model_asset.status_changed", user_id=by,
        action=status, payload_redacted={"model_asset_id": model_asset_id},
    )


async def delete(model_asset_id: str, by: str) -> None:
    """软删模型资产（38.2）。被未删模板槽位引用则拒删（fail-closed：先调整模板，避免管理台出悬挂槽位）；
    legacy overlay（platform_model_id）引用不拦——运行时 fail-safe 回平台默认。
    model_id 唯一索引带 WHERE deleted_at IS NULL，删后同 model_id 可重注册。"""
    from infra.repositories import model_templates  # 延迟导入：本服务默认不依赖模板仓储

    if await model_assets.get(model_asset_id) is None:
        raise ApiError(Err.NOT_FOUND, "模型资产不存在")
    refs = await model_templates.list_referencing_asset(model_asset_id)
    if refs:
        names = "、".join(str(r["display_name"]) for r in refs[:5])
        more = f" 等 {len(refs)} 个" if len(refs) > 5 else ""
        raise ApiError(Err.VALIDATION_FAILED,
                       f"该模型被模型模板引用（{names}{more}），请先在模板中更换主/子槽位或删除对应模板")
    await model_assets.soft_delete(model_asset_id, by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="model_asset.deleted", user_id=by,
        action="delete", payload_redacted={"model_asset_id": model_asset_id},
    )


def _default_model_id(rows: list[dict[str, Any]]) -> str | None:
    """与 [[model_gateway.resolve_runtime_model]] 完全同口径的平台默认解析：
    优先 `OPENOPS_RUNTIME_MODEL`（默认 glm-5.1），否则首个带 `secret_env_var` 的模型。
    默认**必须带 Key** 才成立——否则运行时回退 stub。避免把无 Key 的种子资产（如 Qwen3.5）
    当默认却根本跑不起来（前端初始化向导据此展示真实默认名，而非写死）。"""
    from app.model_gateway import DEFAULT_RUNTIME_MODEL

    by_id = {r.get("model_id"): r for r in rows}
    target = by_id.get(DEFAULT_RUNTIME_MODEL) or next((r for r in rows if r.get("secret_env_var")), None)
    if target is None or not target.get("secret_env_var"):
        return None
    return target.get("model_id")


async def list_available(user: dict[str, Any]) -> list[dict[str, Any]]:
    """用户可见平台模型（30.5 ModelTab / 工作台 ModelPicker 数据源）：全部 active
    （38.1 资产级授权放开，授权在模板维度）。每行带 `is_default`（运行时实际会用的那个，
    同 model_gateway 口径），供初始化向导等直接展示真实平台默认模型。"""
    rows = await model_assets.list_active()
    default_id = _default_model_id(rows)
    return [{**row_json(r), "is_default": r.get("model_id") == default_id} for r in rows]


async def test_connection(req: Any) -> dict[str, Any]:
    """平台模型「测试连接」：Key 从服务器环境变量取（secret_env_var 是变量名，客户端不持 Key）。
    egress SSRF 校验 + tool-calling 探测。
    返回 {ok, supports_tool_calling, reason, probe_mode}——probe_mode=mock 时前端须显示「未真实探测」。"""
    import os

    from infra import egress
    from infra.external import llm_provider_client

    env_var = (req.secret_env_var or "").strip()
    if env_var and not _ENV_VAR_RE.match(env_var):
        return {"ok": False, "supports_tool_calling": False,
                "reason": "「API Key 环境变量名」应填变量名（大写字母/数字/下划线），此处像是填了 Key 本身"}
    api_key = os.environ.get(env_var) if env_var else None
    if env_var and not api_key:
        return {"ok": False, "supports_tool_calling": False,
                "reason": f"服务器进程未配置环境变量 {env_var}（Key 未注入，无法测试）"}
    base_url = (req.base_url or "").strip()
    if not base_url:
        return {"ok": False, "supports_tool_calling": False,
                "reason": "该模型未配置 base_url，无法测试连接（走平台网关的模型可不填而直接保存）"}
    try:
        egress.check_llm_egress(base_url)
    except ApiError as e:
        return {"ok": False, "supports_tool_calling": False, "reason": e.message}
    probe = await llm_provider_client.probe(base_url, req.model_id, api_key)
    return {"ok": bool(probe["ok"] and probe["supports_tool_calling"]),
            "supports_tool_calling": bool(probe["supports_tool_calling"]),
            "reason": probe.get("reason"),
            "probe_mode": probe.get("probe_mode")}


async def is_authorized(user_id: str, model_id: str) -> bool:
    """select-model / legacy platform_model_id 绑定校验（38.1 退化）：仅存在性 + active——
    未知/禁用一律 False（fail-closed 保留）。user_id 参数保留占位不消费（免调用方连锁改签名）；
    受限模型想控住入口 = 只把它编进 restricted 模板（授权在模板维度）。"""
    m = await model_assets.get_by_model_id(model_id)
    return m is not None and m["status"] == "active"
