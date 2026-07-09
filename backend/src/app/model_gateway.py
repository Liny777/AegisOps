"""Model Gateway：解析运行应使用的平台模型元数据（B2；B7 起数据源为 model_asset + 按人授权）。

- 元数据（model_id / base_url / secret_env_var）来自 `model_asset` 表；**API Key 不在这里读、也不返回**，
  只把 `secret_env_var`（环境变量名）传下去，由 runtime 构建 credential 时从环境变量取（SEC-001）。
- **授权二次校验**（B7 模型 ACL 第三处 gating，防「先选后撤销」）：只在该用户可用集合内解析；
  选中模型已被撤销授权/禁用 → 忽略选中值回退默认。
- 返回 None 表示无可用真模型 → runtime 回退 stub（demo / pytest 不依赖真网、不需要 Key）。
"""
from __future__ import annotations

import os
from typing import Any

from infra.repositories import model_assets

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


async def resolve_runtime_model(selected: str | None, user_id: str) -> dict[str, Any] | None:
    """在**该用户授权范围内**解析运行模型：选中值 → 平台默认 → 首个带 secret_env_var 的可用模型 → None(stub)。"""
    rows = await model_assets.list_available_for_user(user_id)
    by_id = {r["model_id"]: r for r in rows}
    target = None
    if selected and selected in by_id:
        target = by_id[selected]
    elif DEFAULT_RUNTIME_MODEL in by_id:
        target = by_id[DEFAULT_RUNTIME_MODEL]
    else:
        target = next((r for r in rows if r["secret_env_var"]), None)
    if target is None or not target.get("secret_env_var"):
        return None  # 无可用真模型 → stub
    return _spec(target)
