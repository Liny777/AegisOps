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
import logging
import os
import re
import uuid
from typing import Any

from infra.chart_contract import ChartContractError, chart_result_summary, normalize_chart_arguments
from runtime import events, tool_gateway
from infra.repositories import agent_session_states, runs
from runtime.emit import emit
from runtime.rca_demo import rca
from runtime.task_registry import TaskState
from runtime.tool_gateway import ToolBlocked

log = logging.getLogger("openops.runtime")
ASK_TIMEOUT_S = float(os.environ.get("OPENOPS_ASK_TIMEOUT_S", "300"))


def _clamped_env_int(name: str, dft: int, lo: int, hi: int) -> int:
    """治理 env 钳制（缺陷批连带 A）：模板路径有范围校验而 env 路径没有——
    D7 事故（tool_result_limit 160000>128k 窗口）可经 env 复现，这里堵死。"""
    try:
        v = int(os.environ.get(name, str(dft)))
    except ValueError:
        return dft
    if v < lo or v > hi:
        log.warning("[OpenOps][governance] %s=%s 越界，钳制到 [%s..%s]", name, v, lo, hi)
        return max(lo, min(hi, v))
    return v
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|Bearer\s+[A-Za-z0-9._\-]+)")


def _redact(msg: str) -> str:
    """脱敏错误信息：抹掉 key/token 样式片段并截断（SEC-001，不外泄凭证）。"""
    return _SECRET_RE.sub("[REDACTED]", msg)[:200]


def _final_text(agent: Any) -> str | None:
    """会话上下文里最后一条 assistant 文本 = 模型生成的结论。"""
    try:
        for m in reversed(agent.state.context):
            if m.role == "assistant":
                t = m.get_text_content()
                if t:
                    return t
    except Exception:  # pragma: no cover
        return None
    return None


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
            self.stream = True
            self.max_retries = 0
            self.retry_delay = 0.0
            self.context_size = 128000
            self.parameters = None
            self.credential = None

        @staticmethod
        async def _stream_response(content: list[Any], *, text: str | None = None):
            """复刻 AgentScope streaming 契约：增量块 is_last=False，末块给累计全文。"""
            if text is None:
                # 工具调用也先发一个增量块；最终累计块供 Agent 写上下文并进入 acting。
                yield ChatResponse(content=content, is_last=False)
                yield ChatResponse(content=content, is_last=True)
                return

            block_id = f"stub-text-{uuid.uuid4()}"
            # 固定多段，便于无凭证环境稳定验证浏览器在 RUN_FINISHED 前发生多次增长。
            chunks = ["已确认根因 H1（Redis 连接泄漏）：", "重启 svc-a 后连接回落、", "P99 恢复 210ms，事件闭环。"]
            for chunk in chunks:
                yield ChatResponse(
                    content=[TextBlock(type="text", id=block_id, text=chunk)],
                    is_last=False,
                )
                await asyncio.sleep(0.03)
            yield ChatResponse(
                content=[TextBlock(type="text", id=block_id, text=text)],
                is_last=True,
            )

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kw):  # noqa: ANN001
            self._step += 1
            # B8·补2：env 门控插入一步容器内诊断（只读→直接执行），证明 Bash 工具接进 agent 循环；
            # 默认关不改现有 demo 序列（recover 仍是第 3 步）；真 GLM 无论此开关都可自主调该工具。
            sbx_on = os.getenv("OPENOPS_DEMO_SANDBOX_STEP") == "1"
            if self._step in (1, 2):  # 巡检 + 定界
                return self._stream_response([ToolCallBlock(
                    type="tool_call", id=f"q{self._step}", name="query_resource",
                    input=json.dumps({"appid": "APP-A"}))])
            if sbx_on and self._step == 3:  # 容器内跑巡检 Skill（真 ZIP 投递 + 容器执行）
                return self._stream_response([ToolCallBlock(
                    type="tool_call", id="sk", name="run_platform_skill",
                    input=json.dumps({"skill_name": "inspection"}))])
            if sbx_on and self._step == 4:  # 容器内只读诊断命令
                return self._stream_response([ToolCallBlock(
                    type="tool_call", id="cmd", name="run_container_command",
                    input=json.dumps({"command": "ls -la"}))])
            if self._step == (5 if sbx_on else 3):  # 恢复动作（ask → 审批）
                return self._stream_response([ToolCallBlock(
                    type="tool_call", id="rec", name="recover_execute",
                    input=json.dumps({"appid": "APP-A", "action": "restart"}))])
            final_text = (
                "已确认根因 H1（Redis 连接泄漏）：重启 svc-a 后连接回落、"
                "P99 恢复 210ms，事件闭环。"
            )
            return self._stream_response([TextBlock(
                type="text",
                text=final_text,
            )], text=final_text)

    return StubRcaModel()


async def _build_model(st: TaskState) -> Any:
    """构建运行模型：平台模型（env Key）或用户自定义 LLM（PG 用户 Secret，构建边界瞬时解密）；否则 stub。

    API Key 只在此处取用、构建 credential 后即用即弃，绝不落 PG / 日志 / 事件 / 审计（SEC-001）。
    """
    spec = st.model_spec
    if not spec:
        print("[OpenOps][model] fallback to stub（无可用模型 spec）", flush=True)
        return _build_stub_model()
    api_key: str | None = None
    key_src = "-"
    if spec.get("is_user_llm"):  # 用户自定义 LLM（C2）：从 PG 用户 Secret 在构建边界瞬时解密
        api_key = await _decrypt_user_secret(str(spec["user_secret_ref_id"]))
        key_src = "user-secret"
    elif spec.get("secret_env_var"):  # 平台模型：从环境变量取 Key
        api_key = os.environ.get(spec["secret_env_var"])
        # 日志显示脱敏：该列应是环境变量名；若被误填成 Key 值（管理台实测有人填错），原样打出=泄密（SEC-001）
        env_name = str(spec["secret_env_var"])
        legal = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", env_name) is not None
        key_src = f"env:{env_name}" if legal else "env:<非环境变量名，疑似误填了 Key 本身，已隐去>"
    if api_key:
        import httpx

        from agentscope.credential import OpenAICredential
        from agentscope.model import OpenAIChatModel
        from infra.external.mcp_registry_client import console_tls_verify, http_trust_env

        # 构建目标一眼可见（同 [db]/[startup] 模式；不含 key 值）——DB base_url 错时这行即诊断
        print(f"[OpenOps][model] building {spec['model_id']} "
              f"base_url={spec.get('base_url') or 'default(api.openai.com)'} key={key_src} "
              f"trust_env={http_trust_env()}", flush=True)
        # 自定义 http_client：trust_env 默认 False——openai SDK 默认信任环境/Windows 注册表代理，
        # 内网 GLM 会被公司 SWG 劫走（实测返回 HIS Proxy 错误页而非模型响应）。超时对齐推理耗时。
        # max_retries 默认 0：openai SDK 默认重试 2 次，坏 base_url（内网实测 DB 里填了占位假地址）
        # 会 connect 超时×3 轮 ≈ 半分钟无反馈才 task.failed——失败要快到快透出。
        client_kwargs: dict[str, Any] = {
            "max_retries": int(os.environ.get("OPENOPS_MODEL_MAX_RETRIES", "0")),
            "http_client": httpx.AsyncClient(
                trust_env=http_trust_env(), verify=console_tls_verify(),
                timeout=httpx.Timeout(connect=float(os.environ.get("OPENOPS_MODEL_CONNECT_TIMEOUT_S", "10")),
                                      read=float(os.environ.get("OPENOPS_MODEL_READ_TIMEOUT_S", "300")),
                                      write=30.0, pool=10.0),
            ),
        }
        if spec.get("base_url"):
            client_kwargs["base_url"] = spec["base_url"]
        return OpenAIChatModel(
            credential=OpenAICredential(api_key=api_key),
            model=spec["model_id"],
            stream=True,
            client_kwargs=client_kwargs,
        )
    print(f"[OpenOps][model] fallback to stub（{spec['model_id']} 的 key 未取到：{key_src}）——"
          "管理台该字段填环境变量名（如 OPENOPS_PLATFORM_GLM_API_KEY），真实 Key 配到后端进程环境变量（run-backend）",
          flush=True)
    return _build_stub_model()


async def _decrypt_user_secret(secret_ref_id: str) -> str | None:
    """用户 LLM Secret 在模型构建边界瞬时解密（SEC-001：不落 PG/日志/事件/审计）。runtime→infra 合规。"""
    from infra import crypto
    from infra.repositories import secrets

    row = await secrets.get_secret(secret_ref_id)
    if row is None or row.get("status") != "active":
        return None
    try:
        return crypto.decrypt(row["ciphertext"])
    except ValueError:  # key 不匹配 / 密文损坏
        return None


_JSON_PY: dict[str, Any] = {"string": str, "integer": int, "number": float, "boolean": bool, "object": dict}


def _py_annotation(prop: dict[str, Any]) -> Any:
    """JSON schema 属性 → Python 注解（供 agentscope 从函数签名抽 tool schema）。"""
    if prop.get("type") == "array":
        item = (prop.get("items") or {}).get("type", "string")
        return list[_JSON_PY.get(item, str)]  # type: ignore[misc]
    return _JSON_PY.get(prop.get("type"), str)


async def _dynamic_mcp_specs() -> list[dict[str, Any]]:
    """OPENOPS_MCPREGISTRY=real 时，从注册表发现所有 server 的工具 → 动态工具 spec（含 server_url/只读/scope）。
    mock 或发现失败 → 空（不拖垮 demo 工具）。appid 约定（拍板 i）：inputSchema 有 project_id/appid → 该字段受 scope 约束。"""
    if os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() != "real":
        return []
    from infra.external import mcp_registry_client

    try:
        servers = await mcp_registry_client.list_servers()
    except Exception as e:  # noqa: BLE001 —— 发现失败不拖垮整个 run
        log.warning("MCP 注册表 list_servers 失败：%s", _redact(str(e)))
        return []
    specs: list[dict[str, Any]] = []
    for srv in servers:
        surl = srv.get("server_url")
        if not surl:
            continue
        try:
            tools = await mcp_registry_client.discover_tools(surl)
        except Exception as e:  # noqa: BLE001
            log.warning("发现 MCP 工具失败 server=%s：%s", srv.get("server_id"), _redact(str(e)))
            continue
        for t in tools:
            schema = t.get("input_schema") or {}
            props = schema.get("properties") or {}
            appid_prop = next((p for p in ("project_id", "appid", "app_id") if p in props), None)
            specs.append({
                "name": t["tool_name"], "description": t.get("description", ""),
                "input_schema": schema, "server_url": surl, "readonly": bool(t.get("readonly")),
                "scope_mode": "required" if appid_prop else "none",
                "appid_arg_path": f"$.{appid_prop}" if appid_prop else None,
            })
    return specs


def _make_dynamic_tool(st: TaskState, run: dict[str, Any], spec: dict[str, Any]) -> Any:
    """发现到的 MCP 工具 → agentscope FunctionTool：调用穿过 Tool Gateway（scope/审批/审计/28.2 头），
    按 server_url 经 console proxy 路由。用 __signature__ 让 agentscope 从中抽出参数 schema。"""
    import inspect

    from agentscope.message import TextBlock
    from agentscope.tool import FunctionTool, ToolResponse

    name, server_url = spec["name"], spec["server_url"]
    schema = spec.get("input_schema") or {}
    props: dict[str, Any] = schema.get("properties") or {}
    required = set(schema.get("required") or [])

    appid_field = (spec.get("appid_arg_path") or "").removeprefix("$.")

    async def _handler(**kwargs: Any) -> Any:
        args = {k: v for k, v in kwargs.items() if k in props and v not in (None, "", [], {})}
        # 联调便利：appid（如 project_id）受 scope 约束（拍板 i），但 GLM 常忘传/传空；当 scope 恰好 1 个 appid
        # 时自动补上（填的就是被允许的那个，不削弱 scope）。多 appid（真 oModel）时不补，交给模型自己选。
        if appid_field and not args.get(appid_field):
            allowed = (st.scope_ctx or {}).get("effective_appids", [])
            if len(allowed) == 1:
                args[appid_field] = allowed[0]
        try:
            r = await tool_gateway.invoke(st, run, name, args, server_url=server_url,
                                          started_msg=f"调用 {name}", succeeded_msg=f"{name} 返回")
        except ToolBlocked as e:
            # 只读查询被拦（如查询出 scope 的 APPID_OUT_OF_SCOPE）不算「恢复被拦」：没有恢复动作可抑制，
            # 模型转述拦截原因即可，任务收尾不该报「恢复动作被运行时拦截」；写类被拦才须抑制「已恢复」结论（B6-RT-001）
            if not spec.get("readonly"):
                st.tool_blocked = True
            return ToolResponse(content=[TextBlock(type="text", text=f"工具被拦截：{e.reason_code} {e.message}")])
        return ToolResponse(content=[TextBlock(type="text", text=str(r.get("result_summary", "ok")))])

    params, doc = [], [spec.get("description", name), "", "Args:"]
    for pname, prop in props.items():
        default = prop.get("default", inspect.Parameter.empty if pname in required else "")
        params.append(inspect.Parameter(pname, inspect.Parameter.KEYWORD_ONLY,
                                        default=default, annotation=_py_annotation(prop)))
        doc.append(f"    {pname}: {prop.get('description', '')}")
    _handler.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    _handler.__doc__ = "\n".join(doc)
    _handler.__name__ = name
    return FunctionTool(_handler, name=name, description=spec.get("description", name),
                        is_read_only=bool(spec.get("readonly")))


async def _build_toolkit(st: TaskState, run: dict[str, Any]) -> Any:
    """工具统一走 runtime.tool_gateway（B4：标注/APPID/Secret/header/审计）；RCA 卡更新留在工具内。

    标注裁剪：仅注册标注 status=allowed 的工具；Gateway 内再做二次校验（纵深防御）。
    动态 MCP（OPENOPS_MCPREGISTRY=real）：注册表发现的 server 工具也在此装配，同样穿 Gateway。
    """
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
        try:
            r = await tool_gateway.invoke(
                st, run, "query_resource", {"appid": appid},
                started_msg="巡检 · 指标查询" if n == 1 else "定界 · 拓扑依赖",
                succeeded_msg="P99 / 错误率 / Redis 连接数已取回" if n == 1 else "svc-payment-api 依赖图已取回",
            )
        except ToolBlocked as e:  # tool.blocked 已发；把失败回给模型收口
            st.tool_blocked = True  # B6-RT-001
            return ToolResponse(content=[TextBlock(type="text", text=f"工具被拦截：{e.reason_code} {e.message}")])
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
        try:
            r = await tool_gateway.invoke(
                st, run, "recover_execute", {"appid": appid, "action": action},
                started_msg="执行恢复动作（已批准）",
                succeeded_msg="恢复动作已执行（execution 受控追踪）",
            )
        except ToolBlocked as e:
            st.tool_blocked = True  # B6-RT-001：软处理拦截也要抑制「已恢复」结论
            return ToolResponse(content=[TextBlock(type="text", text=f"工具被拦截：{e.reason_code} {e.message}")])
        st.rca = rca(3, "已闭环")
        await emit(st, run, "openops.rca.updated", message="结论确认：H1 连接泄漏，已恢复", payload=st.rca)
        return ToolResponse(content=[TextBlock(type="text", text="recovered")])

    async def run_container_command(command: str) -> Any:
        """在你的隔离容器内执行一条 shell 命令做诊断/巡检（如查看日志、进程、磁盘）。只读命令直接执行，
        写类命令需人工批准，危险命令（rm -rf /、chmod 777 等）会被拒绝。破坏面仅限你自己的容器。

        Args:
            command: 要执行的 shell 命令，例如 `ls -la` 或 `cat /var/log/app.log | tail -n 50`。
        """
        from runtime.sandbox_bash import run_container_command as _run_cmd  # 局部导入避免环

        text = await _run_cmd(st, run, command)
        return ToolResponse(content=[TextBlock(type="text", text=text)])

    async def run_platform_skill(skill_name: str) -> Any:
        """执行一个已装配到当前实例的 Skill（平台默认或你绑定的用户 Skill）。Skill 在你的隔离容器内
        经受控执行，返回结构化结果。未装配的 Skill 会被拒绝。

        Args:
            skill_name: Skill 名称，例如 `inspection`。
        """
        from runtime.sandbox_skill import run_bound_skill  # 局部导入避免环

        text = await run_bound_skill(st, run, skill_name)
        return ToolResponse(content=[TextBlock(type="text", text=text)])

    async def list_scope_apps() -> Any:
        """列出当前会话工作范围（scope）内可用的应用（appid）。用户问「我有哪些应用 / 能查哪些应用」时用此工具。"""
        appids = (st.scope_ctx or {}).get("effective_appids", [])
        if appids:
            text = (f"当前工作范围内共 {len(appids)} 个应用（appid）：\n"
                    + "\n".join(f"- {a}" for a in appids)
                    + "\n（各查询工具的应用参数必须取自此范围）")
        else:
            text = "当前工作范围为空（无可用应用）。"
        return ToolResponse(content=[TextBlock(type="text", text=text)])

    async def render_chart(
        chart_type: str,
        title: str,
        series: list[dict[str, Any]],
        description: str = "",
        unit: str = "",
    ) -> Any:
        """把已经取得的数值展示为受控图表；这是纯展示工具，不查询数据也不执行变更。

        仅当折线图、柱状图或饼图能明显提升可读性时调用，数值必须来自本轮已取得的数据，
        不得臆造。series 的严格形状为
        [{"name": "序列名", "data": [{"label": "横轴/扇区标签", "value": 12.3}]}]；
        line/bar 的各序列必须使用相同标签顺序，pie 只能有一个非负序列。不支持颜色、样式或 HTML。

        Args:
            chart_type: 图表类型，只能是 line、bar 或 pie。
            title: 简短图表标题。
            series: 数值序列，使用上述固定 JSON 形状。
            description: 可选的一句话数据口径或结论。
            unit: 可选的短单位，例如 ms、%、个。
        """
        try:
            chart = normalize_chart_arguments({
                "chart_type": chart_type,
                "title": title,
                "description": description,
                "unit": unit,
                "series": series,
            })
        except ChartContractError as exc:
            return ToolResponse(content=[TextBlock(
                type="text",
                text=f"图表参数未通过校验：{exc}。请按工具说明修正，且不要补充样式或 HTML。",
            )])

        summary = chart_result_summary(chart)
        tool_call_id = str(uuid.uuid4())
        await emit(
            st,
            run,
            "openops.tool.call.started",
            action="render_chart",
            message=f"生成图表 · {chart['title']}",
            payload={"tool": "render_chart", "tool_call_id": tool_call_id, "arguments": chart},
        )
        await emit(
            st,
            run,
            "openops.tool.call.succeeded",
            action="render_chart",
            message=summary,
            payload={"tool": "render_chart", "tool_call_id": tool_call_id, "result_summary": summary},
        )
        return ToolResponse(content=[TextBlock(type="text", text=summary)])

    # 动态 MCP 工具先发现（决定 demo 双工具去留）：有真工具时 demo 退场——不再弹假审批卡/脚本 RCA。
    dynamic_specs = await _dynamic_mcp_specs()
    _demo_env = os.environ.get("OPENOPS_DEMO_TOOLS", "").strip()  # 1=恒开 0=恒关 未设=自动
    keep_demo = _demo_env == "1" or (_demo_env != "0" and not dynamic_specs)
    fns = {"query_resource": (query_resource, True), "recover_execute": (recover_execute, False)} if keep_demo else {}
    anns = st.tool_annotations or {}
    tools = []
    pruned: list[tuple[str, str]] = []  # (tool_name, reason_code) —— 裁剪也要有审计（B6-RT-001③）
    for name, (fn, readonly) in fns.items():
        ann = anns.get(name)
        if ann is not None and ann.get("status") == "allowed":  # 标注裁剪（28.2）
            tools.append(FunctionTool(fn, name=name, is_read_only=readonly))
        elif st.template_tools is not None and name not in st.template_tools:
            pruned.append((name, "TOOL_BLOCKED"))  # B7·二：模板未绑定（空集=零平台工具，B7-SEC-001）
        else:
            pruned.append((name, "TOOL_NOT_ANNOTATED" if ann is None else "TOOL_BLOCKED"))
    # 容器内受控 Bash 工具（B8·补2）：始终可用（会话容器就位），命令级四层裁决在 run_bash 内，
    # 工具级 agentscope 权限设为 allow（tool 本身受控，逐命令再裁决）。
    tools.append(FunctionTool(run_container_command, name="run_container_command", is_read_only=False))
    # Skill 作 agent 工具（C1）：装配集校验 + 真 ZIP 投递 + 容器内执行在 run_bound_skill 内。
    # description 动态注入装配集（同 _make_dynamic_tool 的 description 覆盖模式）：LLM 必须"知道"有哪些
    # skill_key 合法，否则零感知（实测问「介绍 alarm-query」只答同名 MCP）且易传错名被 fail-closed。
    _sk = st.available_skills or {}
    _sk_names = "、".join(
        f"`{k}`（{v.get('display_name')}）" if v.get("display_name") and v.get("display_name") != k else f"`{k}`"
        for k, v in _sk.items()
    ) or "（当前实例未装配任何 Skill）"
    _sk_desc = (f"执行一个已装配到当前实例的 Skill（在你的隔离容器内受控执行，返回结构化结果）。"
                f"当前可用 Skill：{_sk_names}。用户消息以 `/<Skill名>` 开头即要求优先执行对应 Skill。"
                f"skill_name 必须取自上述清单（取反引号内的名字），未装配会被拒绝。")
    tools.append(FunctionTool(run_platform_skill, name="run_platform_skill", description=_sk_desc, is_read_only=False))
    # 真工具 list_scope_apps：读 st.scope_ctx（本地、只读、无出站）——「我有哪些应用」有真答案，GLM 不再乱够工具
    st.tool_annotations = dict(st.tool_annotations or {})
    st.tool_annotations["list_scope_apps"] = {"is_approval_required": False, "is_secret_required": False,
                                              "scope_mode": "none", "appid_arg_path": None, "status": "allowed"}
    if isinstance(st.template_tools, set):
        st.template_tools.add("list_scope_apps")
    tools.append(FunctionTool(list_scope_apps, name="list_scope_apps", is_read_only=True))
    # Generative UI 图表：仅主 Agent 可投影到主对话；子 Agent 先把数据汇报给主 Agent，避免
    # 并行子任务的工具事件交错后把图卡插进错误轮次。该工具只读、无出站，前端按固定 schema 渲染。
    if st.agent_key == "main":
        st.tool_annotations["render_chart"] = {"is_approval_required": False, "is_secret_required": False,
                                                "scope_mode": "none", "appid_arg_path": None, "status": "allowed"}
        if isinstance(st.template_tools, set):
            st.template_tools.add("render_chart")
        tools.append(FunctionTool(
            render_chart,
            name="render_chart",
            description=("把本轮已经取得的数值展示成受控 line/bar/pie 图表；只传 title、description、unit "
                         "和 series[{name,data[{label,value}]}]，不得臆造数据或传 style/HTML。"),
            is_read_only=True,
        ))
    # D 块：派发工具（仅 main 且模板配了 sub_agents；子 st.sub_agents 恒 None → 天然 1 层）
    if st.agent_key == "main" and st.sub_agents:
        from runtime import subagent_dispatch

        _roles = "、".join(f"`{s['key']}`（{s.get('label', s['key'])}：{str(s.get('role', ''))[:60]}）"
                           for s in st.sub_agents)

        async def dispatch_subagents(tasks: list[dict[str, Any]]) -> Any:
            """并行派发子 Agent 执行子任务，全部完成后返回各自汇报。

            Args:
                tasks: 派发清单，每项 {"role": 角色key, "task": 子任务描述}；一次最多 5 个。
            """
            text = await subagent_dispatch.dispatch(st, run, tasks)
            return ToolResponse(content=[TextBlock(type="text", text=text)])

        _dp_desc = (f"把独立子任务并行派发给专职子 Agent（各自只带本角色的工具，只读执行，完成后汇报）。"
                    f"可用角色：{_roles}。互不依赖的查询应一次派一批以并行提效；"
                    f"派发会阻塞至全部子 Agent 完成或超时，汇报在返回值中。")
        # DEF-2：is_concurrency_safe=False → Agent 层归 sequential 执行（防模型一轮
        # 双 dispatch 并发跑预算读写竞态）
        tools.append(FunctionTool(dispatch_subagents, name="dispatch_subagents",
                                  description=_dp_desc, is_read_only=True, is_concurrency_safe=False))
        st.tool_annotations["dispatch_subagents"] = {"is_approval_required": False, "is_secret_required": False,
                                                     "scope_mode": "none", "appid_arg_path": None, "status": "allowed"}
        if isinstance(st.template_tools, set):
            st.template_tools.add("dispatch_subagents")
    # 动态 MCP 工具（OPENOPS_MCPREGISTRY=real）：注册表发现的真 server 工具（如 alarm-server），穿 Tool Gateway
    # 路由到各 server_url。注入标注：只读→免审批、写类→ASK；有 project_id/appid → scope 受限（拍板 i）。
    for spec in dynamic_specs:
        # per-agent 隔离（D/E 块，编排对称化）：动态工具对 main/sub 统一按白名单裁剪——
        # main 按模板 main.default_tools，子 Agent 按画像 mcp_tools（否则每个 Agent 都看到
        # 注册表全部真工具，角色隔离被击穿）。空白名单=纯编排者（main 被迫派发，老 D6 效果）；
        # main 需要直连的动态工具须在模板编辑器勾进 default_tools（先 allowed 标注）。
        if st.template_tools is None or spec["name"] not in st.template_tools:
            continue
        st.tool_annotations[spec["name"]] = {
            "is_approval_required": not spec["readonly"], "is_secret_required": False,
            "scope_mode": spec["scope_mode"], "appid_arg_path": spec["appid_arg_path"], "status": "allowed",
            "origin": "dynamic",  # gateway：catalog 未标注行不推翻此注入（管理员显式标注才接管）
        }
        tools.append(_make_dynamic_tool(st, run, spec))
    return Toolkit(tools=tools), pruned


def _permission_context(st: TaskState) -> Any:
    """由标注编译 permission（B4）：allowed+is_approval_required→ask（暂停→桥到 OpenOps 审批）；
    allowed 免审批→allow（自动执行）。blocked/未标注的工具已在 Toolkit 裁剪，不给规则。
    """
    from agentscope.permission import PermissionBehavior, PermissionContext, PermissionRule

    def _rule(name: str, behavior: Any) -> Any:
        return PermissionRule(tool_name=name, rule_content=None, behavior=behavior, source="platform")

    allow: dict[str, list[Any]] = {}
    ask: dict[str, list[Any]] = {}
    for name, ann in (st.tool_annotations or {}).items():
        if ann.get("status") != "allowed":
            continue
        if ann.get("is_approval_required"):
            ask[name] = [_rule(name, PermissionBehavior.ASK)]
        else:
            allow[name] = [_rule(name, PermissionBehavior.ALLOW)]
    # 容器内 Bash 工具（B8·补2）：tool 级 allow（受控工具），命令级四层裁决/审批在 run_bash 内做
    allow["run_container_command"] = [_rule("run_container_command", PermissionBehavior.ALLOW)]
    # Skill 工具（C1）：tool 级 allow，装配集校验/checksum 在 run_bound_skill 内做
    allow["run_platform_skill"] = [_rule("run_platform_skill", PermissionBehavior.ALLOW)]
    return PermissionContext(allow_rules=allow, ask_rules=ask)


async def _handle_ask(st: TaskState, run: dict[str, Any], require_ev: Any) -> str:
    """恢复动作 ASK 门：建审批→发 approval.required→等 approval_ev（decide/cancel 置位）。

    返回 approved/rejected/timeout/cancelled；approval.{decision} 由 run_state_service.decide_approval 发。
    """
    # 审批卡按真实工具呈现（tool_calls[0].name/.input）；demo recover_execute 保留剧本文案（叙事一致）
    tcs = list(getattr(require_ev, "tool_calls", None) or [])
    tool_name = tcs[0].name if tcs else "unknown_tool"
    try:
        tool_args = json.loads(tcs[0].input) if tcs and tcs[0].input else {}
    except Exception:  # noqa: BLE001 —— 模型给的原始 JSON 串可能不完整
        tool_args = {}
    if tool_name == "recover_execute":
        tool_args = {"appid": "APP-A", "action": "restart", "target": "svc-payment-api/svc-a"}
        ask_msg = "恢复动作待批准：重启 svc-a 释放连接"
        target = "APP-A · svc-payment-api/svc-a"
        impact = "重启期间 svc-a 短暂不可用（约 15s）"
    else:
        ask_msg = f"写类操作待批准：{tool_name}"
        target = str(tool_args.get("project_id") or tool_args.get("appid") or tool_args.get("app_id") or "—")
        impact = "变更类操作，批准后才会执行"
    from infra.redact import redact_args as _redact_args  # 连带 D：入参进审批行前 key 级脱敏
    args = _redact_args(tool_args)  # 同一份脱敏入参：入审批行 + 进事件 payload（真实 MCP 工具入参在此路径才完整）
    appr = await runs.create_approval(
        st.user_id, str(run["agent_team_instance_id"]), st.run_id, st.task_id, tool_name,
        args, str(run["audit_trace_id"]), str(run["framework_session_id"]),
    )
    st.approval_ev.clear()  # 复用同一 asyncio.Event：等待前清位+清旧结果，避免上一次 ASK（如容器 Bash 审批）的
    st.approval_result = None  # set 未清 → 本次 wait() 立即返回并读到陈旧决策（与 sandbox_bash 同规矩）
    st.approval_id = str(appr["approval_request_id"])
    # payload 带 args（脱敏入参字典）供审批卡逐项展示；保留 target/impact 兼容旧前端
    await emit(st, run, "openops.approval.required", severity="warning",
               message=ask_msg,
               payload={"approval_request_id": st.approval_id, "tool": tool_name, "args": args,
                        "target": target, "impact": impact})
    try:
        await asyncio.wait_for(st.approval_ev.wait(), timeout=ASK_TIMEOUT_S)
    except asyncio.TimeoutError:
        from runtime.emit import expire_stale_approvals_and_audit as _exp
        await _exp(st.run_id, force_approval_id=st.approval_id)  # 循环超时 ⇒ 本行必达 timeout
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
    from agentscope.event import (
        ConfirmResult,
        ModelCallEndEvent,
        ModelCallStartEvent,
        RequireUserConfirmEvent,
        TextBlockDeltaEvent,
        UserConfirmResultEvent,
    )
    from agentscope.message import Msg, TextBlock
    from agentscope.state import AgentState

    agent = None  # P3：finally 回写引用；toolkit 构建抛错时保持 None
    try:
        toolkit, pruned = await _build_toolkit(st, run)
        for name, reason in pruned:  # 裁剪审计对齐 mock（B6-RT-001③）：未标注/blocked 工具运行前即留痕
            st.tool_blocked = True  # 任何工具被裁剪都不得宣称闭环（保守 fail-closed）
            await emit(st, run, "openops.tool.blocked", severity="warning", action=name,
                       message=f"工具 {name} 未进入运行工具集（{'未标注' if reason == 'TOOL_NOT_ANNOTATED' else '已拉黑'}），运行时 fail-closed",
                       reason_code=reason, payload={"tool": name, "phase": "toolkit_build"})
        # P3：同 run 跨 task 记忆连续性——按 (framework_session_id,'main') 恢复 AgentState
        # （含 context 消息历史）；permission_context 必须覆盖为本 task 规则（标注可能已热更新）。
        # 旧库未迁移/首个 task → 全新 state。
        fsid = str(run["framework_session_id"])
        agent_state = None
        try:
            saved = await agent_session_states.get_state_json(fsid, "main")
            if saved:
                agent_state = AgentState.model_validate(saved)
                agent_state.permission_context = _permission_context(st)
        except Exception:  # noqa: BLE001 —— 状态损坏/schema 漂移：放弃恢复，全新开始（不阻断任务）
            log.warning("[OpenOps][session-state] AgentState 恢复失败，本 task 从空状态开始 session=%s", fsid)
            agent_state = None
        if agent_state is None:
            agent_state = AgentState(session_id=fsid, permission_context=_permission_context(st))
        try:  # E4：治理 config（2.0.3 公开导出优先，私有路径兜底）
            from agentscope.agent import ContextConfig, ModelConfig, ReActConfig
        except ImportError:  # pragma: no cover
            from agentscope.agent._config import ContextConfig, ModelConfig, ReActConfig
        agent = Agent(
            name="sre-rca",
            system_prompt=("你是资深 SRE 诊断 Agent：先巡检定界、给假设与验证，再提恢复动作；"
                           "恢复类动作必须请求人工批准。只有当本轮已取得的数值适合做趋势或对比时，"
                           "才调用 render_chart 提升可读性；不得为图表臆造数据。"),
            model=await _build_model(st),
            toolkit=toolkit,
            state=agent_state,
            # E4 治理（limits-and-budgets）：max_iters 防失控狂转；tool_result_limit 必须 < 模型窗口
            # （D7 事故：160000>128000 单条工具结果撑爆窗口→压缩 fallback 删掉用户问题）。
            # 2.0.3 默认 20/50000；主 agent 对齐老经验 ≤1/4 窗口取 24000。
            react_config=ReActConfig(max_iters=_clamped_env_int("OPENOPS_MAIN_MAX_ITERS", 20, 1, 200)),
            context_config=ContextConfig(
                tool_result_limit=_clamped_env_int("OPENOPS_MAIN_TOOL_RESULT_LIMIT", 24000, 1000, 200000)),
            # agent-loop 模型重试（默认 0=现状单次；网关 LB 节点漂移致间歇 401 时，设 >0 换连接重试
            # 可能命中好节点。上限 3 防慢失败。openai SDK 层 max_retries 是另一档，见 _build_model）
            model_config=ModelConfig(max_retries=_clamped_env_int("OPENOPS_MODEL_LOOP_RETRIES", 0, 0, 3)),
        )
        inputs: Any = Msg(name="user", role="user", content=[TextBlock(type="text", text=st.input_text)])
        recovery_denied = False  # 恢复被拒绝/超时/取消 → 不让模型最终文本覆盖「未执行」结论（B2-RUNTIME-001）
        fallback_conclusion = None  # st.rca 为 None（真流程无 demo 面板）时结论的落审计通道（task.completed payload）

        while True:
            require_ev = None
            async for ev in agent.reply_stream(inputs):
                if isinstance(ev, ModelCallStartEvent):
                    await emit(st, run, "openops.model.call.started", action="model_call",
                               message=f"模型推理中（{ev.model_name}）", payload={"model": ev.model_name})
                elif isinstance(ev, ModelCallEndEvent):
                    await emit(st, run, "openops.model.call.succeeded", action="model_call", message="模型推理完成",
                               payload={"input_tokens": ev.input_tokens, "output_tokens": ev.output_tokens})
                elif isinstance(ev, TextBlockDeltaEvent):
                    # 助手文本增量（B5）：只发 SSE 供 AG-UI 流翻译成 TEXT_MESSAGE_*，不写审计（增量非事实）
                    events.publish(st.run_id, events.envelope(
                        st.run_id, "openops.assistant.delta", task_id=st.task_id,
                        payload={"delta": ev.delta, "message_id": ev.block_id},
                    ))
                elif isinstance(ev, RequireUserConfirmEvent):
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
                recovery_denied = True
                fallback_conclusion = {
                    "rejected": "恢复动作被拒绝：保持观察，建议走短期配置优化。",
                    "timeout": "批准超时：恢复动作未执行，待人工跟进。",
                }.get(decision, "恢复动作未执行。")
                # 仅 demo 恢复流有面板可更新（st.rca 只有 demo 工具会设）；真流程不得用 rca_demo 剧本造假面板
                # ——曾在真对话结束时弹出「支付延迟突增/H1 连接泄漏」假 RCA 卡（与「根因 H1」误报同族）
                if st.rca:
                    st.rca = {**st.rca, "conclusion": fallback_conclusion}
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
            if recovery_denied:
                # 恢复被拒绝/超时：保留「未执行」结论，不被模型最终文本覆盖（B2-RUNTIME-001）
                msg = "任务结束：恢复动作未执行（按用户决策），保持观察"
            elif st.tool_blocked:
                # 写类工具被运行时拦截（标注热更新/未标注/模板未绑定）：不得采纳模型「已恢复」文本（B6-RT-001）。
                msg = "任务结束：恢复动作被运行时拦截（工具标注变更/未标注/模板未绑定），未执行——请管理员复核配置"
                fallback_conclusion = "恢复动作被运行时拦截（工具标注变更/未标注/模板未绑定），未执行；请管理员复核配置后重试。"
                # st.rca 可能为 None（真流程/空模板全量剪枝，B7-SEC-001）——结论经 task.completed payload 照样
                # 落审计；但不得用 rca_demo 剧本骨架造假面板（曾在真对话结束时弹出假「RCA 决策面板」卡）
                if st.rca:
                    st.rca = {**st.rca, "conclusion": fallback_conclusion}
                    await emit(st, run, "openops.rca.updated", message="恢复动作被拦截，未执行", payload=st.rca)
            else:
                # 已执行恢复（或本就无需 ASK）：采纳模型生成的结论（GLM 真实结论 / stub 脚本结论）
                conclusion = _final_text(agent)
                if conclusion and st.rca:
                    st.rca = {**st.rca, "conclusion": conclusion}
                    await emit(st, run, "openops.rca.updated", message="结论已更新（模型生成）", payload=st.rca)
                # 「根因 H1…」是 demo 剧本文案：仅 demo 恢复流真跑过（st.rca 只有 demo 工具会设）才用；
                # 真工具运行一律中性「任务完成」（曾在真对话里误报"已按审批执行恢复"）
                msg = "任务完成：根因 H1，已按审批执行恢复" if st.rca else "任务完成"
            payload = ({"conclusion": st.rca.get("conclusion")} if st.rca
                       else {"conclusion": fallback_conclusion} if fallback_conclusion else None)
            await emit(st, run, "openops.task.completed", action="task", message=msg, payload=payload)
    except asyncio.CancelledError:
        await _finish_cancel(st, run)
        raise
    except Exception as e:  # 模型/工具异常：结构化脱敏错误 + 任务失败（不外泄凭证；B2-LOG-001 降噪不打堆栈）
        reason = _redact(str(e))  # 已挡 sk-/Bearer 形串；上游「401 Invalid API key」等非密文原样透出
        log.warning("agentscope run_task failed task=%s run=%s: %s", st.task_id, st.run_id, reason)
        st.status = "failed"
        # 真原因进 message（活动栏 detail 只读 message）+ payload（全文）——此前 message 是干巴巴
        # 「模型调用失败」、task.failed 连 payload/reason_code 都没有，用户只能翻后端日志（内网教训）
        await emit(st, run, "openops.model.call.failed", severity="error", action="model_call",
                   message=f"模型调用失败：{reason[:160]}", reason_code="MODEL_CALL_FAILED",
                   payload={"error": reason})
        await emit(st, run, "openops.task.failed", severity="error", action="task",
                   message=f"任务失败：{reason[:160]}", reason_code="MODEL_CALL_FAILED",
                   payload={"error": reason})
    finally:
        # P3：终态回写 AgentState（completed/failed/cancelled 均落）——同 run 下一个 task 恢复记忆
        if agent is not None:
            try:
                import json as _json

                dump = agent.state.model_dump(mode="json")
                if len(_json.dumps(dump, ensure_ascii=False)) > 2_000_000:
                    log.warning("[OpenOps][session-state] state_json 超 2MB（考虑 offload/压缩）session=%s",
                                run["framework_session_id"])
                await agent_session_states.upsert_state_json(
                    str(run["framework_session_id"]), dump, "main", st.user_id)
            except Exception:  # noqa: BLE001 —— 旧库未迁移/序列化异常不阻断终态收口
                log.warning("[OpenOps][session-state] AgentState 回写失败 session=%s", run["framework_session_id"])
