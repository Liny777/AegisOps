"""Tool Gateway（28.2）：平台/用户 HTTP MCP 的唯一受控调用点。

调用链（平台分支）：标注校验 → Scope/APPID 校验 → Secret（仅调用边界、ASK 之后才解出）→
`X-OpenOps-*` header 注入 → HTTP call → `openops.tool.call.*` 审计+SSE。任何一环不满足即
fail-closed：发 `tool.blocked`（审计+SSE，带 reason_code）并抛 ToolBlocked。

- ScopeContext 由 app 层在 start_task 时挂到 TaskState（st.scope_ctx）；标注以启动快照（st.tool_annotations）
  为兜底，**每次工具边界回读 DB 取最新标注**（28.7 热更新，B6），读失败回退快照（ASSET-006 缓存兜底）。
  分层铁律不变：runtime 只 import infra，不 import app。
- **ASK 不在本模块**：是否 ASK 由标注 `is_approval_required` 决定、由两条 runtime 在调用前完成
  （agentscope=PermissionContext / mock=脚本）；invoke() 被调用即意味着已获批 —— 因此 Secret
  在这里才解出，天然满足「ASK 拒绝或超时不解密 Secret」（28.2 顺序）。
- 用户 MCP 分支：不透传 Cookie、不注入任何 `X-OpenOps-*`、不做 APPID 范围管控（28.2）。
"""
from __future__ import annotations

import os
from typing import Any

from infra.external import http_mcp_client
from infra.repositories import mcp_tools
from runtime.emit import emit
from runtime.task_registry import TaskState


class ToolBlocked(Exception):
    """工具调用被 fail-closed 拦截（reason_code ∈ TOOL_NOT_ANNOTATED/TOOL_BLOCKED/APPID_OUT_OF_SCOPE/SECRET_REQUIRED）。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


def _extract_appid(arguments: dict[str, Any], path: str | None) -> str | None:
    """按 appid_arg_path（如 `$.appid` / `$.target.appid`）从入参提取 APPID。"""
    if not path:
        return None
    cur: Any = arguments
    for part in path.lstrip("$").strip(".").split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return str(cur) if cur is not None else None


def _platform_headers(st: TaskState, run: dict[str, Any]) -> dict[str, str]:
    """28.2 平台 MCP 上下文 header（Cookie/X-EC2-IP 由真实网关透传，mock 不造假值）。"""
    scope = st.scope_ctx or {}
    return {
        "X-OpenOps-User-Id": st.user_id,
        "X-OpenOps-Agent-Team-Id": st.instance_id,
        "X-OpenOps-Session-Id": str(run["framework_session_id"]),
        "X-OpenOps-Task-Id": st.task_id,
        "X-OpenOps-Config-Version": str(run.get("config_version_id", "")),
        "X-OpenOps-Effective-Appids": ",".join(scope.get("effective_appids", [])),
        "X-OpenOps-Scope-Snapshot-Id": str(scope.get("scope_snapshot_id", "")),
    }


async def _blocked(st: TaskState, run: dict[str, Any], tool_name: str, reason_code: str, msg: str) -> ToolBlocked:
    await emit(st, run, "openops.tool.blocked", severity="warning", action=tool_name,
               message=msg, reason_code=reason_code, payload={"tool": tool_name})
    return ToolBlocked(reason_code, msg)


async def _effective_annotation(st: TaskState, run: dict[str, Any], tool_name: str) -> dict[str, Any] | None:
    """运行时事实标注（28.7 热更新）：优先取 DB 最新（每次工具边界），读取失败回退任务启动快照。

    最新 catalog 行未标注（schema 变化后不继承）→ 返回 None → TOOL_NOT_ANNOTATED fail-closed。
    与快照不同 → 发 openops.runtime_plan.updated（每 task 每工具一次）。
    """
    snap = (st.tool_annotations or {}).get(tool_name)
    try:
        row = await mcp_tools.get_runtime_annotation(tool_name)
    except Exception:
        return snap  # ASSET-006：读取失败按缓存继续
    if row is None:
        return snap  # 目录无此工具：按快照口径判定
    if row.get("annotation_id") is None:
        fresh: dict[str, Any] | None = None  # 最新行未标注 → fail-closed
    else:
        fresh = {
            "is_approval_required": bool(row["is_approval_required"]),
            "is_secret_required": bool(row["is_secret_required"]),
            "scope_mode": row["scope_mode"],
            "appid_arg_path": row["appid_arg_path"],
            "status": row["annotation_status"],
            "blocked_reason": row["blocked_reason"],
        }
    if fresh != snap and tool_name not in st.plan_notified:
        st.plan_notified.add(tool_name)
        await emit(st, run, "openops.runtime_plan.updated", action=tool_name,
                   message=f"工具 {tool_name} 配置已热更新，按最新标注执行",
                   payload={"tool": tool_name})
    return fresh


def _validate_platform(
    ann: dict[str, Any] | None, st: TaskState, arguments: dict[str, Any]
) -> str | None:
    """平台分支校验：返回拦截原因码；None 即放行。"""
    if ann is None:
        return "TOOL_NOT_ANNOTATED"
    if ann.get("status") != "allowed":
        return "TOOL_BLOCKED"
    mode = ann.get("scope_mode", "none")
    if mode in ("optional", "required"):
        appid = _extract_appid(arguments, ann.get("appid_arg_path"))
        allowed = set((st.scope_ctx or {}).get("effective_appids", []))
        if mode == "required" and appid is None:
            return "APPID_OUT_OF_SCOPE"  # required 必须取到 APPID（28.2）
        if appid is not None and appid not in allowed:
            return "APPID_OUT_OF_SCOPE"
    return None


async def invoke(
    st: TaskState,
    run: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
    *,
    source_type: str = "platform",
    started_msg: str | None = None,
    succeeded_msg: str | None = None,
) -> dict[str, Any]:
    """受控执行一次 HTTP MCP tool 调用；返回 MCP 结果。fail-closed 抛 ToolBlocked。"""
    headers: dict[str, str] = {}
    if source_type == "platform":
        if st.template_tools and tool_name not in st.template_tools:
            # B7·二：模板未绑定的平台工具不进 RuntimePlan（即使全局标注 allowed 也拦）
            raise await _blocked(st, run, tool_name, "TOOL_BLOCKED", f"工具 {tool_name} 未绑定到当前模板，运行时 fail-closed")
        ann = await _effective_annotation(st, run, tool_name)  # 热更新：每次边界取最新标注（28.7）
        reason = _validate_platform(ann, st, arguments)
        if reason == "TOOL_NOT_ANNOTATED":
            raise await _blocked(st, run, tool_name, reason, f"平台工具 {tool_name} 未标注，禁止调用")
        if reason == "TOOL_BLOCKED":
            why = (ann or {}).get("blocked_reason") or "管理员已禁用"
            raise await _blocked(st, run, tool_name, reason, f"平台工具 {tool_name} 已被禁用：{why}")
        if reason == "APPID_OUT_OF_SCOPE":
            raise await _blocked(st, run, tool_name, reason, f"APPID 超出当前有效范围，拒绝调用 {tool_name}")
        assert ann is not None
        # Secret：仅在此处（已过 ASK）解出，注入后即弃；不进事件/审计/日志（28.2）
        if ann.get("is_secret_required"):
            token = os.environ.get("OPENOPS_PLATFORM_MCP_TOKEN")
            if not token:
                raise await _blocked(st, run, tool_name, "SECRET_REQUIRED", f"{tool_name} 需要凭证但未配置")
            headers["Authorization"] = f"Bearer {token}"
        headers.update(_platform_headers(st, run))
    # 用户分支：不注入 X-OpenOps-*、无 Cookie、不做 scope 校验（用户自担责任，仅审计）

    await emit(st, run, "openops.tool.call.started", action=tool_name,
               message=started_msg or f"调用工具 {tool_name}",
               payload={"tool": tool_name, "source_type": source_type})
    try:
        result = await http_mcp_client.call_tool(tool_name, arguments, headers=headers)
    except Exception as e:
        await emit(st, run, "openops.tool.call.failed", severity="error", action=tool_name,
                   message=f"工具 {tool_name} 调用失败", reason_code="TOOL_CALL_FAILED",
                   payload={"tool": tool_name, "error": str(e)[:200]})
        raise
    await emit(st, run, "openops.tool.call.succeeded", action=tool_name,
               message=succeeded_msg or f"工具 {tool_name} 调用完成",
               external_request_id=result.get("request_id"),
               payload={"tool": tool_name,  # 审计查询体验：成功事件也带 tool 名（B5-OBS-002）
                        **{k: v for k, v in result.items() if k in ("result_summary", "execution_id", "status")}})
    return result
