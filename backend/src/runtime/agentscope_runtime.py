"""真 AgentScope 2.0.3 Runtime（B1）：用 Agent + stub model 复刻脚本化 RCA，替换 mock orchestrator。

与 mock 同签名 `run_task(st, run)`；`openops.*` 事件/审计经 [[runtime.emit]] 共享、ASK 走
`st.approval_ev` 握手、PG 仍是事实源。agentscope 为**惰性导入**：仅 `OPENOPS_RUNTIME=agentscope`
选中时加载，pytest（默认 mock）不依赖它。

2.0.3 映射（B1-0 已核，见 reference-agentscope-2.0.3-api）：
- `agentscope.agent.Agent(+ReActConfig)` 驱动 ReAct；stub 继承 `ChatModelBase` 实现 `_call_api`
  返回脚本化 `ChatResponse`（B2 换真 `glm-5.1`）。
- **事件出口 = `agent.reply_stream()`**（2.0.3 无 `pre/post_reply` hook）；这里把工具调用侧的
  `openops.tool.call.*` / `openops.rca.updated` 做在工具函数内，把 ASK 做在 `RequireUserConfirmEvent`。
- **ASK = permission**：查询工具 `allow_rules` 自动执行；恢复工具 `ask_rules` 暂停发
  `RequireUserConfirmEvent` → 桥到 `runs.create_approval` + `st.approval_ev` → `UserConfirmResultEvent` 恢复。

`create_app`/`RedisStorage`/`SubAgentTemplate`/内置 AG-UI 属 app-server 层，推到 B5/部署块。
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from infra.external import http_mcp_client
from infra.repositories import runs
from runtime.emit import emit
from runtime.rca_demo import rca
from runtime.task_registry import TaskState

ASK_TIMEOUT_S = float(os.environ.get("OPENOPS_ASK_TIMEOUT_S", "300"))


def _require_agentscope() -> None:
    try:
        import agentscope  # noqa: F401
    except ModuleNotFoundError as e:  # pragma: no cover - 环境未装时的清晰报错
        raise RuntimeError(
            "OPENOPS_RUNTIME=agentscope 需要安装 agentscope==2.0.3："
            "pip install -e '.[agentscope]'（或在装有 agentscope 的环境运行）。"
        ) from e


def _build_stub_model() -> Any:
    """stub ChatModelBase：脚本化 query→query→recover→结论（B2 换真 GLM）。"""
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    class StubRcaModel(ChatModelBase):  # type: ignore[misc]
        def __init__(self) -> None:
            # 跳过 super().__init__（无真 credential）；手设 agent 循环会读的属性
            self._step = 0
            self.model = "stub-rca"
            self.stream = False
            self.max_retries = 0
            self.retry_delay = 0.0
            self.context_size = 128000
            self.parameters = None
            self.credential = None

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kw):  # noqa: ANN001
            self._step += 1
            if self._step in (1, 2):  # 巡检 + 定界
                return ChatResponse(content=[ToolCallBlock(
                    type="tool_call", id=f"q{self._step}", name="query_resource",
                    input=json.dumps({"appid": "APP-A"}))], is_last=False)
            if self._step == 3:  # 恢复动作（ask → 审批）
                return ChatResponse(content=[ToolCallBlock(
                    type="tool_call", id="rec", name="recover_execute",
                    input=json.dumps({"appid": "APP-A", "action": "restart"}))], is_last=False)
            return ChatResponse(content=[TextBlock(
                type="text",
                text="已确认根因 H1（Redis 连接泄漏）：重启 svc-a 后连接回落、P99 恢复 210ms，事件闭环。",
            )], is_last=True)

    return StubRcaModel()


def _build_model(st: TaskState) -> Any:
    """有平台模型元数据 + 环境变量 API Key → 真 OpenAI 兼容模型（如 glm-5.1）；否则回退 stub。

    API Key 只在此处从环境变量取，构建 credential 后即用即弃，绝不落 PG / 日志 / 事件 / 审计（SEC-001）。
    """
    spec = st.model_spec
    if spec and spec.get("secret_env_var"):
        api_key = os.environ.get(spec["secret_env_var"])
        if api_key:
            from agentscope.credential import OpenAICredential
            from agentscope.model import OpenAIChatModel

            return OpenAIChatModel(
                credential=OpenAICredential(api_key=api_key),
                model=spec["model_id"],
                stream=False,
                client_kwargs={"base_url": spec["base_url"]} if spec.get("base_url") else None,
            )
    return _build_stub_model()


def _build_toolkit(st: TaskState, run: dict[str, Any]) -> Any:
    """两工具包现有 http_mcp_client；工具内发 openops.tool.call.* + rca.updated（与 mock 同序）。"""
    from agentscope.message import TextBlock
    from agentscope.tool import FunctionTool, Toolkit, ToolResponse

    counter = {"query": 0}

    async def query_resource(appid: str) -> Any:
        """查询指定应用的可观测数据（指标 P99/错误率、Redis 连接数、服务依赖拓扑），用于巡检与定界。

        Args:
            appid: 目标应用 ID，例如 APP-A。
        """
        counter["query"] += 1
        n = counter["query"]
        await emit(st, run, "openops.tool.call.started",
                   message="巡检 · 指标查询" if n == 1 else "定界 · 拓扑依赖",
                   action="query_resource", payload={"tool": "query_resource", "appid": appid})
        r = await http_mcp_client.call_tool("query_resource", {"appid": appid})
        await emit(st, run, "openops.tool.call.succeeded",
                   message="P99 / 错误率 / Redis 连接数已取回" if n == 1 else "svc-payment-api 依赖图已取回",
                   action="query_resource", external_request_id=r["request_id"],
                   payload={"summary": r.get("result_summary", "")})
        st.rca = rca(1 if n == 1 else 2, "定界中" if n == 1 else "验证 H1")
        await emit(st, run, "openops.rca.updated",
                   message="RCA 面板更新（定界中）" if n == 1 else "假设排行更新（H1 领先）", payload=st.rca)
        return ToolResponse(content=[TextBlock(type="text", text=r.get("result_summary", "ok"))])

    async def recover_execute(appid: str, action: str) -> Any:
        """对指定应用执行恢复动作（如重启实例释放连接）。变更类操作，需人工批准后才会执行。

        Args:
            appid: 目标应用 ID。
            action: 恢复动作，例如 restart。
        """
        r = await http_mcp_client.call_tool("recover_execute", {"appid": appid, "action": action})
        await emit(st, run, "openops.tool.call.succeeded", message="恢复动作已执行（execution 受控追踪）",
                   action="recover_execute", external_request_id=r["request_id"],
                   payload={"execution_id": r.get("execution_id", "")})
        st.rca = rca(3, "已闭环")
        await emit(st, run, "openops.rca.updated", message="结论确认：H1 连接泄漏，已恢复", payload=st.rca)
        return ToolResponse(content=[TextBlock(type="text", text="recovered")])

    return Toolkit(tools=[
        FunctionTool(query_resource, name="query_resource", is_read_only=True),
        FunctionTool(recover_execute, name="recover_execute"),
    ])


def _permission_context() -> Any:
    """查询工具 allow（自动执行）；恢复工具 ask（暂停→桥到 OpenOps 审批）。

    对应 mcp_tool_annotation.is_approval_required；B4 由标注编译，B1 先固定两条规则。
    """
    from agentscope.permission import PermissionBehavior, PermissionContext, PermissionRule

    def _rule(name: str, behavior: Any) -> Any:
        return PermissionRule(tool_name=name, rule_content=None, behavior=behavior, source="platform")

    return PermissionContext(
        allow_rules={"query_resource": [_rule("query_resource", PermissionBehavior.ALLOW)]},
        ask_rules={"recover_execute": [_rule("recover_execute", PermissionBehavior.ASK)]},
    )


async def _handle_ask(st: TaskState, run: dict[str, Any], require_ev: Any) -> str:
    """恢复动作 ASK 门：建审批→发 approval.required→等 approval_ev（decide/cancel 置位）。

    返回 approved/rejected/timeout/cancelled；approval.{decision} 由 run_state_service.decide_approval 发。
    """
    appr = await runs.create_approval(
        st.user_id, str(run["agent_team_instance_id"]), st.run_id, st.task_id, "recover_execute",
        {"appid": "APP-A", "action": "restart", "target": "svc-payment-api/svc-a"},
        str(run["audit_trace_id"]), str(run["framework_session_id"]),
    )
    st.approval_id = str(appr["approval_request_id"])
    await emit(st, run, "openops.approval.required", severity="warning",
               message="恢复动作待批准：重启 svc-a 释放连接",
               payload={"approval_request_id": st.approval_id, "tool": "recover_execute",
                        "target": "APP-A · svc-payment-api/svc-a",
                        "impact": "重启期间 svc-a 短暂不可用（约 15s）"})
    try:
        await asyncio.wait_for(st.approval_ev.wait(), timeout=ASK_TIMEOUT_S)
    except asyncio.TimeoutError:
        await runs.expire_stale_approvals(st.run_id)
        st.approval_result = "timeout"
    return st.approval_result or "rejected"


async def _finish_cancel(st: TaskState, run: dict[str, Any]) -> None:
    if st.status != "cancelled":
        st.status = "cancelled"
    await emit(st, run, "openops.task.cancelled", severity="warning",
               message="任务已取消（Run 保持 active，可继续新任务）", action="task")


async def run_task(st: TaskState, run: dict[str, Any]) -> None:
    """真 AgentScope 驱动一次 Task：Agent(stub)+Toolkit+Permission；事件桥回 openops.*。"""
    _require_agentscope()
    from agentscope.agent import Agent
    from agentscope.event import ConfirmResult, RequireUserConfirmEvent, UserConfirmResultEvent
    from agentscope.message import Msg, TextBlock
    from agentscope.state import AgentState

    try:
        agent = Agent(
            name="sre-rca",
            system_prompt="你是资深 SRE 诊断 Agent：先巡检定界、给假设与验证，再提恢复动作；恢复类动作必须请求人工批准。",
            model=_build_model(st),
            toolkit=_build_toolkit(st, run),
            state=AgentState(session_id=str(run["framework_session_id"]), permission_context=_permission_context()),
        )
        inputs: Any = Msg(name="user", role="user", content=[TextBlock(type="text", text=st.input_text)])

        while True:
            require_ev = None
            async for ev in agent.reply_stream(inputs):
                if isinstance(ev, RequireUserConfirmEvent):
                    require_ev = ev
                    break  # 停止消费，去做审批握手，再以 UserConfirmResultEvent 恢复
            if st.status != "running":  # 外部 cancel
                return await _finish_cancel(st, run)
            if require_ev is None:
                break  # reply 正常结束

            decision = await _handle_ask(st, run, require_ev)
            if st.status != "running":
                return await _finish_cancel(st, run)
            confirmed = decision == "approved"
            if not confirmed:
                st.rca = rca(2, "验证 H1", {"conclusion": {
                    "rejected": "恢复动作被拒绝：保持观察，建议走短期配置优化。",
                    "timeout": "批准超时：恢复动作未执行，待人工跟进。",
                }.get(decision, "恢复动作未执行。")})
                await emit(st, run, "openops.rca.updated", message="恢复动作未执行", payload=st.rca)
                if decision == "timeout":
                    await emit(st, run, "openops.approval.timeout", severity="warning",
                               message="批准超时：恢复动作未执行", reason_code="APPROVAL_TIMEOUT")
            inputs = UserConfirmResultEvent(
                reply_id=require_ev.reply_id,
                confirm_results=[ConfirmResult(confirmed=confirmed, tool_call=tc) for tc in require_ev.tool_calls],
            )

        if st.status == "running":
            st.status = "completed"
            await emit(st, run, "openops.task.completed", message="任务完成：根因 H1，已按审批推进", action="task")
    except asyncio.CancelledError:
        await _finish_cancel(st, run)
        raise
