"""审计与活动事件的脱敏、安全投影。

活动流会同时进入审计、/state、/events、SSE、AG-UI CUSTOM 和浏览器 DOM，因此这里采用
deny-by-default：公共关联字段按白名单保留，工具/Skill/沙箱的完整参数与输出只生成短摘要；
审批参数是用户决策所必需的例外，但仍限制深度、数量和长度。
"""
from __future__ import annotations

import json
import re
from typing import Any

from infra.chart_contract import ChartContractError, chart_result_summary, normalize_chart_arguments

_KEY_RE = re.compile(
    r"(pass(word|wd)?|token|secret|api[_-]?key|apikey|authorization|cookie|credential|private[_-]?key)",
    re.I,
)
_HEADER_RE = re.compile(
    r"(?im)\b(authorization|proxy-authorization|cookie|set-cookie)\s*[:=]\s*[^\r\n]+"
)
_NAMED_VALUE_RE = re.compile(
    r"(?i)(\b(?:password|passwd|pwd|token|secret|api[_-]?key|apikey|credential|private[_-]?key)\b"
    r"[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s&;,]+)"
)
_AUTH_VALUE_RE = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}")
_TOKEN_RE = re.compile(
    r"(?i)(?:sk-[A-Za-z0-9_-]{6,}|gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,}|"
    r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,})"
)
_URL_CREDENTIAL_RE = re.compile(r"(://[^\s/:@]+:)[^\s/@]+(@)")


def _redact_string(value: str) -> str:
    value = _HEADER_RE.sub(lambda m: f"{m.group(1)}: [REDACTED]", value)
    value = _NAMED_VALUE_RE.sub(lambda m: f"{m.group(1)}[REDACTED]", value)
    value = _AUTH_VALUE_RE.sub("[REDACTED]", value)
    value = _TOKEN_RE.sub("[REDACTED]", value)
    return _URL_CREDENTIAL_RE.sub(r"\1[REDACTED]\2", value)


def redact_args(obj: Any) -> Any:
    """递归脱敏（不改原对象）；敏感 key 整体打码，自由文本凭证模式同样打码。"""
    if isinstance(obj, dict):
        return {
            k: ("***" if isinstance(k, str) and _KEY_RE.search(k) else redact_args(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact_args(v) for v in obj]
    if isinstance(obj, str):
        return _redact_string(obj)
    return obj


def redact_text(value: Any, *, max_length: int = 300) -> str:
    """脱敏并截断可展示文本；用于活动摘要，严禁写入完整 prompt/结果。"""
    return _redact_string(str(value))[:max_length]


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    """审批所需的小型结构预览：最多 2 层、8 项、每个字符串 160 字。"""
    if depth >= 2 and isinstance(value, (dict, list, tuple)):
        return "[嵌套内容已省略]"
    if isinstance(value, dict):
        redacted = redact_args(value)
        items = list(redacted.items())[:8]
        out = {str(k)[:80]: _bounded_value(v, depth=depth + 1) for k, v in items}
        if len(redacted) > len(items):
            out["_truncated"] = f"另有 {len(redacted) - len(items)} 项"
        return out
    if isinstance(value, (list, tuple)):
        items = list(value)[:8]
        out = [_bounded_value(v, depth=depth + 1) for v in items]
        if len(value) > len(items):
            out.append(f"另有 {len(value) - len(items)} 项")
        return out
    if isinstance(value, str):
        return redact_text(value, max_length=160)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(value, max_length=160)


def _bounded_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"summary": redact_text(value, max_length=300)}
    result = _bounded_value(value)
    assert isinstance(result, dict)
    encoded = json.dumps(result, ensure_ascii=False, default=str)
    if len(encoded) <= 1200:
        return result
    return {"summary": redact_text(encoded, max_length=1000), "_truncated": True}


def sanitize_approval_arguments(value: Any) -> dict[str, Any]:
    """审批卡可公开的参数预览；与活动流 approval.required 使用同一上限。"""
    return _bounded_mapping(value)


def _summary(value: Any, *, max_length: int = 300) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return redact_text(value, max_length=max_length)
    try:
        encoded = json.dumps(redact_args(value), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        encoded = str(value)
    return redact_text(encoded, max_length=max_length)


_COMMON_KEYS = (
    "agent_key",
    "agent_label",
    "leader_task_id",
    "child_task_id",
    "delegation_id",
    "dispatch_batch_id",
    "dispatch_batch_no",
    "display_label",
)

_GENERIC_SAFE_KEYS = (
    "target_user_id",
    "workspace_id",
    "model_id",
    "model_asset_id",
    "access_scope",
    "template_id",
    "template_version_id",
    "version_no",
    "config_version_id",
    "from_template_version",
    "to_template_version",
    "input_chars",
    "name",
)


def _copy_scalar(source: dict[str, Any], target: dict[str, Any], key: str, *, limit: int = 300) -> None:
    value = source.get(key)
    if value is None:
        return
    if isinstance(value, str):
        target[key] = redact_text(value, max_length=limit)
    elif isinstance(value, (bool, int, float)):
        target[key] = value


_RCA_SCALARS = ("revision", "title", "phaseLabel", "currentQ", "why", "conclusion", "status", "board_task_id")
_RCA_LIST_FIELDS: dict[str, tuple[str, ...]] = {
    "tiles": ("label", "value"),
    "steps": ("num", "label", "state", "summary"),
    "facts": ("text",),
    "unknowns": ("text",),
    "sources": ("name", "status", "tone"),
    "hypotheses": ("text", "tag", "tagTone", "conf"),
    "actions": ("tier", "text", "confirm", "impact", "status", "statusTone"),
}


def _safe_rca(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _RCA_SCALARS:
        _copy_scalar(payload, out, key, limit=1200 if key == "conclusion" else 500)
    for list_key, fields in _RCA_LIST_FIELDS.items():
        value = payload.get(list_key)
        if not isinstance(value, list):
            continue
        rows: list[dict[str, Any]] = []
        for item in value[:30]:
            if not isinstance(item, dict):
                continue
            clean: dict[str, Any] = {}
            for field in fields:
                _copy_scalar(item, clean, field, limit=500)
            rows.append(clean)
        out[list_key] = rows
    return out


def sanitize_activity_payload(
    event_type: str,
    payload: Any,
    *,
    message: Any = "",
    external_request_id: Any = None,
) -> dict[str, Any]:
    """事件 payload 的公开安全形状；未知事件只保留公共关联字段与摘要。"""
    source = payload if isinstance(payload, dict) else {}
    out: dict[str, Any] = {}
    for key in _COMMON_KEYS:
        _copy_scalar(source, out, key, limit=200)
    for key in _GENERIC_SAFE_KEYS:
        _copy_scalar(source, out, key, limit=300)
    if isinstance(source.get("keys"), list):
        out["keys"] = [redact_text(value, max_length=100) for value in source["keys"][:30]]

    summary = redact_text(source.get("summary") or message, max_length=300)
    if summary:
        out["summary"] = summary
    if source.get("reason") is not None:
        out["reason_summary"] = _summary(source.get("reason"), max_length=300)
    if source.get("error") is not None:
        out["error_summary"] = _summary(source.get("error"), max_length=300)

    event = event_type.lower().removeprefix("openops.")

    if event == "rca.updated":
        out.update(_safe_rca(source))

    if event == "workspace.admin_created":
        # 管理员代查审计：哪个 APPID 被手输纳入范围是事件核心，必须在管理台可见
        for key in ("manual_app_ids", "app_ids"):
            if isinstance(source.get(key), list):
                out[key] = [redact_text(v, max_length=100) for v in source[key][:30]]
        _copy_scalar(source, out, "name", limit=200)

    if event.startswith("tool.") or event == "tool.blocked" or event == "runtime_plan.updated":
        # server_name = 工具所属 MCP server（复合键身份下同名工具靠它区分；管理台明文标识符，不涉敏）
        for key in ("tool", "source_type", "status", "execution_id", "server_name"):
            _copy_scalar(source, out, key, limit=200)
        if event == "tool.skipped" and isinstance(source.get("tools"), list):
            # 「注册表已发现、但未进模板白名单」的工具名清单：本事件的**全部诊断价值**都在这儿。
            # 上面只拷标量，列表除 `keys` 外一律丢弃 ⇒ 不显式保留就等于「噪音拉满、数据为零」：
            # message 只剩数量+前 3 个，管理员在审计里查不到究竟哪些工具没装配。
            # 工具名是管理台明文可见的标识符，不涉敏；上限口径同 workspace.admin_created 的 APPID 清单。
            out["tools"] = [redact_text(v, max_length=100) for v in source["tools"][:50]]
            out["tools_total"] = len(source["tools"])
        request_id = external_request_id or source.get("request_id") or source.get("external_request_id")
        if request_id is not None:
            out["request_id"] = redact_text(request_id, max_length=200)
        if "started" in event or event.endswith("tool.call"):
            # Generative UI 的唯一结构化参数例外。render_chart 是平台内置的纯展示工具，
            # 先递归脱敏、再按固定契约重新校验；任何未知字段/HTML/style/超限数据都会降级为摘要，
            # 绝不复用普通工具的完整入参透传能力。
            chart_arguments = None
            if source.get("tool") == "render_chart" and source.get("arguments") is not None:
                try:
                    chart_arguments = normalize_chart_arguments(redact_args(source["arguments"]))
                except ChartContractError:
                    chart_arguments = None
            if chart_arguments is not None:
                out["argument_summary"] = chart_result_summary(chart_arguments)
                out["arguments"] = chart_arguments
            else:
                argument_summary = _summary(
                    source.get("argument_summary") or source.get("arguments"), max_length=300
                )
                if argument_summary:
                    out["argument_summary"] = argument_summary
                    # CopilotKit 标准工具卡仍收到合法 JSON，但不再收到完整参数对象。
                    out["arguments"] = {"summary": argument_summary}
        if "succeeded" in event or "completed" in event or "result" in event:
            result_summary = _summary(source.get("result_summary"), max_length=500)
            if result_summary:
                out["result_summary"] = result_summary
        if "failed" in event or "blocked" in event:
            error_summary = _summary(
                source.get("error_summary") or source.get("error") or source.get("reason"),
                max_length=300,
            )
            if error_summary:
                out["error_summary"] = error_summary

    if event.startswith("approval."):
        for key in ("approval_request_id", "tool", "decision", "server_name"):
            _copy_scalar(source, out, key, limit=200)
        if event == "approval.required":
            args_source = source.get("args")
            if args_source is None and source.get("command") is not None:
                args_source = {"command": source.get("command")}
            if args_source is not None:
                out["args"] = _bounded_mapping(args_source)
            for key in ("command", "target", "impact"):
                _copy_scalar(source, out, key, limit=300)

    if event.startswith("subagent."):
        if event.endswith("dispatched") or event.endswith("started"):
            task_summary = _summary(source.get("task_summary") or source.get("task"), max_length=300)
            if task_summary:
                out["task_summary"] = task_summary
        if event.endswith("reported"):
            _copy_scalar(source, out, "report_chars")
            report_summary = _summary(source.get("report_summary"), max_length=300)
            if report_summary:
                out["report_summary"] = report_summary
        if event.endswith(("failed", "timeout", "cancelled", "canceled")):
            error_summary = _summary(
                source.get("error_summary") or source.get("error") or source.get("reason"),
                max_length=300,
            )
            if error_summary:
                out["error_summary"] = error_summary

    if event.startswith("model.") or event == "model.selected":
        for key in ("model", "input_tokens", "output_tokens"):
            _copy_scalar(source, out, key, limit=200)

    if event.startswith("skill."):
        for key in ("skill", "status", "exit_code"):
            _copy_scalar(source, out, key, limit=200)
        result_status = source.get("result", {}).get("status") if isinstance(source.get("result"), dict) else None
        if result_status is not None:
            out["result"] = {"status": redact_text(result_status, max_length=80)}
        detail = source.get("result_summary") or source.get("result") or source.get("stdout")
        if detail is not None:
            out["result_summary"] = _summary(detail, max_length=300)
        error = source.get("error_summary") or source.get("error") or source.get("stderr")
        if error is not None:
            out["error_summary"] = _summary(error, max_length=300)

    if event.startswith("sandbox.command"):
        for key in ("layer", "exit_code", "status"):
            _copy_scalar(source, out, key, limit=100)
        command = _summary(source.get("command"), max_length=300)
        if command:
            # 审批/审计兼容保留短命令；stdout/stderr 只保留摘要。
            out["command"] = command
            out["command_summary"] = command
        stdout = source.get("stdout_summary") or source.get("stdout")
        stderr = source.get("stderr_summary") or source.get("stderr")
        if stdout is not None:
            out["stdout_summary"] = _summary(stdout, max_length=300)
        if stderr is not None:
            out["stderr_summary"] = _summary(stderr, max_length=300)

    if event.startswith("scope."):
        for key in ("scope_snapshot_id", "scope_revision", "appid_count", "from", "to"):
            _copy_scalar(source, out, key, limit=200)

    if event == "task.completed":
        conclusion = _summary(source.get("conclusion"), max_length=4000)
        if conclusion:
            out["conclusion"] = conclusion
    elif event in ("task.failed", "task.cancelled", "task.interrupted"):
        error_summary = _summary(
            source.get("error_summary") or source.get("error") or source.get("reason"),
            max_length=300,
        )
        if error_summary:
            out["error_summary"] = error_summary

    return out
