"""Secret & Model Gateway：Secret 密文落库 + LLM 配置探测（MODEL-001/002，SEC-001）。"""
from __future__ import annotations

import uuid
from typing import Any

from domain.errors import ApiError, Err
from infra import crypto, egress
from infra.db import row_json
from infra.external import llm_provider_client
from infra.repositories import audit, secrets


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
    probe = await llm_provider_client.probe(req.base_url, req.model_name, api_key, req.extra_headers)
    if not probe["ok"] or not probe["supports_tool_calling"]:
        raise ApiError(Err.MODEL_PROBE_FAILED, "模型探测失败或不支持 tool calling，不能设为 active")
    return await secrets.create_llm_config(user["user_id"], req.model_dump(), probe)


async def list_llm_configs(user: dict[str, Any]) -> list[dict[str, Any]]:
    return [row_json(r) for r in await secrets.list_llm_configs(user["user_id"])]


# 改动即让上次探测结论失效的字段：必须重探测通过才落库，否则会存下一个 active 但跑不通的配置
_PROBE_FIELDS = ("base_url", "model_name", "secret_ref_id", "extra_headers")


async def _owned_llm_config(user: dict[str, Any], llm_config_id: str) -> dict[str, Any]:
    """取本人的配置，否则 NOT_FOUND。刻意不区分「不存在」与「是别人的」——
    否则接口成了「探测某 UUID 是否存在」的旁路（同 agent_team_service._owner_check 口径）。"""
    cfg = await secrets.get_llm_config(llm_config_id)
    if cfg is None or cfg["user_id"] != user["user_id"]:
        raise ApiError(Err.NOT_FOUND, "自定义模型配置不存在")
    return cfg


async def update_llm_config(user: dict[str, Any], llm_config_id: str, req: Any) -> dict[str, Any]:
    """局部更新自带模型（PATCH：只改显式提供的键）。

    改连接类字段（base_url/model_name/secret_ref_id/extra_headers）会强制重探测——与创建同门禁
    （MODEL_PROBE_FAILED 则整条不落库）。改完**新 run 立即生效**：model_gateway 每次 run 启动
    都重查库，无缓存需失效；已在跑的 run 沿用 TaskState 里已解析的 spec 到结束。
    """
    cfg = await _owned_llm_config(user, llm_config_id)
    fields = req.model_dump(exclude_unset=True)  # 未出现的键不进 SET，与「显式传 null」区分开
    fields.pop("client_request_id", None)
    if not fields:
        raise ApiError(Err.VALIDATION_FAILED, "未提供任何可更新字段")
    for k in ("display_name", "base_url", "model_name", "context_window_tokens"):
        if k in fields and fields[k] is None:  # 这些列 NOT NULL：显式 null 会被 DB 拒（整条 500）
            raise ApiError(Err.VALIDATION_FAILED, f"{k} 不可置空")

    merged_headers = _merged_headers(cfg, fields)
    if any(k in fields for k in _PROBE_FIELDS):
        base_url = fields.get("base_url") or cfg["base_url"]
        egress.check_llm_egress(base_url)  # SSRF 防护（13/28.4）：改址也要过闸
        secret_ref = fields.get("secret_ref_id") or str(cfg["secret_ref_id"])
        s = await secrets.get_secret(secret_ref)
        if s is None or s["user_id"] != user["user_id"] or s["status"] != "active":
            raise ApiError(Err.SECRET_REQUIRED, "密钥不可用，请重新选择或创建")
        api_key = crypto.decrypt(s["ciphertext"])  # 仅探测瞬时使用，不出网关
        probe = await llm_provider_client.probe(
            base_url, fields.get("model_name") or cfg["model_name"], api_key, merged_headers)
        if not probe["ok"] or not probe["supports_tool_calling"]:
            raise ApiError(Err.MODEL_PROBE_FAILED, "模型探测失败或不支持 tool calling，改动未保存")

    if "extra_headers" in fields:  # header 存 extra_params_json，不是独立列
        fields["extra_params_json"] = {"extra_headers": merged_headers} if merged_headers else {}
    fields.pop("extra_headers", None)
    await secrets.update_fields(llm_config_id, fields, user["user_id"])
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="user_llm_config.updated",
        user_id=user["user_id"], action="update",
        # 只记改了哪些字段名；header 的名与值、base_url 均不进审计（可能含租户/路由凭据）
        payload_redacted={"llm_config_id": llm_config_id, "fields": sorted(fields)},
    )
    return row_json(await secrets.get_llm_config(llm_config_id))  # type: ignore[arg-type]


def _merged_headers(cfg: dict[str, Any], fields: dict[str, Any]) -> dict[str, str]:
    """本次生效的自定义 Header：传了就整体替换（{} = 清空），没传则沿用库里的。"""
    if "extra_headers" in fields:
        return fields["extra_headers"] or {}
    return (cfg.get("extra_params_json") or {}).get("extra_headers") or {}


async def delete_llm_config(user: dict[str, Any], llm_config_id: str) -> None:
    """软删自带模型 + 连带作废其专属 Secret。

    仍被某实例**当前生效配置**绑定时拒删（fail-closed）：运行时对失效的自带 LLM 是硬失败——
    model_gateway.resolve_runtime_model 抛 MODEL_NOT_AUTHORIZED 且 run_state_service 未接住，
    真删了那些 Agent 每次起任务都 403、要进编辑向导换绑才能恢复。先让用户改绑再删。
    """
    cfg = await _owned_llm_config(user, llm_config_id)
    refs = await secrets.list_instances_using_llm_config(llm_config_id)
    if refs:
        names = "、".join(str(r["instance_name"]) for r in refs[:5])
        more = f" 等 {len(refs)} 个" if len(refs) > 5 else ""
        raise ApiError(Err.VALIDATION_FAILED,
                       f"该模型正被 Agent 使用（{names}{more}），请先在对应 Agent 的编辑页换用其它模型再删除")
    await secrets.soft_delete_llm_config(llm_config_id, user["user_id"])
    if cfg.get("secret_ref_id"):
        await secrets.soft_delete_secret(str(cfg["secret_ref_id"]), user["user_id"])
    await audit.insert_event(
        audit_trace_id=str(uuid.uuid4()), event_type="user_llm_config.deleted",
        user_id=user["user_id"], action="delete", payload_redacted={"llm_config_id": llm_config_id},
    )


async def test_connection(req: Any) -> dict[str, Any]:
    """用户自带模型「测试连接」（存前探测，不落库）：egress SSRF 校验 + tool-calling 探测。
    raw API Key 仅本次请求瞬时用于探测，绝不落库/日志（SEC-001）。
    返回 {ok, supports_tool_calling, reason, probe_mode}——probe_mode=mock 时前端须显示「未真实探测」。"""
    try:
        egress.check_llm_egress(req.base_url)  # 拦 localhost/metadata/内网基础设施
    except ApiError as e:
        return {"ok": False, "supports_tool_calling": False, "reason": e.message}
    probe = await llm_provider_client.probe(req.base_url, req.model_name, req.api_key or None,
                                            req.extra_headers)
    return {"ok": bool(probe["ok"] and probe["supports_tool_calling"]),
            "supports_tool_calling": bool(probe["supports_tool_calling"]),
            "reason": probe.get("reason"),
            "probe_mode": probe.get("probe_mode")}
