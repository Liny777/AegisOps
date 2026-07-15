"""AG-UI 运行态端点服务（B5，30.4）：把 per-run `openops.*` 事件通道翻译成 AG-UI 标准事件流。

三层模型（30.4）：
- AG-UI 标准事件（RUN_STARTED / TEXT_MESSAGE_* / TOOL_CALL_* / RUN_FINISHED / RUN_ERROR）承载对话主区；
- `openops.*` 自定义事件原样以 CUSTOM(name=event_type, value=envelope) 透传，驱动活动线 / RCA 卡 / ASK 卡；
- 本流只送本次 task 的新事件；断线 / 刷新由 sidecar `/connect` 优先回放内存事件，内存 miss 再读
  `GET /agent-runs/{id}/messages` 的持久 transcript。

wire 契约对齐 @ag-ui/client(0.0.56)：POST RunAgentInput(threadId/runId/messages/...)，
响应 text/event-stream，每条 `data: {json}`，字段 camelCase（messageId/delta/toolCallId/toolCallName…）。
"""
from __future__ import annotations

import asyncio
import logging
import json
import uuid
from dataclasses import dataclass
from typing import Any, AsyncGenerator

from app import run_state_service
from domain.errors import ApiError, Err
from infra.repositories import agent_session_states, runs
from runtime import events, task_registry

KEEPALIVE_S = 15.0
HARD_TIMEOUT_S = 1800.0


@dataclass
class _TaskReq:  # run_state_service.start_task 的 DTO 形状（client_request_id + input_text）
    client_request_id: str
    input_text: str


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _last_user_text(body: dict[str, Any]) -> str:
    for m in reversed(body.get("messages") or []):
        if m.get("role") == "user" and isinstance(m.get("content"), str) and m["content"].strip():
            return m["content"].strip()
    return ""


def project_transcript(state_json: dict[str, Any] | None, run_id: str) -> list[dict[str, str]]:
    """AgentState.model_dump() → 带稳定消息 ID 的用户可见对话。

    对话历史在 AgentState.context（list[Msg]）；只取 user/assistant 两种 role，content blocks 里只留
    TextBlock（type=="text"），丢弃 thinking / tool_call / tool_result / hint / data（工具噪声不进 transcript）。
    AgentScope 2.x 的 Msg.id 会随 AgentState 持久化；兼容缺 id 的旧数据时，用 run + context 序号生成
    稳定 fallback，保证 CopilotKit 重连时可以按 id 正确替换而非重复追加。
    """
    out: list[dict[str, str]] = []
    for index, m in enumerate((state_json or {}).get("context", []) or []):
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        text = "\n".join(
            b.get("text", "") for b in (m.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text"
        )
        if text.strip():
            message_id = str(m.get("id") or f"history-{run_id}-{index}")
            out.append({"id": message_id, "role": role, "content": text})
    return out


async def _load_transcript(run_id: str) -> list[dict[str, str]]:
    """重进会话时的历史对话：run → framework_session_id → 已持久化的 AgentState → 投影。"""
    run = await runs.get_run(run_id)
    if not run:
        return []
    state = await agent_session_states.get_state_json(str(run["framework_session_id"]), "main")
    return project_transcript(state, run_id)


async def start(user: dict[str, Any], run_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """校验 + 订阅事件通道 + 启动 task（在流开始前完成，错误走标准错误 envelope）。"""
    text = _last_user_text(body)
    agui_run_id = str(body.get("runId") or uuid.uuid4())
    if not text:
        raise ApiError(Err.VALIDATION_FAILED, "RunAgentInput.messages 缺少用户文本")
    q, _replay, _ = events.subscribe(run_id, None)  # 只订阅新事件（历史恢复走 runner /connect）
    try:
        task = await run_state_service.start_task(
            user, run_id, _TaskReq(client_request_id=f"agui_{agui_run_id}", input_text=text)
        )
    except BaseException:
        events.unsubscribe(run_id, q)
        raise
    return {
        "queue": q,
        "run_id": run_id,
        "task_id": task["task_id"],
        "thread_id": str(body.get("threadId") or run_id),
        "agui_run_id": agui_run_id,
        "user": user,  # 取消桥：断流时以发起人身份取消任务
    }


log = logging.getLogger("openops.agui")

_cancel_tasks: set[asyncio.Task] = set()  # 断流取消桥 task 强引用（连带 C）


def _schedule_cancel_on_disconnect(ctx: dict[str, Any]) -> None:
    """客户端断流（CopilotChat 停止按钮 / 关页）→ 停止 Agent 运行（取消桥，Part B）。

    只在本 task 仍 running 时取消（正常终态在 return 前已推进状态，不会误伤）。
    GeneratorExit 处理器内禁止 await —— fire-and-forget 到事件循环。
    """
    st = task_registry.get_by_task(ctx["task_id"])
    if st is None or st.status != "running":
        return

    async def _do() -> None:
        try:
            await run_state_service.cancel_task(ctx["user"], ctx["task_id"])
        except Exception:  # noqa: BLE001 —— 已终态/权限竞态等，尽力而为
            pass

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # 连带 C：非事件循环上下文的生成器 close()——放弃取消，勿抛
        log.warning("[OpenOps][agui] 断流取消桥无事件循环上下文，跳过（task=%s）", ctx["task_id"])
        return
    t = loop.create_task(_do())
    _cancel_tasks.add(t)  # 连带 C：强引用防 GC（fire-and-forget task 无引用可能提前回收）
    t.add_done_callback(_cancel_tasks.discard)


async def _await_terminal_persistence(ctx: dict[str, Any]) -> None:
    """终态 AG-UI 帧必须晚于 runtime finally 中的 AgentState 回写。

    AgentScope 先 publish task.* 事件，再在 run_task.finally 持久化 state。若这里立即发
    RUN_FINISHED/RUN_ERROR，用户在完成瞬间刷新可能先命中空 transcript。等待同一个 orchestrator
    收口即可关闭这个窗口；mock runtime 没有 AgentState，但同样安全地等待任务结束。
    """
    st = task_registry.get_by_task(ctx["task_id"])
    orchestrator = st.orchestrator if st is not None else None
    if orchestrator is None or orchestrator.done() or orchestrator is asyncio.current_task():
        return
    try:
        await asyncio.shield(orchestrator)
    except asyncio.CancelledError:
        # 任务自身取消属于 cancelled 终态；若是当前 stream 被取消则继续向上传播。
        if not orchestrator.cancelled():
            raise
    except Exception:  # runtime 已负责发 failed/log；不要用持久化等待覆盖原终态帧
        pass


async def stream(ctx: dict[str, Any]) -> AsyncGenerator[str, None]:
    """事件翻译主循环：openops.* → AG-UI；task 终态 → RUN_FINISHED / RUN_ERROR。"""
    q: asyncio.Queue[dict[str, Any]] = ctx["queue"]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + HARD_TIMEOUT_S
    open_msg: str | None = None  # 当前打开的 TEXT_MESSAGE（messageId）
    streamed = False  # 是否已流式输出过非空文本（决定 task.completed 是否需要合成结论消息）
    open_tool_calls: set[str] = set()
    legacy_tool_stack: list[str] = []  # 兼容没有 tool_call_id 的旧事件；新事件不再依赖到达顺序

    def text_start(mid: str) -> str:
        return _sse({"type": "TEXT_MESSAGE_START", "messageId": mid, "role": "assistant"})

    def text_end(mid: str) -> str:
        return _sse({"type": "TEXT_MESSAGE_END", "messageId": mid})

    try:
        yield _sse({"type": "RUN_STARTED", "threadId": ctx["thread_id"], "runId": ctx["agui_run_id"]})
        while True:
            try:
                e = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_S)
            except asyncio.TimeoutError:
                if loop.time() > deadline:
                    yield _sse({"type": "RUN_ERROR", "message": "运行超时"})
                    return
                yield ": keepalive\n\n"
                continue

            et = str(e.get("event_type", ""))
            p = (e.get("payload_redacted_json") or {})

            # 文本增量：openops.assistant.delta（内部传输事件）→ TEXT_MESSAGE_*
            if et == "openops.assistant.delta":
                delta = str(p.get("delta") or "")
                if not delta:
                    continue
                mid = str(p.get("message_id") or "assistant")
                if open_msg != mid:
                    if open_msg is not None:
                        yield text_end(open_msg)
                    open_msg = mid
                    yield text_start(mid)
                streamed = True
                yield _sse({"type": "TEXT_MESSAGE_CONTENT", "messageId": mid, "delta": delta})
                continue
            if open_msg is not None:  # 其它事件到来即闭合文本块（AG-UI 消息内不插其它事件）
                yield text_end(open_msg)
                open_msg = None

            # 终态先出文本再透传 CUSTOM：前端先以 messageId=event_id 建结论气泡，
            # 再收到 CUSTOM(task.*) 时按同 id 去重，避免文本双写
            if et == "openops.task.completed":
                conclusion = str(p.get("conclusion") or e.get("message") or "")
                if not streamed and conclusion:  # 未流式过（mock / 无 delta）→ 合成一条结论消息
                    mid = str(e.get("event_id") or uuid.uuid4())
                    yield text_start(mid)
                    yield _sse({"type": "TEXT_MESSAGE_CONTENT", "messageId": mid, "delta": conclusion})
                    yield text_end(mid)
                yield _sse({"type": "CUSTOM", "name": et, "value": e})
                await _await_terminal_persistence(ctx)
                yield _sse({"type": "RUN_FINISHED", "threadId": ctx["thread_id"], "runId": ctx["agui_run_id"]})
                return
            if et == "openops.task.cancelled":
                mid = str(e.get("event_id") or uuid.uuid4())
                yield text_start(mid)
                yield _sse({"type": "TEXT_MESSAGE_CONTENT", "messageId": mid, "delta": str(e.get("message") or "任务已取消")})
                yield text_end(mid)
                yield _sse({"type": "CUSTOM", "name": et, "value": e})
                await _await_terminal_persistence(ctx)
                yield _sse({"type": "RUN_FINISHED", "threadId": ctx["thread_id"], "runId": ctx["agui_run_id"]})
                return

            # 自定义事件全量透传（活动线 / RCA / ASK / scope / model 都吃这个）
            yield _sse({"type": "CUSTOM", "name": et, "value": e})

            # 标准工具事件（30.4 五：tool.call.* 同时走标准 + 自定义）
            if et == "openops.tool.call.started":
                payload_tcid = p.get("tool_call_id")
                tcid = str(payload_tcid or e.get("event_id") or uuid.uuid4())
                open_tool_calls.add(tcid)
                if not payload_tcid:
                    legacy_tool_stack.append(tcid)
                yield _sse({"type": "TOOL_CALL_START", "toolCallId": tcid,
                            "toolCallName": str(p.get("tool") or "tool")})
                # 入参 → TOOL_CALL_ARGS（内网实测缺口：不发它 CopilotChat 工具卡 arguments 恒空）
                if p.get("arguments") is not None:
                    yield _sse({"type": "TOOL_CALL_ARGS", "toolCallId": tcid,
                                "delta": json.dumps(p["arguments"], ensure_ascii=False)})
            elif et in ("openops.tool.call.succeeded", "openops.tool.call.failed"):
                payload_tcid = p.get("tool_call_id")
                tcid = str(payload_tcid) if payload_tcid else (legacy_tool_stack.pop() if legacy_tool_stack else "")
                if not tcid or tcid not in open_tool_calls:
                    # 缺 started 的终态不能制造孤立 TOOL_CALL_END/RESULT；CUSTOM 仍已在上方透传供审计查看。
                    continue
                open_tool_calls.discard(tcid)
                yield _sse({"type": "TOOL_CALL_END", "toolCallId": tcid})
                # Result 正文优先工具真实输出（succeeded=payload.result_summary / failed=payload.error），
                # 回退事件 message——内网实测：只发 message 时工具卡 Result 恒为「xxx 返回」占位文案。
                # result_summary 与回给 LLM 的 ToolResponse 同源（http_mcp_client._summarize）；4000 字防撑卡
                body = str(
                    p.get("result_summary") or p.get("error_summary") or p.get("error") or e.get("message") or ""
                )[:4000]
                yield _sse({"type": "TOOL_CALL_RESULT", "messageId": str(e.get("event_id") or uuid.uuid4()),
                            "toolCallId": tcid, "content": body, "role": "tool"})

            # 终态（completed/cancelled 已在上方先文本后 CUSTOM 提前返回）
            elif et == "openops.task.failed":
                # 先合成失败文本气泡再发 RUN_ERROR（对齐 completed/cancelled）：CopilotChat 只渲染
                # TEXT_MESSAGE_*，此前只发 RUN_ERROR → onError=console → 聊天区空白「没响应」（内网教训）
                fail_text = str(e.get("message") or (p.get("error") and f"任务失败：{p['error']}") or "任务失败，请重试或联系管理员")
                mid = str(e.get("event_id") or uuid.uuid4())
                yield text_start(mid)
                yield _sse({"type": "TEXT_MESSAGE_CONTENT", "messageId": mid, "delta": fail_text})
                yield text_end(mid)
                err: dict[str, Any] = {"type": "RUN_ERROR", "message": fail_text}
                if e.get("reason_code"):
                    err["code"] = str(e["reason_code"])  # zod optional：无值时不带 key
                await _await_terminal_persistence(ctx)
                yield _sse(err)
                return
            elif et == "openops.run.closed":
                yield _sse({"type": "RUN_FINISHED", "threadId": ctx["thread_id"], "runId": ctx["agui_run_id"]})
                return
    except (asyncio.CancelledError, GeneratorExit):
        _schedule_cancel_on_disconnect(ctx)
        raise
    finally:
        events.unsubscribe(ctx["run_id"], q)
