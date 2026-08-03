"""Model Gateway：解析运行应使用的平台模型元数据（B2；B7 起数据源为 model_asset + 按人授权）。

- 元数据（model_id / base_url / secret_env_var）来自 `model_asset` 表；**API Key 不在这里读、也不返回**，
  只把 `secret_env_var`（环境变量名）传下去，由 runtime 构建 credential 时从环境变量取（SEC-001）。
- **授权二次校验**（B7 模型 ACL 第三处 gating，防「先选后撤销」）：只在该用户可用集合内解析；
  选中模型已被撤销授权/禁用 → 忽略选中值回退默认。
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
    """在**该用户授权范围内**解析运行模型。

    优先级：选中平台模型（授权内）→ 选中的用户自定义 LLM → 平台默认 → 首个带 secret_env_var 的可用 → None(stub)。
    C2：选中用户 LLM 时解析用户配置（不再静默回退平台模型）；用户 LLM 不可用（禁用/越权）时才回退默认。
    """
    rows = await model_assets.list_available_for_user(user_id)
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
    `OPENOPS_RUNTIME_MODEL` → 首个带 secret_env_var 的可用 → None(stub)。"""
    by_id = {r["model_id"]: r for r in rows}
    target = by_id.get(DEFAULT_RUNTIME_MODEL) or next((r for r in rows if r["secret_env_var"]), None)
    if target is None or not target.get("secret_env_var"):
        return None  # 无可用真模型 → stub
    return _spec(target)


async def resolve_template_models(model_template_id: str, user_id: str) -> dict[str, Any]:
    """模型模板 → 主/子槽位 runtime spec（B7 ACL 第三闸门的模板版，逐槽 fail-safe）。

    - 槽位资产不在该用户授权集合（授权撤销/资产禁用/软删）→ 该槽位回退平台默认并记 degraded；
    - 模板整体失效（软删/disabled）→ 主槽回退平台默认、子槽置 None（子回落跟随主）。
    降级不阻断任务（对齐 platform_model_id 的 fail-safe 先例）；留痕由调用方
    （run_state_service.start_task）写 model.template_degraded 审计 + SSE。
    返回 {"main_spec","sub_spec","main_selected","sub_selected","degraded":[{slot,reason,...}]}。
    """
    rows = await model_assets.list_available_for_user(user_id)
    degraded: list[dict[str, Any]] = []
    tpl = await model_templates.get(model_template_id)
    if tpl is None or tpl.get("status") != "active":
        degraded.append({"slot": "template", "model_template_id": model_template_id,
                         "reason": "TEMPLATE_UNAVAILABLE"})
        fb = _default_spec(rows)
        return {"main_spec": fb, "sub_spec": None,
                "main_selected": (fb or {}).get("model_id"), "sub_selected": None,
                "degraded": degraded}
    by_asset = {str(r["model_asset_id"]): r for r in rows}

    def _slot(asset_id: Any, slot: str) -> tuple[dict[str, Any] | None, str | None]:
        row = by_asset.get(str(asset_id))
        if row is not None:
            return _spec(row), str(row["model_id"])
        degraded.append({"slot": slot, "model_template_id": model_template_id,
                         "reason": "MODEL_NOT_AUTHORIZED"})
        fb = _default_spec(rows)
        return fb, (fb or {}).get("model_id")

    main_spec, main_sel = _slot(tpl["main_model_asset_id"], "main")
    sub_spec, sub_sel = _slot(tpl["sub_model_asset_id"], "sub")
    return {"main_spec": main_spec, "sub_spec": sub_spec,
            "main_selected": main_sel, "sub_selected": sub_sel, "degraded": degraded}
