"""Model Gateway：解析平台运行模型的元数据（B2）。

- 模型元数据（provider / model_id / base_url / secret_env_var）在 platform_runtime_config（DOMAIN_MODEL）。
- **API Key 不在这里读、也不返回** —— 只把 `secret_env_var`（环境变量名）传下去，由 runtime 构建
  credential 时从环境变量取，绝不落 PG / 日志 / SSE / 审计 / prompt（SEC-001）。
- 返回 None 表示无可用配置 → runtime 回退 stub（demo / pytest 不依赖真网、不需要 Key）。
"""
from __future__ import annotations

import os
from typing import Any

from infra.repositories import runtime_config

DEFAULT_RUNTIME_MODEL = os.environ.get("OPENOPS_RUNTIME_MODEL", "glm-5.1")

_COMPLETIONS_SUFFIX = "/chat/completions"


def _openai_base_url(url: str | None) -> str | None:
    """OpenAI 兼容客户端要 base（不含 /chat/completions）；seed 存的是完整 completions URL。"""
    if url and url.endswith(_COMPLETIONS_SUFFIX):
        return url[: -len(_COMPLETIONS_SUFFIX)]
    return url


async def resolve_runtime_model(selected: str | None = None) -> dict[str, Any] | None:
    """解析当前运行应使用的平台模型元数据；无 active 配置或无 secret_env_var → None（回退 stub）。"""
    target = selected or DEFAULT_RUNTIME_MODEL
    rows = await runtime_config.get_domain(runtime_config.DOMAIN_MODEL)
    cfg = next((r["config_value_json"] for r in rows if r["config_key"] == target), None)
    if cfg is None or cfg.get("status") != "active" or not cfg.get("secret_env_var"):
        return None
    return {
        "provider": "openai_compatible",
        "model_id": cfg["model_id"],
        "display_name": cfg.get("name", cfg["model_id"]),
        "base_url": _openai_base_url(cfg.get("base_url")),
        "secret_env_var": cfg["secret_env_var"],  # 环境变量名，非 Key 本身
    }
