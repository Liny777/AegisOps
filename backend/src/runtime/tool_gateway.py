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
import uuid
from typing import Any

from infra import host_ip
from infra.external import http_mcp_client
from infra.external.mcp_registry_client import _BROWSER_UA
from infra.redact import redact_args
from infra.repositories import mcp_tools
from infra.request_context import cached_user_cookie, client_ip, user_cookie
from runtime.emit import emit
from runtime.task_registry import TaskState


class ToolBlocked(Exception):
    """工具调用被 fail-closed 拦截（reason_code ∈ TOOL_NOT_ANNOTATED/TOOL_BLOCKED/APPID_OUT_OF_SCOPE/SECRET_REQUIRED）。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


def _args_for_event(arguments: dict[str, Any]) -> dict[str, Any]:
    """工具入参转事件 payload：序列化超 2000 字则截断为占位（防大参数撑爆事件/审计行）。"""
    arguments = redact_args(arguments)  # 连带 D：key 级脱敏先于长度截断
    import json

    try:
        s = json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"_unserializable": str(arguments)[:200]}
    if len(s) <= 2000:
        return arguments
    return {"_truncated": s[:2000]}


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
    """28.2 平台 MCP 出站上下文 header + 用户登录态透传（IAM 保护的内网 MCP 靠用户 Cookie 鉴权）。"""
    scope = st.scope_ctx or {}
    h = {
        "X-OpenOps-User-Id": st.user_id,
        "X-OpenOps-Agent-Team-Id": st.instance_id,
        "X-OpenOps-Session-Id": str(run["framework_session_id"]),
        "X-OpenOps-Task-Id": st.task_id,
        "X-OpenOps-Config-Version": str(run.get("config_version_id", "")),
        "X-OpenOps-Effective-Appids": ",".join(scope.get("effective_appids", [])),
        "X-OpenOps-Scope-Snapshot-Id": str(scope.get("scope_snapshot_id", "")),
        "X-OpenOps-Audit-Trace-Id": str(run.get("audit_trace_id", "")),  # 28.2：回放 Trace 串联
    }
    ws = scope.get("workspace_id") or ""
    if ws:
        h["workspace_id"] = str(ws)  # omodel 工作空间 id（接收端 MCP 按此名读）
    # 用户登录态透传（与 omodel/console 出站同款，console_client_kwargs 口径）：华为 IAM 会话绑客户端 IP——
    # 只带 Cookie 从服务器 IP 出站会被判 code=1001 失效，故 Cookie 必与用户真实 IP 一并带。
    # 优先级：当前请求上下文用户 cookie > 按 user_id 缓存（后台 task 兜底）> env（仅联调调试）。
    # 后台 task 经 create_task 继承请求 contextvars，故此处（工具调用时）仍能读到发起用户的 cookie/IP。
    cookie = user_cookie() or cached_user_cookie(st.user_id) or os.getenv("OPENOPS_MCPREGISTRY_COOKIE") or ""
    if cookie:
        h["Cookie"] = cookie
        h["User-Agent"] = _BROWSER_UA  # 网关拦脚本 UA，按浏览器 UA 放行
    ip = client_ip()
    if ip:
        h["IAM-Client-Ip"] = ip
        h["X-Forwarded-For"] = ip
    # x-ec2-ip = **OpenOps 后端主机自身的 IP**（对端按调用来源主机做准入/审计）。与上面两个头语义
    # 完全不同——那是**用户浏览器**的 IP（IAM 会话绑它）——两者并存，任一取不到都不可用另一个顶替。
    # 只在本函数（平台支路）构建 ⇒ 用户自注册 MCP 天然收不到（其 URL 由用户任填，见 28.2）。
    h.update(host_ip.ec2_ip_headers())
    return h


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
        # 真机排除占位平台 MCP，与 runtime_annotations 的快照口径**保持一致**：本查询按 tool_name
        # 全局取最新一条，占位资产（endpoint=http://mock）的 catalog 行可能比真 server 的更新而被选中，
        # 两层取到不同标注就会重演「管理台设了不生效」。判定见 mcp_tools._EXCLUDE_PLACEHOLDER。
        row = await mcp_tools.get_runtime_annotation(
            tool_name, exclude_placeholder=os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() == "real")
    except Exception:
        return snap  # ASSET-006：读取失败按缓存继续
    if row is None:
        return snap  # 目录无此工具：按快照口径判定
    if row.get("annotation_id") is None:
        # 动态注册表工具（origin=dynamic）：catalog 行只是 reconcile 持久化的「发现」，未标注不推翻
        # 运行时注入（拍板 i：readOnlyHint 定审批）——否则入库动作本身会把可用工具变不可用（内网实测
        # 回归：alarm-server 入库后 query_alarm_list 被 TOOL_NOT_ANNOTATED 拦）。管理员显式标注后
        # （annotation_id 非空）走下方 fresh=catalog 分支，以 catalog 为准（含 blocked 立即生效）。
        # 平台工具（含 schema 变更后标注软删）仍 fail-closed（28.7 标注不继承铁律）。
        fresh: dict[str, Any] | None = snap if (snap or {}).get("origin") == "dynamic" else None
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
    server_url: str | None = None,
) -> dict[str, Any]:
    """受控执行一次 HTTP MCP tool 调用；返回 MCP 结果。fail-closed 抛 ToolBlocked。"""
    # 同一次调用的 started/succeeded/failed 必须共享稳定 ID。不能再让 AG-UI 根据事件到达
    # 顺序猜配对关系：并发子 Agent 的完成顺序与启动顺序通常不同，LIFO 会把结果挂错卡片。
    tool_call_id = str(uuid.uuid4())
    headers: dict[str, str] = {}
    if source_type == "platform":
        if st.template_tools is not None and tool_name not in st.template_tools:
            # B7·二：模板未绑定的平台工具不进 RuntimePlan（即使全局标注 allowed 也拦）。
            # 空集也拦（B7-SEC-001：模板显式零工具 ≠ 无限制；None 才是未装配模板信息）。
            raise await _blocked(st, run, tool_name, "TOOL_BLOCKED", f"工具 {tool_name} 未绑定到当前模板，运行时 fail-closed")
        ann = await _effective_annotation(st, run, tool_name)  # 热更新：每次边界取最新标注（28.7）
        reason = _validate_platform(ann, st, arguments)
        if reason == "TOOL_NOT_ANNOTATED":
            raise await _blocked(st, run, tool_name, reason, f"平台工具 {tool_name} 未标注，禁止调用")
        if reason == "TOOL_BLOCKED":
            why = (ann or {}).get("blocked_reason") or "管理员已禁用"
            raise await _blocked(st, run, tool_name, reason, f"平台工具 {tool_name} 已被禁用：{why}")
        if reason == "APPID_OUT_OF_SCOPE":
            bad = _extract_appid(arguments, (ann or {}).get("appid_arg_path"))
            allowed = (st.scope_ctx or {}).get("effective_appids", [])
            raise await _blocked(st, run, tool_name, reason,
                                 f"APPID 超出有效范围，拒绝调用 {tool_name}（工具传入 appid={bad!r}，当前 scope={allowed}）")
        assert ann is not None
        # Secret：仅在此处（已过 ASK）解出，注入后即弃；不进事件/审计/日志（28.2）
        if ann.get("is_secret_required"):
            token = os.environ.get("OPENOPS_PLATFORM_MCP_TOKEN")
            if not token:
                raise await _blocked(st, run, tool_name, "SECRET_REQUIRED", f"{tool_name} 需要凭证但未配置")
            headers["Authorization"] = f"Bearer {token}"
        headers.update(_platform_headers(st, run))
        # 28.2：恢复类/ASK tool 批准后透传 approval id（可空）——仅当本 tool 需 ASK（避免带上无关审批）
        if ann.get("is_approval_required") and st.approval_id:
            headers["X-OpenOps-Approval-Request-Id"] = str(st.approval_id)
    # 用户分支：不注入 X-OpenOps-*、无 Cookie、不做 scope 校验（用户自担责任，仅审计）

    await emit(st, run, "openops.tool.call.started", action=tool_name,
               message=started_msg or f"调用工具 {tool_name}",
               payload={"tool": tool_name, "source_type": source_type,
                        "tool_call_id": tool_call_id,
                        "agent_key": st.agent_key,  # D 块：前端活动栏按 agent 分组
                        # 入参进事件（前端工具卡展示；内网实测缺口：agent 真带参但界面显示空）。
                        # Secret 不在 arguments（gateway 在调用边界注 header，28.2 不破）；超长截断防事件膨胀
                        "arguments": _args_for_event(arguments)})
    try:
        # 握手头显式按 source_type 传（非从 headers 反查）：direct 路由下 initialize 与 tools/call
        # 必须同带 x-ec2-ip，否则对端若在握手阶段就按 IP 准入会握手即失败（见 call_tool docstring）。
        result = await http_mcp_client.call_tool(
            tool_name, arguments, headers=headers, server_url=server_url,
            handshake_headers=host_ip.ec2_ip_headers() if source_type == "platform" else None)
    except Exception as e:
        await emit(st, run, "openops.tool.call.failed", severity="error", action=tool_name,
                   message=f"工具 {tool_name} 调用失败", reason_code="TOOL_CALL_FAILED",
                   payload={"tool": tool_name, "tool_call_id": tool_call_id, "error": str(e)[:200]})
        raise
    await emit(st, run, "openops.tool.call.succeeded", action=tool_name,
               message=succeeded_msg or f"工具 {tool_name} 调用完成",
               external_request_id=result.get("request_id"),
               payload={"tool": tool_name,  # 审计查询体验：成功事件也带 tool 名（B5-OBS-002）
                        "tool_call_id": tool_call_id,
                        "agent_key": st.agent_key,  # D 块：前端活动栏按 agent 分组
                        **{k: v for k, v in result.items() if k in ("result_summary", "execution_id", "status")}})
    return result
