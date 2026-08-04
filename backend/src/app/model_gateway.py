"""Model Gateway：解析运行应使用的平台模型元数据（B2；38.1 起授权在模型模板维度）。

- 元数据（model_id / base_url / secret_env_var）来自 `model_asset` 表；**API Key 不在这里读、也不返回**，
  只把 `secret_env_var`（环境变量名）传下去，由 runtime 构建 credential 时从环境变量取（SEC-001）。
- 资产池对全员一致（list_active，38.1 资产级授权废弃）；**模板级授权二次校验**在
  resolve_template_models（第三闸门，防「先绑后撤销」）：模板失效/未授权 → 回退平台默认。
- 返回 None 表示无可用真模型 → runtime 回退 stub（demo / pytest 不依赖真网、不需要 Key）。
"""
from __future__ import annotations

import os
import uuid
from typing import Any

from domain.errors import ApiError, Err
from infra import egress
from infra.repositories import model_assets, model_templates, secrets

DEFAULT_RUNTIME_MODEL = os.environ.get("OPENOPS_RUNTIME_MODEL", "glm-5.1")

_COMPLETIONS_SUFFIX = "/chat/completions"


def _openai_base_url(url: str | None) -> str | None:
    """OpenAI 兼容客户端要 base（不含 /chat/completions）；资产表存的可能是完整 completions URL。"""
    if url and url.endswith(_COMPLETIONS_SUFFIX):
        return url[: -len(_COMPLETIONS_SUFFIX)]
    return url


def _spec(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": "openai_compatible",
        "model_id": row["model_id"],
        "display_name": row["display_name"],
        "base_url": _openai_base_url(row["base_url"]),
        "secret_env_var": row["secret_env_var"],  # 环境变量名，非 Key 本身
    }


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except (ValueError, AttributeError):
        return False


async def _user_llm_spec(llm_config_id: str, user_id: str) -> dict[str, Any] | None:
    """用户自定义 LLM 解析为 runtime spec（C2：修静默回退——选中用户 LLM 不再退回平台模型）。

    携带 `secret_ref_id`（不解密），由 runtime 在构建边界瞬时 decrypt（SEC-001）；base_url 过 egress。
    """
    cfg = await secrets.get_llm_config(llm_config_id)
    if cfg is None or cfg["user_id"] != user_id or cfg.get("status") != "active":
        return None
    egress.check_llm_egress(cfg["base_url"])  # 每次调用边界复校（28.4）
    return {
        "provider": "openai_compatible",
        "model_id": cfg["model_name"],
        "display_name": cfg["display_name"],
        "base_url": _openai_base_url(cfg["base_url"]),
        "user_secret_ref_id": str(cfg["secret_ref_id"]),  # 用户 Secret 引用（不解密）
        "is_user_llm": True,
    }


async def resolve_runtime_model(selected: str | None, user_id: str) -> dict[str, Any] | None:
    """在**全部 active 平台模型**内解析运行模型（38.1：资产级授权放开）。

    优先级：选中平台模型 → 选中的用户自定义 LLM → 平台默认 → 首个带 secret_env_var 的可用 → None(stub)。
    C2：选中用户 LLM 时解析用户配置（不再静默回退平台模型）；用户 LLM 不可用（禁用/越权）时才回退默认。
    """
    rows = await model_assets.list_active()
    by_id = {r["model_id"]: r for r in rows}
    if selected and selected in by_id:
        return _spec(by_id[selected])
    if selected and _is_uuid(selected):  # UUID 形状 → 可能是用户自定义 LLM 的 llm_config_id（平台 model_id 非 UUID）
        user_spec = await _user_llm_spec(selected, user_id)
        if user_spec is not None:
            return user_spec
        # S3（C2-OBS-003 关闭）：选中的自定义 LLM 失效（禁用/删除/非本人）不再静默回退平台默认——
        # 否则用户 prompt 会流向其没有选择的模型。显式报错让用户重选/改绑。
        raise ApiError(Err.MODEL_NOT_AUTHORIZED,
                       "选中的自定义 LLM 不可用（已禁用或非本人配置），请重新选择模型或调整实例默认绑定")
    return _default_spec(rows)


def _default_spec(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """平台默认解析（与 model_asset_service._default_model_id 同口径）：
    `OPENOPS_RUNTIME_MODEL` → 首个带 secret_env_var 的可用 → None(stub)。
    候选池 = 全部 active 资产（38.1 关键：不受任何授权约束——无模板授权的用户也不会跌 stub）。"""
    by_id = {r["model_id"]: r for r in rows}
    target = by_id.get(DEFAULT_RUNTIME_MODEL) or next((r for r in rows if r["secret_env_var"]), None)
    if target is None or not target.get("secret_env_var"):
        return None  # 无可用真模型 → stub
    return _spec(target)


async def resolve_template_models(model_template_id: str, user_id: str) -> dict[str, Any]:
    """模型模板 → 主/子槽位 runtime spec（38.1：授权在模板维度，槽位只看资产存活）。

    - 模板软删/disabled → TEMPLATE_UNAVAILABLE：主槽回平台默认、子槽 None（子回落跟随主）；
    - 模板 restricted 且该用户无 active grant → TEMPLATE_NOT_AUTHORIZED：同上
      （ACL 第三闸门，防「先绑后撤销」）；
    - 槽位资产缺失/禁用/软删 → 该槽位回退平台默认并记 MODEL_UNAVAILABLE（已非授权问题，语义如实）。
    降级不阻断任务（对齐 platform_model_id 的 fail-safe 先例）；留痕由调用方
    （run_state_service.start_task）写 model.template_degraded 审计 + SSE。
    返回 {"main_spec","sub_spec","main_selected","sub_selected","degraded":[{slot,reason,...}]}。
    """
    rows = await model_assets.list_active()
    degraded: list[dict[str, Any]] = []

    def _template_fallback(reason: str) -> dict[str, Any]:
        degraded.append({"slot": "template", "model_template_id": model_template_id, "reason": reason})
        fb = _default_spec(rows)
        return {"main_spec": fb, "sub_spec": None,
                "main_selected": (fb or {}).get("model_id"), "sub_selected": None,
                "degraded": degraded}

    tpl = await model_templates.get(model_template_id)
    if tpl is None or tpl.get("status") != "active":
        return _template_fallback("TEMPLATE_UNAVAILABLE")
    # fail-closed 写法：access_scope 列缺失（未迁移旧行）视同 restricted，走 grant 判定
    if tpl.get("access_scope") != "all" and not await model_templates.has_grant(model_template_id, user_id):
        return _template_fallback("TEMPLATE_NOT_AUTHORIZED")
    by_asset = {str(r["model_asset_id"]): r for r in rows}

    def _slot(asset_id: Any, slot: str) -> tuple[dict[str, Any] | None, str | None]:
        row = by_asset.get(str(asset_id))
        if row is not None:
            return _spec(row), str(row["model_id"])
        degraded.append({"slot": slot, "model_template_id": model_template_id,
                         "reason": "MODEL_UNAVAILABLE"})
        fb = _default_spec(rows)
        return fb, (fb or {}).get("model_id")

    main_spec, main_sel = _slot(tpl["main_model_asset_id"], "main")
    sub_spec, sub_sel = _slot(tpl["sub_model_asset_id"], "sub")
    return {"main_spec": main_spec, "sub_spec": sub_spec,
            "main_selected": main_sel, "sub_selected": sub_sel, "degraded": degraded}
