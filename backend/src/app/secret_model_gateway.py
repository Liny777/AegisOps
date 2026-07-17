"""Secret & Model Gateway：Secret 密文落库 + LLM 配置探测（MODEL-001/002，SEC-001）。"""
from __future__ import annotations

from typing import Any

from domain.errors import ApiError, Err
from infra import crypto, egress
from infra.db import row_json
from infra.external import llm_provider_client
from infra.repositories import secrets


async def create_secret(user: dict[str, Any], req: Any) -> dict[str, Any]:
    fp = crypto.fingerprint(req.secret_value)
    cipher = crypto.encrypt(req.secret_value)
    return await secrets.create_secret(user["user_id"], req.secret_name, req.secret_type, req.provider, cipher, fp)


async def list_secrets(user: dict[str, Any]) -> list[dict[str, Any]]:
    return [row_json(r) for r in await secrets.list_secrets_masked(user["user_id"])]


async def create_llm_config(user: dict[str, Any], req: Any) -> dict[str, Any]:
    # SECRET_REQUIRED（修 schema 不一致：user_llm_config.secret_ref_id NOT NULL，缺 secret 不能建）
    if not req.secret_ref_id:
        raise ApiError(Err.SECRET_REQUIRED, "用户自定义 LLM 必须绑定一个已录入的密钥")
    egress.check_llm_egress(req.base_url)  # SSRF 防护（13/28.4）：拦 localhost/metadata/内网基础设施
    s = await secrets.get_secret(req.secret_ref_id)
    if s is None or s["user_id"] != user["user_id"] or s["status"] != "active":
        raise ApiError(Err.SECRET_REQUIRED, "密钥不可用，请重新选择或创建")
    api_key = crypto.decrypt(s["ciphertext"])  # 仅探测瞬时使用，不出网关
    probe = await llm_provider_client.probe(req.base_url, req.model_name, api_key)
    if not probe["ok"] or not probe["supports_tool_calling"]:
        raise ApiError(Err.MODEL_PROBE_FAILED, "模型探测失败或不支持 tool calling，不能设为 active")
    return await secrets.create_llm_config(user["user_id"], req.model_dump(), probe)


async def list_llm_configs(user: dict[str, Any]) -> list[dict[str, Any]]:
    return [row_json(r) for r in await secrets.list_llm_configs(user["user_id"])]


async def test_connection(req: Any) -> dict[str, Any]:
    """用户自带模型「测试连接」（存前探测，不落库）：egress SSRF 校验 + tool-calling 探测。
    raw API Key 仅本次请求瞬时用于探测，绝不落库/日志（SEC-001）。
    返回 {ok, supports_tool_calling, reason, probe_mode}——probe_mode=mock 时前端须显示「未真实探测」。"""
    try:
        egress.check_llm_egress(req.base_url)  # 拦 localhost/metadata/内网基础设施
    except ApiError as e:
        return {"ok": False, "supports_tool_calling": False, "reason": e.message}
    probe = await llm_provider_client.probe(req.base_url, req.model_name, req.api_key or None)
    return {"ok": bool(probe["ok"] and probe["supports_tool_calling"]),
            "supports_tool_calling": bool(probe["supports_tool_calling"]),
            "reason": probe.get("reason"),
            "probe_mode": probe.get("probe_mode")}
