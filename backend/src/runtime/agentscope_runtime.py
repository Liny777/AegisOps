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

from domain import tool_key
from infra.chart_contract import ChartContractError, chart_result_summary, normalize_chart_arguments
from infra.rca_contract import RcaBoardContractError
import studio  # Agent Studio 垂直切片：只经 facade（src/studio/__init__.py），不碰其内部结构
from runtime import diagnosis_checkpoint, events, tool_gateway
from infra.repositories import agent_session_states, runs
from runtime.emit import emit
from runtime.rca_board import apply_board_update, board_owner, reopen_with_conclusion
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
    from agentscope.message import TextBlock, ThinkingBlock, ToolCallBlock
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
            # 段间停顿要够长：太快（如 30ms）会被 SSE→sidecar→CopilotKit 渲染管线合并，浏览器只见
            # 一次到位，既看不出流式、也让「完成前多次增长」的验收无法稳定观测。真实模型逐 token 到达
            # 本就有节奏，此处只补齐无凭证 stub 的可感知节奏。
            for chunk in chunks:
                yield ChatResponse(
                    content=[TextBlock(type="text", id=block_id, text=chunk)],
                    is_last=False,
                )
                await asyncio.sleep(0.4)
            yield ChatResponse(
                content=[TextBlock(type="text", id=block_id, text=text)],
                is_last=True,
            )

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kw):  # noqa: ANN001
            self._step += 1
            # B8·补2：env 门控插入一步容器内诊断（只读→直接执行），证明 Bash 工具接进 agent 循环；
            # 默认关不改现有 demo 序列（recover 仍是第 3 步）；真 GLM 无论此开关都可自主调该工具。
            sbx_on = os.getenv("OPENOPS_DEMO_SANDBOX_STEP") == "1"
            # A6：env 门控插入两次 update_diagnosis_board（恢复动作前 step=2、恢复后 step=5+completed），
            # 默认关不改现有序列——专供 test_agui 全链路断言「模型自报 → rca_board → rca.updated」真链路。
            board_on = os.getenv("OPENOPS_DEMO_BOARD_STEP") == "1"
            if self._step in (1, 2):  # 巡检 + 诊断
                blocks: list[Any] = []
                if self._step == 1:  # 首步附一段思考：演示 reasoning 折叠卡（真模型经 reasoning_content 天然产生）
                    blocks.append(ThinkingBlock(thinking=(
                        "先给 svc-payment-api 做巡检：拉指标与依赖拓扑，重点看 P99 与下游 Redis 连接数。"
                        "初判 P99 升高疑似 Redis 连接饱和——先 query 指标定位，再验证假设 H1（连接泄漏）。"
                    )))
                blocks.append(ToolCallBlock(
                    type="tool_call", id=f"q{self._step}", name="query_resource",
                    input=json.dumps({"appid": "APP-A"})))
                return self._stream_response(blocks)
            if sbx_on and self._step == 3:  # 容器内跑巡检 Skill（真 ZIP 投递 + 容器执行）
                return self._stream_response([ToolCallBlock(
                    type="tool_call", id="sk", name="run_platform_skill",
                    input=json.dumps({"skill_name": "inspection"}))])
            if sbx_on and self._step == 4:  # 容器内只读诊断命令
                return self._stream_response([ToolCallBlock(
                    type="tool_call", id="cmd", name="run_container_command",
                    input=json.dumps({"command": "ls -la"}))])
            base = 5 if sbx_on else 3  # 无 board 门控时恢复动作所在步
            if board_on and self._step == base:  # 诊断中自报进度（step=2 证据）
                return self._stream_response([ToolCallBlock(
                    type="tool_call", id="bd1", name="update_diagnosis_board",
                    input=json.dumps({
                        "step": 2, "title": "支付下单 P99 突增",
                        "current_question": "Redis 连接饱和是慢查询还是连接泄漏导致？",
                        "facts": [{"text": "P99 180ms→1.4s，错误率 0.6%"},
                                  {"text": "svc-payment-api → Redis 连接打满（1000/1000）"}],
                        "hypotheses": [{"text": "H1 Redis 连接泄漏", "tag": "支持",
                                        "tagTone": "good", "conf": 0.72}],
                    }))])
            if self._step == (base + 1 if board_on else base):  # 恢复动作（ask → 审批）
                return self._stream_response([ToolCallBlock(
                    type="tool_call", id="rec", name="recover_execute",
                    input=json.dumps({"appid": "APP-A", "action": "restart"}))])
            if board_on and self._step == base + 2:  # 诊断收尾（step=5 完成 + conclusion）
                return self._stream_response([ToolCallBlock(
                    type="tool_call", id="bd2", name="update_diagnosis_board",
                    input=json.dumps({
                        "step": 5, "step_completed": True,
                        "conclusion": "已确认 H1（Redis 连接泄漏）：重启 svc-a 后连接回落、P99 恢复 210ms。",
                    }))])
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
    """构建运行模型：平台模型或用户自定义 LLM——两者的 Key 都是 PG 密文，构建边界瞬时解密；否则 stub。

    API Key 只在此处取用、构建 credential 后即用即弃，绝不落日志 / 事件 / 审计（SEC-001）。
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
    elif spec.get("model_asset_id"):  # 平台模型：从 PG 资产密文列在构建边界瞬时解密
        api_key, fp = await _decrypt_asset_secret(str(spec["model_asset_id"]))
        # 只打不可逆指纹——密文/明文都不进日志（原 env 分支那套「疑似误填 Key 已隐去」的脱敏
        # 已随环境变量口径一并废弃：密文列不存在被误填明文的可能）
        key_src = f"db:{fp or '未配置'}"
    if api_key:
        import httpx

        from agentscope.credential import OpenAICredential
        from agentscope.model import OpenAIChatModel
        from infra.external.mcp_registry_client import console_tls_verify, http_trust_env
        from runtime import inline_think

        # 构建目标一眼可见（同 [db]/[startup] 模式；不含 key 值）——DB base_url 错时这行即诊断
        # 自定义 header 只打条数不打名值（值可能是租户/路由凭据，SEC-001 同口径）
        extra_headers: dict[str, str] = spec.get("extra_headers") or {}
        # 内联 think 切流（网关把思考混在 content 里、只吐 </think> 闭标签时按 model_id 白名单开启）
        inline = inline_think.enabled_for(spec["model_id"])
        print(f"[OpenOps][model] building {spec['model_id']} "
              f"base_url={spec.get('base_url') or 'default(api.openai.com)'} key={key_src} "
              f"extra_headers={len(extra_headers)} trust_env={http_trust_env()} "
              f"inline_think={'on' if inline else 'off'}", flush=True)
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
        if extra_headers:
            # 用户高级选项自定义 Header（内网网关路由/租户标识等）：openai SDK 每请求附带。
            # 与「测试连接」探测（llm_provider_client）同源，避免测通了但真跑缺头。
            client_kwargs["default_headers"] = dict(extra_headers)
        # 白名单命中 → 切流子类（只覆写 _call_api，构造参数与基类逐字相同）
        model_cls = inline_think.patched_model_cls() if inline else OpenAIChatModel
        return model_cls(
            credential=OpenAICredential(api_key=api_key),
            model=spec["model_id"],
            stream=True,
            client_kwargs=client_kwargs,
        )
    print(f"[OpenOps][model] fallback to stub（{spec['model_id']} 的 key 未取到：{key_src}）——"
          "请在管理台「模型资产」里编辑该模型并填写 API Key（保存后加密入库，下一个 run 即生效）",
          flush=True)
    return _build_stub_model()


async def _decrypt_asset_secret(model_asset_id: str) -> tuple[str | None, str | None]:
    """平台模型 Key 在模型构建边界瞬时解密（SEC-001：不落日志/事件/审计）。runtime→infra 合规。

    与 [[_decrypt_user_secret]] 同构，额外回传 fingerprint 供日志标注「用的是哪把 key」——
    换过 Key 后排查「到底生效没有」全靠这一行。
    """
    from infra import crypto
    from infra.repositories import model_assets

    row = await model_assets.get_secret_material(model_asset_id)
    if row is None or not row.get("secret_ciphertext"):
        return None, None
    fp = row.get("secret_fingerprint")
    try:
        return crypto.decrypt(row["secret_ciphertext"]), fp
    except ValueError:  # key 不匹配（OPENOPS_ENCRYPTION_KEY 换过）/ 密文损坏
        print(f"[OpenOps][model] 资产 {model_asset_id} 的密钥解密失败（{fp}）——"
              "OPENOPS_ENCRYPTION_KEY 变更或密文损坏，需在管理台重新录入 API Key", flush=True)
        return None, fp


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


async def _dynamic_mcp_specs(user_id: str = "") -> list[dict[str, Any]]:
    """OPENOPS_MCPREGISTRY=real 时，从注册表发现所有 server 的工具 → 动态工具 spec（含 server_url/只读/scope）。
    mock 或发现失败 → 空（不拖垮 demo 工具）。appid 约定（拍板 i）：inputSchema 有 project_id/appid → 该字段受 scope 约束。"""
    if os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() != "real":
        return []
    from infra import host_ip
    from infra.external import mcp_registry_client

    # 复合键左半的**稳定锚**：按 server_id 映射到本地资产的 display_name。
    # 上游改名后 srv["server_name"] 会变，而模板 default_tools 里存的是旧名的复合键——直接用上游
    # 现名会让 `{新名}::{tool}` 与模板的 `{旧名}::{tool}` 失配，工具被白名单静默跳过
    # （TOOL_NOT_WHITELISTED，且该路径不置 tool_blocked，对话里毫无提示）。
    # 口径与 asset_admin_service.register_mcp 一致：display_name 是我们的绑定身份，上游名只是元数据。
    # 取不到本地行（新 server 首轮、对账还没跑）才回退上游名——与 reconcile 建行时的取值同源。
    local_name_by_sid: dict[str, str] = {}
    try:
        from infra.repositories import assets as _assets

        for m in await _assets.list_platform_mcps_with_manifest():
            _sid = str((m.get("manifest_json") or {}).get("server_id") or "")
            if _sid and m.get("display_name"):
                local_name_by_sid[_sid] = str(m["display_name"])
    except Exception as e:  # noqa: BLE001 —— 读不到就退回上游名（不因一次库抖动丢掉整个工具面）
        log.warning("平台 MCP 本地名映射读取失败，复合键回退上游 server_name：%s", _redact(str(e)))

    try:
        servers = await mcp_registry_client.list_servers(user_id)
    except Exception as e:  # noqa: BLE001 —— 发现失败不拖垮整个 run
        # 动态工具面将整轮为空（主+子 Agent）——后台链路（告警诊断）走纯 IAM 机机态
        # （2026-08-16 定案：对端双鉴权 cookie 优先/IAM 兜底），失败先核对 j2c_utils
        # IAM token 可取 与 对端双鉴权是否上线；401/1001=对端未认 IAM 或误配静态 cookie。
        log.warning("MCP 注册表 list_servers 失败（动态工具面将为空）：%s", _redact(str(e)))
        return []
    specs: list[dict[str, Any]] = []
    for srv in servers:
        surl = srv.get("server_url")
        if not surl:
            continue
        try:
            # 平台支路：发现面与调用面头对称，同带 x-ec2-ip（后端主机 IP）+ 服务态 IAM 头。
            # 对照 _user_mcp_specs——那边不传，默认 None 即不带（用户支路零平台凭据）。
            from infra.iam_headers import iam_auth_headers
            tools = await mcp_registry_client.discover_tools(
                surl, {**host_ip.ec2_ip_headers(), **iam_auth_headers()})
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
                # server 身份（复合键 "{server_name}::{tool}" 的左半）：白名单/标注解析按它区分同名工具。
                # 取**本地** display_name（按 server_id 映射）而非上游现名——见上方 local_name_by_sid：
                # 上游改名不得改变复合键，否则模板绑定与标注全部失配。本地无对应行才回退上游名。
                "server_name": tool_key.sanitize_server_name(
                    local_name_by_sid.get(str(srv.get("server_id") or ""))
                    or str(srv.get("server_name") or srv.get("server_id") or "")),
                "scope_mode": "required" if appid_prop else "none",
                "appid_arg_path": f"$.{appid_prop}" if appid_prop else None,
                "source_type": "platform",  # 自证：随 spec 下传给 tool_gateway.invoke（对照 _user_mcp_specs）
            })
    return specs


# 用户 MCP 工具发现超时（每家独立）：direct 路由是 3 次串行 POST × httpx timeout=15 ⇒ 不封顶会把一家挂掉的
# server 变成每轮 +45s。
_USER_MCP_DISCOVER_S = float(os.getenv("OPENOPS_USER_MCP_DISCOVER_TIMEOUT_S", "5"))
# 用户 MCP 工具是否一律需审批。默认 0 = 信任 server 自证的 readOnlyHint（28.2「用户自担责任，仅审计」：
# 是用户自己的 server、不带任何平台凭证、每次调用都有审计）。置 1 可一键收紧为无条件 ASK。
_USER_MCP_ASK_ALWAYS = os.getenv("OPENOPS_USER_MCP_ASK", "0") == "1"


async def _user_mcp_specs(st: TaskState) -> list[dict[str, Any]]:
    """用户自定义 MCP（st.mcp_servers）→ 动态工具 spec。与平台支路的四点差别，逐条承重：

    1) **仅 main**：子 Agent 工具面恒按画像 mcp_tools 白名单裁剪（B7 per-agent 隔离）。子 st.mcp_servers
       本就不继承（_child_state 不复制），此处 agent_key 守卫是第二道锁。
    2) **不受 OPENOPS_MCPREGISTRY=mock 门**：registry 是**平台** server 的目录，与用户自己登记的 endpoint
       无关。门在那个开关上会让本特性在所有 dev/CI（默认 mock）里静默不存在。
    3) **source_type='user' 随 spec 下传** → Tool Gateway 走用户分支（28.2）：不透传 Cookie、不注入
       X-OpenOps-*、不做 APPID 范围管控。**绝不能让用户填的 URL 收到本人 IAM Cookie**——invoke 的
       source_type 默认值就是 "platform"，不显式传即泄（_platform_headers 会带上 Cookie + effective_appids）。
    4) **scope_mode 恒 none / appid_arg_path 恒 None**：用户分支本就不校 scope；且 _make_dynamic_tool 的
       「单 appid 自动补」会把**平台 APPID 塞进用户 server 的入参**（越权外泄），必须靠 appid_field=="" 关死。
    5) **发现与调用两面都不带 x-ec2-ip**（后端主机内网 IP）：用户可填任意 URL，带上即泄露内网拓扑；
       且本函数对每个已登记用户 MCP **每轮无条件出网**，泄露发生在任何审批/标注之前。靠 discover_tools
       的 extra_headers 默认 None 与 tool_gateway.invoke 用户支路不传 handshake_headers 两处保证——
       **勿给 discover_tools 加带头的默认值**（理由同 3 的 Cookie）。

    审批口径：is_approval_required = not readonly（拍板：信任 readOnlyHint）。**该 hint 由用户自己的
    server 自证**，且用户分支没有标注 fail-closed 兜底（_effective_annotation 只在 platform 分支内），
    故这是唯一的门 —— OPENOPS_USER_MCP_ASK=1 可收紧为无条件 ASK。

    mock 环境行为（**勿当 bug 修**）：mock 下 discover_tools 对任意 URL 返回内置 _TOOLS
    {query_resource, recover_execute}，二者通常已被平台/内置占名 → 被 _build_toolkit 的同名冲突守卫跳过
    ⇒ 零装配、零出站。real 环境才真正装配。
    """
    if st.agent_key != "main" or not st.mcp_servers:
        return []
    from infra import egress
    from infra.external import mcp_registry_client

    async def _one(srv: dict[str, Any]) -> list[dict[str, Any]]:
        surl = str(srv.get("endpoint") or "")
        if mcp_registry_client.is_placeholder_endpoint(surl):
            return []  # 占位 endpoint 不出网、不出 spec
        try:
            # 调用边界复校（登记时校过，但 DNS 会翻转；先例 model_gateway.py:57）。getaddrinfo 是**同步阻塞**，
            # 必须 to_thread——否则 gather 的同步前缀串行执行，并发是假的。
            await asyncio.to_thread(egress.check_mcp_egress, surl)
            tools = await asyncio.wait_for(mcp_registry_client.discover_tools(surl), _USER_MCP_DISCOVER_S)
        except Exception as e:  # noqa: BLE001 —— 一家用户 server 坏/被 egress 拦，不拖垮整轮（同 reconcile 口径）
            log.warning("用户 MCP 工具发现失败 mcp=%s：%s", srv.get("display_name"), _redact(str(e)))
            return []
        out: list[dict[str, Any]] = []
        for t in tools:
            if not t.get("tool_name"):
                continue
            out.append({
                "name": t["tool_name"], "description": t.get("description", ""),
                "input_schema": t.get("input_schema") or {}, "server_url": surl,
                "readonly": bool(t.get("readonly")),
                "scope_mode": "none", "appid_arg_path": None,  # 见 docstring 4)：关死平台 APPID 自动补
                "source_type": "user",
                "display_name": srv.get("display_name"),
            })
        return out

    got = await asyncio.gather(*(_one(s) for s in st.mcp_servers), return_exceptions=True)
    return [s for r in got if isinstance(r, list) for s in r]


def _make_dynamic_tool(st: TaskState, run: dict[str, Any], spec: dict[str, Any],
                       reg_name: str | None = None) -> Any:
    """发现到的 MCP 工具 → agentscope FunctionTool：调用穿过 Tool Gateway（scope/审批/审计/28.2 头），
    按 server_url 经 console proxy 路由。用 __signature__ 让 agentscope 从中抽出参数 schema。

    reg_name = toolkit 注册名（LLM 面）：同名跨 server 冲突时由 _build_toolkit 分配器给出
    `{slug(server)}__{tool}` 命名空间名，默认（无冲突）就是裸 spec["name"]。invoke 另带
    server_name（复合键身份的 server 维度）——白名单与标注按它区分同名工具，不解析注册名。"""
    import inspect

    from agentscope.message import TextBlock
    from agentscope.tool import FunctionTool, ToolResponse

    name, server_url = reg_name or spec["name"], spec["server_url"]
    server_name = spec.get("server_name") or spec.get("display_name")
    schema = spec.get("input_schema") or {}
    props: dict[str, Any] = schema.get("properties") or {}
    required = set(schema.get("required") or [])

    appid_field = (spec.get("appid_arg_path") or "").removeprefix("$.")
    source_type = str(spec.get("source_type") or "platform")

    async def _handler(**kwargs: Any) -> Any:
        args = {k: v for k, v in kwargs.items() if k in props and v not in (None, "", [], {})}
        # 联调便利：appid（如 project_id）受 scope 约束（拍板 i），但 GLM 常忘传/传空；当 scope 恰好 1 个 appid
        # 时自动补上（填的就是被允许的那个，不削弱 scope）。多 appid（真 oModel）时不补，交给模型自己选。
        # 用户 MCP：appid_arg_path 恒 None ⇒ 此处不触发（不得把平台 APPID 塞进用户 server 入参）。
        if appid_field and not args.get(appid_field):
            allowed = (st.scope_ctx or {}).get("effective_appids", [])
            if len(allowed) == 1:
                args[appid_field] = allowed[0]
        if source_type == "user":
            # 调用边界复校（发现→调用之间 DNS 可能翻转）；被拦即不发包
            from infra import egress
            try:
                await asyncio.to_thread(egress.check_mcp_egress, server_url)
            except Exception as e:  # noqa: BLE001
                return ToolResponse(content=[TextBlock(type="text", text=f"工具被拦截：EGRESS_BLOCKED {e}")])
        try:
            r = await tool_gateway.invoke(st, run, name, args, server_url=server_url, source_type=source_type,
                                          server_name=str(server_name) if server_name else None,
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


def _render_scope_apps(scope_ctx: dict[str, Any]) -> str:
    """list_scope_apps 的文案（提出来是为了脱开 agentscope 单测）：每行「appid｜名称」，无名退回裸 appid。

    `scope_apps`（`[{appid, name, enterprise_id}]`，omodel resolve 带出）只是 `effective_appids` 的显示
    装饰，**仅在两者严格对齐时采用**——子 Agent 拷贝 ctx、测试手搓 scope_ctx、进程重启后读到旧 shadow
    状态都可能让它对不上，此时整体退回裸列表，绝不半渲染。

    企业 id 是 32 位长串，逐行重复会淹没 appid：全部应用同企业（绝大多数情况）时收进抬头只说一次；
    跨租户（`_build_workspace_ui` 明确允许不同应用属不同租户）才逐行标注。
    """
    appids = scope_ctx.get("effective_appids", [])
    if not appids:
        return "当前工作范围为空（无可用应用）。"
    apps = scope_ctx.get("scope_apps") or []
    names: dict[str, str] = {}
    ents: dict[str, str] = {}
    if [a.get("appid") for a in apps] == list(appids):
        names = {a["appid"]: str(a.get("name") or "") for a in apps}
        ents = {a["appid"]: str(a.get("enterprise_id") or "") for a in apps}
    distinct = {e for e in ents.values() if e}
    # 同企业且人人有值 → 抬头说一次；否则（跨租户/部分缺失）逐行标，宁可啰嗦也不含糊
    uniform = distinct if len(distinct) == 1 and len(ents) == len(appids) and all(ents.values()) else set()
    head = (f"当前工作范围内共 {len(appids)} 个应用（appid｜名称），企业 {next(iter(uniform))}：" if uniform
            else f"当前工作范围内共 {len(appids)} 个应用（appid｜名称{'｜企业' if distinct else ''}）：")
    lines = []
    for a in appids:
        # appid 必须打头且不加修饰：模型会原样复制它填工具参数，Tool Gateway 做精确字符串成员校验。
        seg = f"- {a}"
        if names.get(a):
            seg += f"｜{names[a]}"
        if not uniform and ents.get(a):
            seg += f"｜企业 {ents[a]}"
        lines.append(seg)
    return (head + "\n" + "\n".join(lines)
            + "\n（各查询工具的应用参数必须取自此范围，且只填 appid 本身、不要带名称或企业）")


def _render_skill_catalog(available_skills: dict[str, dict[str, Any]]) -> str:
    """构造 run_platform_skill 的工具描述（发现链路核心）：逐条列出 `skill_key`（display_name）：用途。
    用途来自 SKILL.md 的 description（经 resolve_available_skills 流入 available_skills[k]['description']）。
    LLM 必须"知道"有哪些 skill_key 合法**以及各自用途**，否则零感知、不会主动调、且易传错名被 fail-closed。"""
    def _line(k: str, v: dict[str, Any]) -> str:
        dn = v.get("display_name")
        head = f"`{k}`（{dn}）" if dn and dn != k else f"`{k}`"
        d = " ".join((v.get("description") or "").split())  # 压平换行/多空格
        return f"- {head}：{d[:200]}{'…' if len(d) > 200 else ''}" if d else f"- {head}"

    lines = "\n".join(_line(k, v) for k, v in available_skills.items()) or "（当前实例未装配任何 Skill）"
    return ("执行一个已装配到当前实例的 Skill（在你的隔离容器内受控执行，返回结构化结果）。"
            "根据下列各 Skill 的用途，判断用户诉求匹配哪个就主动调用；用户消息以 `/<Skill名>` 开头即要求优先执行对应 Skill。"
            "skill_name 必须取自下列清单（取反引号内的名字），未装配会被拒绝。\n"
            f"当前可用 Skill：\n{lines}")


# 主 Agent 人设兜底（模板 main.role 必填，通常不会走到；仅防御 role 缺失/空）
_DEFAULT_MAIN_ROLE = "你是资深 SRE 诊断 Agent：理解用户任务，调度平台巡检/诊断/恢复能力完成诊断与恢复。"
# 平台层固定规则：始终附在角色人设之后。安全项声明优先级高于角色设定，用户人设无法绕过。
# **按受众拆块**（原为单块常量，主 Agent 独享）：子 Agent 曾只拿到 role + 汇报纪律，既没有
# 「优先用 Skill」引导、又被纪律里的「禁止无差别调用工具」反向抑制，导致概率性不调 Skill
# （用户体感「子 Agent 时灵时不灵地不加载 Skill」）；同时也没被告知审批要求。故共享块下沉，
# 主/子经 _build_system_prompt / _build_sub_system_prompt 各自拼装，规则新增时不会再漏掉子 Agent。
_RULES_HEADER = "\n\n【平台规则（始终遵守；安全项优先级高于以上角色设定）】\n"
# 主/子共享：Skill 偏好引导——这条是 Skill 调用确定性的来源，两边都必须有
_SKILL_PREFERENCE_RULE = (
    "- 当某个可用 Skill 的用途与当前任务匹配时，必须优先调用该 Skill（run_platform_skill）并严格按其步骤/流程执行，"
    "不要自行发挥；只有在没有任何匹配 Skill 时，才用通用工具（查询/命令/Read/Grep）自己诊断。\n"
)
# 诊断面板上报——update_diagnosis_board 主/子都注册（run 级单例面板，见 _build_toolkit），
# 这是「模型漏调」的四层兜底之二（之一是工具 docstring，之三/四见 sandbox_skill 手册指引与 seed 角色 prompt）。
# **按受众拆两版**（同本文件既有的「按受众拆块」纪律）：原为主/子共享一段，连子 Agent 都被教
# 「诊断完成时以 step=5、step_completed=true 提交 conclusion」——主 Agent 还在第2步证据阶段，任何一个
# 并行子 Agent 自认为查完就把 run 级单例面板一步打成「诊断完成」（内网现象：走到第2步就直接出根因报告）。
# 服务端已在 rca_board 硬收窄子任务权限，这里同口径告知，省得模型反复撞墙浪费迭代预算。
_BOARD_RULE_MAIN = (
    "- 执行诊断类任务（含按 Skill 手册的五步法流程执行）时，必须调用 update_diagnosis_board 把进度"
    "同步到用户界面：每进入一个新步骤调用一次（step=新步骤号），并把该步已取得的事实/假设/证据源等一并"
    "增量提交，同时用 step_summary 附上该步的一句话小结；诊断完成时以 step=5、step_completed=true 提交"
    " conclusion。内容必须来自本轮真实取得的数据，不得虚构。\n"
    "- 面板必须按 1→2→3→4→5 逐步推进，每步各调用一次：禁止跳步，禁止把假设（3）、验证（4）的产出并进"
    "一次 step=5 提交收尾。用户就是靠这条推进链看你的推理过程，跳步等于把假设与验证过程对用户隐藏。\n"
)
_BOARD_RULE_SUB = (
    "- 执行诊断类任务时，用 update_diagnosis_board 把你本轮取得的事实（facts）/证据源（sources）/"
    "假设（hypotheses）增量提交到用户界面的诊断面板，step 填你当前所处的步骤号即可。内容必须来自本轮"
    "真实取得的数据，不得虚构。\n"
    "- 你是被派发的子任务：面板的步骤推进、step_completed 与 conclusion 一律由主任务提交，"
    "你提交也不会生效（平台会忽略），不要尝试用 step=5、step_completed=true 收尾整个诊断。\n"
)
# 主/子共享：安全护栏（子 Agent 可绑 recover_execute，同样需要知道审批要求——纵深防御）
_SAFETY_RULES = (
    "- 恢复类/写操作必须先请求人工批准、获批后才执行。\n"
    "- 若完成用户诉求需要某个当前工具列表里并不存在的能力（如查询告警、拓扑/对象关系等外部 MCP 工具），"
    "不要笼统说「MCP 工具没有注册/没有被加载」；应据实说明：该能力对应的 MCP 工具需要管理员先在插件页"
    "注册并标注、且加入当前实例模板后才可用，并同时用你现有的工具尽力给出可行的替代帮助或下一步建议。\n"
)
# 仅主 Agent：render_chart 只注册给 main（见 _build_toolkit 的 agent_key == "main" 分支）
_MAIN_ONLY_RULES = (
    "- 仅当本轮已取得的数值适合做趋势或对比时才调用 render_chart；不得为图表臆造数据。"
)
_PLATFORM_RULES = _RULES_HEADER + _SKILL_PREFERENCE_RULE + _BOARD_RULE_MAIN + _SAFETY_RULES + _MAIN_ONLY_RULES

# 输出语言（2026-08-21 换模型后新增）：新模型默认全英文作答，而平台交付面（对话正文、诊断面板、
# 图表、WeLink 通知摘要取 conclusion 前 200 字）全中文——英文结论等于交付物不可用。语言是平台层
# 硬约束、与角色人设无关，故与 _PLATFORM_RULES 同源下沉；同本文件既有的「按受众拆块」纪律拆主/子
# 两版（render_chart 只注册给 main，不能拿去教子 Agent），子 Agent 也必须有——它的汇报会被主
# Agent 直接引用进最终结论，漏了子就漏半条链。位置刻意放**最末**取近因位。
_OUTPUT_LANGUAGE_HEADER = "\n\n【输出语言（平台硬约束，优先级高于角色设定与本提示词其余各节）】\n"
_OUTPUT_LANGUAGE_CORE = (
    "- 无论用户输入、工具返回、Skill 手册、日志与告警原文是什么语言，你产出的所有文字一律用简体中文："
    "对话正文与最终结论、思考过程、update_diagnosis_board 的 facts / hypotheses / sources / "
    "step_summary / conclusion、以及你返回给上游的汇报文本，全部包含在内。\n"
    # 例外清单不是客套：模型会原样复制 appid 去填工具参数（见 _render_workspace_scope 的注释），
    # 「顺手汉化」一次就被 Tool Gateway 的精确成员校验 fail-closed。
    "- 例外（原样保留，不得翻译或改写）：appid、实例名/主机名、服务名、指标名、告警编号、错误码、"
    "命令、代码、文件路径、日志原文片段，以及所有工具调用参数；需要解释时在其后用中文括注。\n"
    "- 不要中英对照输出两份，也不要先写英文再附译文——直接只给中文。\n"
)
# 仅主 Agent：render_chart 只注册给 main（与 _MAIN_ONLY_RULES 同一条边界）
_OUTPUT_LANGUAGE_MAIN = _OUTPUT_LANGUAGE_HEADER + _OUTPUT_LANGUAGE_CORE + (
    "- render_chart 的图表标题与坐标轴/系列名同样用简体中文。"
)
# 子 Agent：rstrip 对齐行距（拼在【汇报纪律】之后，末尾不留空行）
_OUTPUT_LANGUAGE_SUB = (_OUTPUT_LANGUAGE_HEADER + _OUTPUT_LANGUAGE_CORE).rstrip("\n")

# D 块：worker 汇报纪律（37 号老 roles.yaml 口径翻译）——拼进每个 sub agent 的 system_prompt。
# 原住 infra.seed（DB 播种模块）却只被运行时消费，与 _PLATFORM_RULES 不同源；迁来与之并置。
SUB_REPORT_DISCIPLINE = (
    "缺参数时直接返回 blocker 说明，绝不凭空造参数。"
    "禁止无差别调用所有通用工具——按任务选择所需工具，同参数每个工具只调用一次"
    "（上述「优先调用用途匹配的 Skill」不受本条限制）；"
    "工具返回空结果/无数据 = 查询完成，严禁对同一条件重复调用或自行调整参数重试。"
    "必须在全部步骤执行完成后一次性汇报结果，禁止中途输出部分结论；"
    "只汇报查询结果本身，不要发散分析置信度、caveats 或建议——这些由主 Agent 判断。"
)


def _skill_hint_clause(skill_hint: str) -> str:
    """/<skill> 显式触发时的确定性执行指令（主/子同形）。"""
    return (f"\n- 本轮用户已显式指定优先执行 Skill `{skill_hint}`："
            f"请第一步调用 run_platform_skill(skill_name='{skill_hint}')，除非用户另有明确指示。")


def _build_system_prompt(st: TaskState) -> str:
    """装配主 Agent 的 system_prompt：用户人设（模板 main.role + 实例 main_role_append）在前领跑，
    平台规则在后固定兜底，skill_hint 命中时再追加确定性执行指令，输出语言硬约束收尾。修「主 Agent
    人设被丢弃、硬编码『先巡检定界』逼模型自行诊断」——子 Agent 早已走 sub['role']，主 Agent 对齐。"""
    persona = (st.main_role or _DEFAULT_MAIN_ROLE).strip() or _DEFAULT_MAIN_ROLE
    if st.main_role_append:
        persona = f"{persona}\n{st.main_role_append.strip()}"
    prompt = persona + _PLATFORM_RULES
    if st.skill_hint:  # /<skill> 显式触发：确定性优先执行指定 Skill（start_task 已按 available_skills 校验命中）
        prompt += _skill_hint_clause(st.skill_hint)
    return prompt + _OUTPUT_LANGUAGE_MAIN  # 语言块压轴：近因位 + 自带优先级声明，用户人设压不过它


def _build_sub_system_prompt(child: TaskState, sub: dict[str, Any]) -> str:
    """装配子 Agent 的 system_prompt：画像人设 + 子 Agent 版平台规则（面板规则用 _BOARD_RULE_SUB：
    只提内容、不推进不收尾）+ 汇报纪律 + 输出语言硬约束。

    顺序刻意：Skill 规则**先于**汇报纪律，否则纪律里的「禁止无差别调用通用工具」会被读成
    对 Skill 的抑制。skill_hint 必须按 **child** 的技能面重新校验——子技能面是 leader 的子集，
    主 Agent 能跑的 hint 子 Agent 未必装配，直接透传会引导它调一个必被 fail-closed 的 skill。
    """
    # rstrip：_SAFETY_RULES 末尾的 \n 与 hint 子句开头的 \n 会叠出空行（主 Agent 侧因
    # _MAIN_ONLY_RULES 不带尾换行而无此问题）——对齐两边的行距
    prompt = str(sub["role"]) + (_RULES_HEADER + _SKILL_PREFERENCE_RULE + _BOARD_RULE_SUB + _SAFETY_RULES).rstrip("\n")
    if child.skill_hint and child.skill_hint in (child.available_skills or {}):
        prompt += _skill_hint_clause(child.skill_hint)
    # 独立小节标题：纪律原本紧贴规则列表，读起来像上一条 bullet 的续行——本次修复的要害正是
    # 让模型把「优先用 Skill」和「别乱调工具」当成两条互不覆盖的指令，分节可降低误读
    return f"{prompt}\n\n【汇报纪律】\n{SUB_REPORT_DISCIPLINE}" + _OUTPUT_LANGUAGE_SUB


async def _build_toolkit(st: TaskState, run: dict[str, Any]) -> Any:
    """工具统一走 runtime.tool_gateway（B4：标注/APPID/Secret/header/审计）；RCA 卡更新留在工具内。

    标注裁剪：仅注册标注 status=allowed 的工具；Gateway 内再做二次校验（纵深防御）。
    动态 MCP（OPENOPS_MCPREGISTRY=real）：注册表发现的 server 工具也在此装配，同样穿 Gateway。
    """
    from agentscope.message import TextBlock
    from agentscope.tool import FunctionTool, Grep, Read, Toolkit, ToolResponse

    from runtime.sandbox_tool_backend import SandboxToolBackend

    counter = {"query": 0}

    async def query_resource(appid: str) -> Any:
        """查询指定应用的可观测数据（指标 P99/错误率、Redis 连接数、服务依赖拓扑），用于巡检与诊断。

        Args:
            appid: 目标应用 ID，例如 APP-A。
        """
        counter["query"] += 1
        n = counter["query"]
        try:
            r = await tool_gateway.invoke(
                st, run, "query_resource", {"appid": appid},
                started_msg="巡检 · 指标查询" if n == 1 else "诊断 · 拓扑依赖",
                succeeded_msg="P99 / 错误率 / Redis 连接数已取回" if n == 1 else "svc-payment-api 依赖图已取回",
            )
        except ToolBlocked as e:  # tool.blocked 已发；把失败回给模型收口
            st.tool_blocked = True  # B6-RT-001
            return ToolResponse(content=[TextBlock(type="text", text=f"工具被拦截：{e.reason_code} {e.message}")])
        # A4 守卫：模型已接管面板（update_diagnosis_board，owner=主任务）后 demo 剧本不得覆写/倒灌
        if st.rca_source != "model" and board_owner(st).rca_source != "model":
            st.rca = rca(1 if n == 1 else 2, "诊断中" if n == 1 else "验证 H1")
            st.rca_source = "demo"
            await emit(st, run, "openops.rca.updated",
                       message="RCA 面板更新（诊断中）" if n == 1 else "假设排行更新（H1 领先）", payload=st.rca)
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
        # A4 守卫：同 query_resource——模型面板在场时 demo 剧本「已闭环」不得覆写
        if st.rca_source != "model" and board_owner(st).rca_source != "model":
            st.rca = rca(3, "已闭环")
            st.rca_source = "demo"
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

    async def list_container_files(path: str = ".", pattern: str | None = None) -> Any:
        """列出你容器内某个目录下的文件（可按文件名通配过滤）。用于发现 Skill 包 / references 下有哪些
        文件，再用 Read 工具逐个读取。

        Args:
            path: 目录路径，如 /openops/workspace/skills/<task>/<call>。默认当前目录。
            pattern: 可选文件名通配，如 `*.md`。
        """
        from runtime.sandbox_bash import list_container_files as _ls  # 局部导入避免环

        text = await _ls(st, run, path, pattern=pattern)
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
        """列出当前会话工作范围（scope）内可用的应用（appid 与名称）。用户问「我有哪些应用 / 能查哪些应用」时用此工具。"""
        return ToolResponse(content=[TextBlock(type="text", text=_render_scope_apps(st.scope_ctx or {}))])

    async def update_diagnosis_board(
        step: int,
        step_completed: bool = False,
        step_summary: str = "",
        title: str = "",
        tiles: list[dict[str, Any]] | None = None,
        current_question: str = "",
        why: str = "",
        facts: list[dict[str, Any]] | None = None,
        unknowns: list[dict[str, Any]] | None = None,
        sources: list[dict[str, Any]] | None = None,
        hypotheses: list[dict[str, Any]] | None = None,
        actions: list[dict[str, Any]] | None = None,
        conclusion: str = "",
        verdict: str = "",
    ) -> Any:
        """把「诊断五步法」（1范围→2证据→3假设→4验证→5结论）的进度与阶段产出同步到用户界面的诊断面板。这是纯展示工具：不查询数据、不执行变更、不触发审批。

        调用时机（必须遵守）：
        - 执行诊断类任务（含按 Skill 手册的五步法流程执行）时必须使用本工具；
        - 每进入一个新步骤调用一次（step=新步骤号），并把已取得的事实/假设等产出一并带上；
        - 诊断完成时最后调用一次：step=5、step_completed=true，并填写 conclusion（诊断结论）。
        - 增量合并：未传的字段保留上次的值，传了的字段整体替换；每次只需提交新增或变化的内容。
        - 必须按 1→2→3→4→5 逐步推进、每步各一次：步骤只能前进不能回退，且不得跳步——
          不要把假设（3）、验证（4）的产出并进一次 step=5 收尾。
        - 若你是被派发的子任务：只提交本轮取得的内容（facts/sources/hypotheses），步骤推进、
          step_completed 与 conclusion 由主任务负责，你提交也不会生效。
        - 提交 step=3（假设）后平台可能弹卡请用户确认，本工具的返回值会包含用户决策：
          若返回值提示「用户补充了一条候选假设」，必须把它并入候选、重排置信度并重新提交
          step=3（不会再次弹卡），再进入验证；返回值提示继续排查时直接进入验证。
        - 内容必须来自本轮真实取得的数据，不得虚构。

        Args:
            step: 当前所处步骤号，1..5（1=范围 2=证据 3=假设 4=验证 5=结论）。
            step_completed: 当前步骤是否已完成；step=5 且为 true 表示整个诊断结束。
            step_summary: 当前步骤的一句话小结（≤120 字），每次推进或完成一步时提交；界面在步骤收起时展示。
            title: 事件/问题短标题（如「支付下单 P99 突增」），首次调用必填。
            tiles: 概览信息块，最多 6 个，形如 [{"label": "症状", "value": "P99 180ms→1.4s"}]。
            current_question: 当前正在回答的关键问题（一句话）。
            why: 为什么这个问题是当前关键（一句话）。
            facts: 已确认事实，形如 [{"text": "..."}]，最多 20 条。
            unknowns: 未知待验证项，形如 [{"text": "..."}]，最多 20 条。
            sources: 证据源状态，形如 [{"name": "Prometheus", "status": "done", "tone": "good"}]；tone 只能是 good、warning、danger 或 neutral。
            hypotheses: 假设排行，形如 [{"text": "H1 Redis 连接泄漏", "tag": "支持", "tagTone": "good", "conf": 0.72}]；conf 是 0..1 的置信度。
            actions: 建议动作，形如 [{"tier": "立即", "text": "重启 svc-a", "confirm": true, "impact": "3 实例", "status": "待确认", "statusTone": "warning"}]，最多 8 条。
            conclusion: 诊断结论（step=5 时必填）：影响边界、最可能根因方向、建议下一步。
                用纯文本、不要 Markdown 标记（#、**、`、列表符）；第一句先给最可能根因——
                该文本会作为告警通知摘要（截前 200 字）直接推送给用户。
            verdict: 结论判定（step=5 收尾时随 conclusion 一并提交）：recovered=故障已恢复/已自愈，
                escalated=需人工升级处理；无法判定时留空。该值会显示为告警清单的「接管结果」列。
        """
        try:
            text = await apply_board_update(st, run, {
                "step": step, "step_completed": step_completed, "step_summary": step_summary,
                "title": title, "tiles": tiles,
                "current_question": current_question, "why": why, "facts": facts, "unknowns": unknowns,
                "sources": sources, "hypotheses": hypotheses, "actions": actions, "conclusion": conclusion,
                "verdict": verdict,
            })
        except RcaBoardContractError as exc:
            # 契约错误回给模型自纠（同 render_chart 口径）；纯展示工具失败绝不置 st.tool_blocked
            # （面板更新失败不应压制诊断结论）
            return ToolResponse(content=[TextBlock(
                type="text",
                text=f"诊断面板参数未通过校验：{exc}。请按工具说明修正后重新调用，且不要提交 HTML 或未列出的字段。",
            )])
        # 假设 checkpoint：主任务首次提交 step>=3 后弹卡暂停，等用户补充假设/继续排查（超时自动继续）。
        # 决策文本拼进本工具返回值——模型下一轮必然读到，比新事件让它「自己发现」确定得多。
        suffix = await diagnosis_checkpoint.maybe_pause_for_user(
            st, run, step=step, step_completed=step_completed)
        if suffix:
            text += suffix
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
    # user_id=任务 owner 工号（=login_key）：机机态（告警诊断）无
    # cookie，console 靠此入参识别用户返回「平台+该用户自定义」server——人对话与告警
    # 两路径 st.user_id 天然都有值（登录工号 / incident owner 工号），显式传参零魔法。
    dynamic_specs = await _dynamic_mcp_specs(st.user_id)
    _demo_env = os.environ.get("OPENOPS_DEMO_TOOLS", "").strip()  # 1=恒开 0=恒关 未设=自动
    keep_demo = _demo_env == "1" or (_demo_env != "0" and not dynamic_specs)
    if (keep_demo and not dynamic_specs and _demo_env != "1"
            and os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() == "real"):
        # 观测补强（2026-08-16）：demo 回台会掩盖「真工具全丢」——real 档发现为空必须现形
        log.warning("[runtime] 动态 MCP 发现为空，demo 工具回台掩盖中 agent=%s run=%s"
                    "——见上方 list_servers 失败日志", st.agent_key, st.run_id)
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
    # 容器内文件读取/搜索（B8·补3）：官方 Read/Grep 绑「指向本用户容器」的后端适配器——在容器内读文件
    # （带行号+分页）/ ripgrep 搜内容，绕开 run_container_command 的字符上限，解决 Skill 手册/references
    # 大文件被截断读不全。默认 LocalBackend 会读宿主机（越权），故必须显式传容器后端。均只读、tool 级 allow。
    _tool_backend = SandboxToolBackend(getattr(st, "sandbox_uid", "") or st.user_id,
                                       st.run_id, st.sandbox_cfg)
    tools.append(Read(backend=_tool_backend))
    tools.append(Grep(backend=_tool_backend))
    # 容器内 LS/Glob（find 可移植；官方无 LS，Glob 本轮未启用）
    tools.append(FunctionTool(list_container_files, name="list_container_files", is_read_only=True))
    # Skill 作 agent 工具（C1）：装配集校验 + 真 ZIP 投递 + 容器内执行在 run_bound_skill 内。
    # description 动态注入装配集（同 _make_dynamic_tool 的 description 覆盖模式）：实测问「介绍 alarm-query」
    # 只答同名 MCP——见 _render_skill_catalog（把 skill_key + 用途一并注入，让 Agent 主动发现/调用）。
    _sk_desc = _render_skill_catalog(st.available_skills or {})
    tools.append(FunctionTool(run_platform_skill, name="run_platform_skill", description=_sk_desc, is_read_only=False))
    # 真工具 list_scope_apps：读 st.scope_ctx（本地、只读、无出站）——「我有哪些应用」有真答案，GLM 不再乱够工具
    st.tool_annotations = dict(st.tool_annotations or {})
    st.tool_annotations["list_scope_apps"] = {"is_approval_required": False, "is_secret_required": False,
                                              "scope_mode": "none", "appid_arg_path": None, "status": "allowed"}
    if isinstance(st.template_tools, set):
        st.template_tools.add("list_scope_apps")
    tools.append(FunctionTool(list_scope_apps, name="list_scope_apps", is_read_only=True))
    # 诊断面板（A1）：主/子都注册、不加 agent_key 条件——面板是 run 级单例（owner=主任务，
    # 见 rca_board.board_owner），无 render_chart 的对话轮次交错问题。不走 tool_gateway、
    # 不发 tool.call.* 事件：面板更新本身经 rca.updated 落审计+活动线，再发工具卡是对话噪音。
    st.tool_annotations["update_diagnosis_board"] = {"is_approval_required": False, "is_secret_required": False,
                                                     "scope_mode": "none", "appid_arg_path": None,
                                                     "status": "allowed"}
    if isinstance(st.template_tools, set):
        st.template_tools.add("update_diagnosis_board")
    tools.append(FunctionTool(update_diagnosis_board, name="update_diagnosis_board", is_read_only=True))
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
    # 路由到各 server_url。身份 = 「server::tool」复合键（tool_key，白名单/标注解析用，跨 server 同名
    # 不互踩）；注册名 = LLM 面（见下方分配器）。注入标注：只读→免审批、写类→ASK；有 project_id/appid
    # → scope 受限（拍板 i）。
    _skipped_dynamic: list[str] = []  # 发现到、但因不在白名单未装配的动态工具（复合键；可见性：见循环后 emit）
    _dyn_added = 0  # 实际装配的动态工具数（子 Agent 空工具面现形用，2026-08-16）
    _assembled: list[tuple[dict[str, Any], dict[str, Any]]] = []  # (spec, 标注) —— 过完白名单/标注闸的装配集
    for spec in dynamic_specs:
        # server 名缺失（老 spec/测试手搓）→ 身份退化为裸名（旧语义），不造 "::tool" 畸形键
        _srv = str(spec.get("server_name") or "")
        key = tool_key.make_key(_srv, str(spec["name"])) if _srv else str(spec["name"])
        # per-agent 隔离（D/E 块，编排对称化）：动态工具对 main/sub 统一按白名单裁剪——
        # main 按模板 main.default_tools，子 Agent 按画像 mcp_tools（否则每个 Agent 都看到
        # 注册表全部真工具，角色隔离被击穿）。空白名单=纯编排者（main 被迫派发，老 D6 效果）；
        # main 需要直连的动态工具须在模板编辑器勾进 default_tools（先 allowed 标注）。
        # 复合键或裸名任一命中即放行（已迁移复合键模板 / 存量裸名模板双通）。
        if st.template_tools is None or (key not in st.template_tools and spec["name"] not in st.template_tools):
            # 可见性：收集复合键，循环后仅对 main 发一条聚合观测事件（tool.skipped）。
            # 逐条日志降为 debug（2026-08-16 内网反馈刷屏）：白名单裁剪是**常态**——每个子
            # Agent 都全量发现再按画像收窄，别家角色的工具被裁本就是设计预期（alarm-agent
            # 没有 log-server 的工具天经地义），逐条 info 一次诊断能刷几十行。异常态另有
            # 承载：main=tool.skipped 聚合事件；子 Agent 全空=TOOL_DISCOVERY_EMPTY warning。
            _skipped_dynamic.append(key)
            log.debug("动态 MCP 工具未装配（不在%s白名单）：tool=%s agent=%s",
                      "模板 default_tools" if st.agent_key == "main" else "子 Agent 画像 mcp_tools",
                      key, st.agent_key)
            continue
        # 管理员在管理台显式标注即事实来源（runtime_annotations 已按 annotation_id 非空装进快照）：
        # is_approval_required（勿审批/需审批）/scope/secret/status 全以标注为准；server 的 readOnlyHint
        # 仅作**未标注**时的审批默认。修「管理员『勿审批』被 readOnlyHint 静默覆盖、非只读动态工具永远弹
        # 审批」——旧代码在此无条件用 not readonly 顶掉了快照里的管理员标注。origin=dynamic 恒补写：供
        # tool_gateway._effective_annotation 在标注被抹除（schema 变更软删）后仍能回退续用（tool_gateway.py:121）。
        # 解析顺序：复合键条目优先；裸名条目仅当其无 server 归属（测试手搓快照）或与本 spec 同家时才
        # 回退采用——防跨 server 同名标注互串（正是本次根治的缺陷形态）。
        admin_ann = st.tool_annotations.get(key)
        if admin_ann is None:
            cand = st.tool_annotations.get(spec["name"])
            if cand is not None and cand.get("mcp_display_name") in (None, spec.get("server_name")):
                admin_ann = cand
        if admin_ann is not None:
            entry = {**admin_ann, "origin": "dynamic"}
        else:
            entry = {
                "is_approval_required": not spec["readonly"], "is_secret_required": False,
                "scope_mode": spec["scope_mode"], "appid_arg_path": spec["appid_arg_path"],
                "status": "allowed", "origin": "dynamic",
            }
        entry.setdefault("mcp_display_name", spec.get("server_name"))
        entry.setdefault("tool_name", spec["name"])
        # 管理员禁用（status!=allowed）→ 不装配（对齐平台工具：blocked 不进 toolkit、_permission_context
        # 不给规则），记 pruned 供审计（B6-RT-001③）；标注保留占名 + gateway 兜底。
        if entry.get("status") != "allowed":
            st.tool_annotations[spec["name"]] = entry
            pruned.append((spec["name"], "TOOL_BLOCKED"))
            continue
        _assembled.append((spec, entry))
    # 注册名分配（LLM 面）：裸名在「本次装配集 + 已注册内置/demo 名」内唯一 → 注册名=裸名（零行为
    # 变化）；冲突（同名跨 server 同时选中，或撞内置名）→ 冲突方**全部**改 `{slug(server)}__{tool}`
    # （不留裸名——留一个等于把歧义留给模型），内置工具从此不可被动态工具静默 shadow（agentscope
    # Toolkit 同名注册 last-wins 且无告警，此前平台动态工具与内置之间无守卫）。分配确定性：装配集
    # 按 specs 原序（注册表顺序），slug 撞车按序号递增——仅真冲突才改名，存量会话恢复后注册名漂移最小。
    _reserved = {t.name for t in tools if getattr(t, "name", None)} | set(fns)
    _name_counts: dict[str, int] = {}
    for spec, _e in _assembled:
        _name_counts[str(spec["name"])] = _name_counts.get(str(spec["name"]), 0) + 1
    _used_reg = set(_reserved)
    for spec, entry in _assembled:
        name = str(spec["name"])
        if _name_counts[name] == 1 and name not in _reserved:
            reg_name = name
        else:
            slug = re.sub(r"[^A-Za-z0-9_-]", "_", str(spec.get("server_name") or "srv")) or "srv"
            reg_name = f"{slug}__{name}"[:64]
            n = 2
            while reg_name in _used_reg:
                reg_name = f"{slug}__{name}"[: 64 - len(str(n)) - 1] + f"_{n}"
                n += 1
        _used_reg.add(reg_name)
        # st.tool_annotations 的运行面形态：key=注册名、值内携带 server 身份元数据——
        # _permission_context / _handle_ask / gateway 快照回退全按注册名工作，无需解析。
        st.tool_annotations[reg_name] = entry
        tools.append(_make_dynamic_tool(st, run, spec, reg_name=reg_name))
        _dyn_added += 1
    # 可见性（白名单闸）：注册表已发现、却因不在模板白名单未装配的动态工具——仅对 main 发一条 tool.skipped
    # 观测事件（子 Agent 的白名单收窄是刻意角色隔离，噪声大，只落日志）。**不置 st.tool_blocked**：
    # 「未启用」不是「被拦截」，不该压制本轮「已闭环」结论；故用独立事件类型（非 tool.blocked 的红色阻断）。
    if st.agent_key == "main" and _skipped_dynamic:
        # message 只报数量 + 前 3 个：曾把全部工具名拼进去，内网一次 74 个直接顶爆 emit 的
        # redact_text(max_length=500)，在活动栏里断在半截（`query_service_call_serv`）且毫无可读性。
        # 完整清单走 payload["tools"]（redact.sanitize_activity_payload 显式保留），数据不靠 message 承载。
        _head = "、".join(_skipped_dynamic[:3])
        _more = f" 等 {len(_skipped_dynamic)} 个" if len(_skipped_dynamic) > 3 else ""
        await emit(st, run, "openops.tool.skipped", severity="info", action="mcp_not_whitelisted",
                   message=(f"注册表发现 {len(_skipped_dynamic)} 个 MCP 工具未装配到本实例"
                            f"（不在模板 default_tools 白名单）：{_head}{_more}。"
                            f"完整清单见管理台 MCP 工具页；启用需在模板编辑器勾入 default_tools 并标注 allowed。"),
                   reason_code="TOOL_NOT_WHITELISTED",
                   payload={"tools": _skipped_dynamic, "phase": "toolkit_build"})
    if (st.agent_key != "main" and st.template_tools and not _dyn_added
            and os.getenv("OPENOPS_MCPREGISTRY", "mock").lower() == "real"):  # mock 档动态发现未开，装不上是常态
        # 子 Agent 画像点名了 MCP 工具却一个都没装上（多为发现失败）——此前完全静默，
        # 模型只能自述「工具没有注册」（2026-08-16 内网盲区）。活动栏现形 + warning 日志。
        log.warning("[runtime] 子 Agent 动态工具面为空 agent=%s run=%s 画像白名单=%d 个"
                    "——多为 MCP 发现失败（见 list_servers 日志）", st.agent_key, st.run_id,
                    len(st.template_tools))
        await emit(st, run, "openops.tool.skipped", severity="warning", action="mcp_empty_toolset",
                   message=(f"子 Agent {st.agent_key} 的 {len(st.template_tools)} 个画像 MCP 工具"
                            "一个都未装配——多为工具发现失败（告警后台链路走 IAM 机机态，"
                            "核对 IAM token 与对端双鉴权），本轮将以内置能力作答。"),
                   reason_code="TOOL_DISCOVERY_EMPTY",
                   payload={"whitelist_count": len(st.template_tools), "phase": "toolkit_build"})
    # 用户自定义 MCP 工具（st.mcp_servers，仅 main）：**豁免模板 default_tools 白名单**——先例见
    # run_state_service.filter_main_skills「白名单只收窄平台资产，用户个人资产恒保留」。用户登记的 MCP
    # 不可能出现在管理员维护的模板白名单里，若按白名单裁剪则本特性等于不存在。隔离靠「仅 main」
    # （_user_mcp_specs 守 agent_key + 子不继承 mcp_servers），平台环的白名单门一行未动（B7-SEC-001）。
    # 占名集必须含**未装配**的平台名（被裁剪的 demo/动态工具、模板外的注册表工具）：它们没进 toolkit，
    # 名字却不能让用户 server 顶上——否则 Agent 以为在调那个平台工具、实际打到用户 URL。
    _taken = (set(st.tool_annotations) | {t.name for t in tools if getattr(t, "name", None)}
              | {s["name"] for s in dynamic_specs} | set(fns) | {n for n, _ in pruned})
    for spec in await _user_mcp_specs(st):
        # 同名冲突：**平台赢**，跳过用户工具。注意这与 skill 模型刻意相反（resolve_available_skills 的
        # Loop B 是 out[key]=... 即用户覆盖平台）——让用户 server 影子化一个平台工具名，等于 Agent 以为在
        # 调平台工具、实际打到用户 URL。安全优先于对称。
        if spec["name"] in _taken:
            log.warning("用户 MCP 工具重名，已跳过（平台优先）：tool=%s mcp=%s",
                        spec["name"], spec.get("display_name"))
            continue
        _taken.add(spec["name"])
        st.tool_annotations[spec["name"]] = {
            # readOnlyHint 由用户自己的 server 自证；用户分支无标注 fail-closed 兜底 ⇒ 这是唯一的门。
            # OPENOPS_USER_MCP_ASK=1 → 无条件 ASK（28.2「用户自担责任，仅审计」下默认信任）。
            "is_approval_required": True if _USER_MCP_ASK_ALWAYS else not spec["readonly"],
            "is_secret_required": False,  # 平台 Secret 绝不注入用户 endpoint
            "scope_mode": "none", "appid_arg_path": None, "status": "allowed",
            "origin": "user",
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
        if tool_key.is_composite(name):
            continue  # 复合键身份条目（启动快照）非注册名——规则只发给真实注册名，免生无主规则噪音
        if ann.get("status") != "allowed":
            continue
        if ann.get("is_approval_required"):
            ask[name] = [_rule(name, PermissionBehavior.ASK)]
        else:
            allow[name] = [_rule(name, PermissionBehavior.ALLOW)]
    # 容器内 Bash 工具（B8·补2）：tool 级 allow（受控工具），命令级四层裁决/审批在 run_bash 内做
    allow["run_container_command"] = [_rule("run_container_command", PermissionBehavior.ALLOW)]
    # 容器内文件读取/搜索（B8·补3）：官方 Read/Grep + list_container_files 均只读，tool 级 allow（防被裁剪）
    allow["Read"] = [_rule("Read", PermissionBehavior.ALLOW)]
    allow["Grep"] = [_rule("Grep", PermissionBehavior.ALLOW)]
    allow["list_container_files"] = [_rule("list_container_files", PermissionBehavior.ALLOW)]
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
    # payload 带 args（脱敏入参字典）供审批卡逐项展示；保留 target/impact 兼容旧前端。
    # server_name：注册名可能是命名空间形（如 alarm_server__restart），审批卡靠它标注工具归属。
    _snap = (st.tool_annotations or {}).get(tool_name) or {}
    await emit(st, run, "openops.approval.required", severity="warning",
               message=ask_msg,
               payload={"approval_request_id": st.approval_id, "tool": tool_name, "args": args,
                        "server_name": _snap.get("mcp_display_name"),
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
        ThinkingBlockDeltaEvent,
        UserConfirmResultEvent,
    )
    from agentscope.message import Msg, TextBlock
    from agentscope.state import AgentState

    # Agent Studio：本 task 的 span 归属（切片内部从 contextvar 读并盖成 span 属性）
    _studio_tok = studio.set_task_context(st.user_id, st.run_id, st.task_id, "main")
    agent = None  # P3：终态回写引用；toolkit 构建抛错时保持 None
    state_persisted = False

    async def _persist_state() -> None:
        """P3：AgentState 回写（幂等，成功一次即止）。

        必须在 openops.task.completed/failed **事件可见之前**完成——事件驱动方（前端
        紧接着的下一个 task、测试轮询）以完成事件为同步点，晚于事件的回写会让下一轮
        恢复到旧记忆（同 run 失忆竞态；reply 链上每多一层 middleware/generator 包装，
        「事件已发、状态未落」的窗口就更容易被调度器放大）。finally 仍兜底取消等路径。
        """
        nonlocal state_persisted
        if agent is None or state_persisted:
            return
        try:
            import json as _json

            dump = agent.state.model_dump(mode="json")
            if len(_json.dumps(dump, ensure_ascii=False)) > 2_000_000:
                log.warning("[OpenOps][session-state] state_json 超 2MB（考虑 offload/压缩）session=%s",
                            run["framework_session_id"])
            await agent_session_states.upsert_state_json(
                str(run["framework_session_id"]), dump, "main", st.user_id)
            state_persisted = True
        except Exception:  # noqa: BLE001 —— 旧库未迁移/序列化异常不阻断终态收口
            log.warning("[OpenOps][session-state] AgentState 回写失败 session=%s", run["framework_session_id"])

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
        _system_prompt = _build_system_prompt(st)  # 用户人设(main.role+append) + 平台规则 + skill_hint
        agent = Agent(
            name="sre-rca",
            system_prompt=_system_prompt,
            model=await _build_model(st),
            toolkit=toolkit,
            state=agent_state,
            middlewares=studio.agent_middlewares(),  # Agent Studio span 捕获（关闭/降级时 []）
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
                    # model 取自 model_spec（EndEvent 不带 model_name）：主/子异模型后成对事件才能对上模型名
                    await emit(st, run, "openops.model.call.succeeded", action="model_call", message="模型推理完成",
                               payload={"model": (st.model_spec or {}).get("model_id"),
                                        "input_tokens": ev.input_tokens, "output_tokens": ev.output_tokens})
                elif isinstance(ev, TextBlockDeltaEvent):
                    # 助手文本增量（B5）：只发 SSE 供 AG-UI 流翻译成 TEXT_MESSAGE_*，不写审计（增量非事实）
                    events.publish(st.run_id, events.envelope(
                        st.run_id, "openops.assistant.delta", task_id=st.task_id,
                        payload={"delta": ev.delta, "message_id": ev.block_id},
                    ))
                elif isinstance(ev, ThinkingBlockDeltaEvent):
                    # 模型思考增量：只发 SSE 供 AG-UI 翻译成 REASONING_MESSAGE_*（前端 CopilotKit v2 折叠卡），
                    # 与文本增量同处理——不写审计、不进 transcript（增量非事实，且思考只做实时展示不持久化）。
                    events.publish(st.run_id, events.envelope(
                        st.run_id, "openops.assistant.thinking.delta", task_id=st.task_id,
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
                # 面板在场（demo 剧本或模型自报）才有可更新对象；真流程无面板时不得用 rca_demo 剧本造假
                # ——曾在真对话结束时弹出「支付延迟突增/H1 连接泄漏」假 RCA 卡（与「根因 H1」误报同族）。
                # 安全事实优先：拒绝/超时结论覆盖模型面板并拉回「进行中」（恢复未执行≠闭环；
                # steps/phaseLabel/revision 由 reopen_with_conclusion 统一重派生，防矛盾形状）。
                if st.rca:
                    st.rca = reopen_with_conclusion(st.rca, fallback_conclusion)
                    await emit(st, run, "openops.rca.updated", message="恢复动作未执行", payload=st.rca)
                if decision == "timeout":
                    await emit(st, run, "openops.approval.timeout", severity="warning",
                               message="批准超时：恢复动作未执行", reason_code="APPROVAL_TIMEOUT")
            inputs = UserConfirmResultEvent(
                reply_id=require_ev.reply_id,
                confirm_results=[ConfirmResult(confirmed=confirmed, tool_call=tc) for tc in require_ev.tool_calls],
            )

        # P3：记忆落库必须先于任何「完成」信号可见——包括下面的内存态 st.status 翻转
        # （/state 轮询读的就是内存 TaskState）与 task.completed 事件。晚于信号的回写会让
        # 紧接着的下一个 task 恢复到旧记忆（同 run 失忆竞态）。
        await _persist_state()
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
                # 落审计；但不得用 rca_demo 剧本骨架造假面板（曾在真对话结束时弹出假「RCA 决策面板」卡）。
                # 安全事实优先覆盖 conclusion 并拉回「进行中」（恢复被拦≠闭环；steps/phaseLabel/
                # revision 由 reopen_with_conclusion 统一重派生，防「五步全 done + in_progress」矛盾形状）。
                if st.rca:
                    st.rca = reopen_with_conclusion(st.rca, fallback_conclusion)
                    await emit(st, run, "openops.rca.updated", message="恢复动作被拦截，未执行", payload=st.rca)
            else:
                # 已执行恢复（或本就无需 ASK）：采纳模型生成的结论（GLM 真实结论 / stub 脚本结论）。
                # A4：仅 demo 面板或面板尚无 conclusion 时才覆盖——模型经 update_diagnosis_board 提交的
                # 诊断结论是结构化事实，不得被最终对话文本顶掉
                conclusion = _final_text(agent)
                if conclusion and st.rca and (st.rca_source == "demo"
                                              or not str(st.rca.get("conclusion") or "").strip()):
                    st.rca = {**st.rca, "conclusion": conclusion,
                              "revision": int(st.rca.get("revision") or 0) + 1}
                    await emit(st, run, "openops.rca.updated", message="结论已更新（模型生成）", payload=st.rca)
                # 「根因 H1…」是 demo 剧本文案：仅 demo 恢复流真跑过（rca_source=="demo"）才用；
                # 模型自报面板（rca_source=="model"）与真工具运行一律中性「任务完成」
                msg = ("任务完成：根因 H1，已按审批执行恢复"
                       if st.rca and st.rca_source == "demo" else "任务完成")
            payload = ({"conclusion": st.rca.get("conclusion")} if st.rca
                       else {"conclusion": fallback_conclusion} if fallback_conclusion else None)
            await _persist_state()  # 先持久化再发完成事件（事件可见 ⇒ 记忆已落库）
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
        await _persist_state()  # 同 completed：task.failed 可见前记忆已落库
        await emit(st, run, "openops.task.failed", severity="error", action="task",
                   message=f"任务失败：{reason[:160]}", reason_code="MODEL_CALL_FAILED",
                   payload={"error": reason})
    finally:
        studio.reset_task_context(_studio_tok)
        # P3：终态回写兜底（取消/completed 分支未走到等路径；已持久化则幂等跳过）
        await _persist_state()
