"""模型资产（B7，30.6 五；38.1 起授权迁模型模板维度，本服务只管资产 CRUD/探测）。

- 授权见 model_template_service（模板 scope+grant 三闸门）；资产池对全员一致。
- API Key（2026-08-17 起）：管理台表单直填 → 本层 Fernet 加密 → `sre_model_asset` 密文三列。
  明文只在本层与探测客户端之间过一次手，**不进 fields、不进审计、不进日志**（SEC-001）；
  回显只给 `secret_fingerprint` + `has_secret`（仓储 `_PUBLIC_COLS` 保证密文不出库）。
  原口径「Key 只进进程环境变量」已废弃，`secret_env_var` 仅剩一次性导入源（见 seed 的 backfill）。
- is_authorized 退化为「存在 + active」（fail-closed 对未知/禁用仍 False），服务
  select-model 与 legacy platform_model_id 绑定两个旧路径。
"""
from __future__ import annotations

import uuid
from typing import Any

from domain.errors import ApiError, Err
from infra import crypto
from infra.db import row_json
from infra.repositories import audit, model_assets

# 密钥三列的「清除」形态：显式传空串即三列同时置 null（DTO 层靠 exclude_unset 区分「没传」）
_SECRET_CLEARED: dict[str, Any] = {
    "secret_ciphertext": None, "secret_key_version": None, "secret_fingerprint": None,
}


def _encrypt_secret(api_key: str) -> dict[str, Any]:
    """明文 Key → 密文三列。调用方保证 api_key 非空；返回值之外不留明文副本。"""
    return {
        "secret_ciphertext": crypto.encrypt(api_key),
        "secret_key_version": crypto.current_key_version(),
        "secret_fingerprint": crypto.fingerprint(api_key),
    }


async def admin_list() -> list[dict[str, Any]]:
    return [row_json(r) for r in await model_assets.list_all()]


async def register(req: Any, by: str) -> dict[str, Any]:
    if await model_assets.get_by_model_id(req.model_id):
        raise ApiError(Err.VALIDATION_FAILED, f"model_id 已存在：{req.model_id}")
    api_key = (getattr(req, "api_key", "") or "").strip()
    row = await model_assets.create(
        req.display_name, req.protocol, req.model_id, req.base_url,
        None, "active", by,  # secret_env_var 已废弃：新注册一律不写，Key 走密文列
        context_window_tokens=getattr(req, "context_window_tokens", 128000),
        extra_headers=getattr(req, "extra_headers", None),
        secret=_encrypt_secret(api_key) if api_key else None,
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
    if "api_key" in fields:
        # 明文在这里立即换成密文三列后即弃：下方 audit 记的是 sorted(fields)（列名），
        # 若 api_key 留在 fields 里，明文虽不会被记但字段名会误导——更重要的是绝不能流到别处。
        api_key = (fields.pop("api_key") or "").strip()
        fields.update(_encrypt_secret(api_key) if api_key else _SECRET_CLEARED)
    # 这两列 NOT NULL：显式传 null 会被 DB 拒（整条 500），入口处给出可读原因
    if "display_name" in fields and not (fields["display_name"] or "").strip():
        raise ApiError(Err.VALIDATION_FAILED, "display_name 不可置空")
    if "context_window_tokens" in fields and fields["context_window_tokens"] is None:
        raise ApiError(Err.VALIDATION_FAILED, "context_window_tokens 不可置空")
    if "extra_headers" in fields:  # header 存 extra_params_json，不是独立列；传 {} 即清空
        hdrs = fields.pop("extra_headers") or {}
        fields["extra_params_json"] = {"extra_headers": hdrs} if hdrs else {}
    await model_assets.update_fields(model_asset_id, fields, by)
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="model_asset.updated", user_id=by,
        action="update",
        # 只记改了哪些**列名** + 新 base_url。fields 里此刻已无 api_key 明文（上面换成密文三列了），
        # 列名 secret_ciphertext 出现在这里只表示「这次动了密钥」，不泄漏任何密钥内容（SEC-001）。
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
    优先 `OPENOPS_RUNTIME_MODEL`（默认 glm-5.1），否则首个**已配 Key** 的模型（`has_secret`）。
    默认必须带 Key 才成立——否则运行时回退 stub。避免把无 Key 的种子资产（如 Qwen3.5）
    当默认却根本跑不起来（前端初始化向导据此展示真实默认名，而非写死）。"""
    from app.model_gateway import DEFAULT_RUNTIME_MODEL

    by_id = {r.get("model_id"): r for r in rows}
    target = by_id.get(DEFAULT_RUNTIME_MODEL) or next((r for r in rows if r.get("has_secret")), None)
    if target is None or not target.get("has_secret"):
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
    """平台模型「测试连接」：Key 用**本次表单新填的**，没新填就解密库里已存的那把。

    两个来源缺一不可——注册态只有新填的 Key（还没落库）；编辑态用户往往不重填 Key 只改
    base_url，此时必须能拿库里的密钥测通，否则「改个地址就得重录 Key」。
    egress SSRF 校验 + tool-calling 探测。
    返回 {ok, supports_tool_calling, reason, probe_mode}——probe_mode=mock 时前端须显示「未真实探测」。"""
    from infra import egress
    from infra.external import llm_provider_client

    api_key: str | None = (getattr(req, "api_key", "") or "").strip() or None
    asset_id = (getattr(req, "model_asset_id", "") or "").strip()
    if api_key is None and asset_id:  # 编辑态未重填：用库里已存的密钥（瞬时解密，不外泄）
        mat = await model_assets.get_secret_material(asset_id)
        if mat and mat.get("secret_ciphertext"):
            try:
                api_key = crypto.decrypt(mat["secret_ciphertext"])
            except ValueError:
                return {"ok": False, "supports_tool_calling": False,
                        "reason": "已存密钥解密失败（OPENOPS_ENCRYPTION_KEY 变更或密文损坏），请重新填写 API Key"}
    base_url = (req.base_url or "").strip()
    if not base_url:
        return {"ok": False, "supports_tool_calling": False,
                "reason": "该模型未配置 base_url，无法测试连接（走平台网关的模型可不填而直接保存）"}
    try:
        egress.check_llm_egress(base_url)
    except ApiError as e:
        return {"ok": False, "supports_tool_calling": False, "reason": e.message}
    # 自定义 Header 与真实调用同源携带（agentscope_runtime 的 default_headers）——不传这一份
    # 就会出现「测试连接绿勾但真跑被网关拒」
    probe = await llm_provider_client.probe(base_url, req.model_id, api_key,
                                            getattr(req, "extra_headers", None))
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
