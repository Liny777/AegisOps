from __future__ import annotations

import json
import time

from conftest import ADMIN_HEADERS, OTHER_HEADERS, USER_HEADERS, create_instance, create_run, unwrap, wait_until


def _run_input(text: str = "支付延迟突增，帮我定位") -> dict:
    return {
        "threadId": "th_test", "runId": f"agui_{time.time_ns()}", "state": {},
        "messages": [{"id": "m1", "role": "user", "content": text}],
        "tools": [], "context": [], "forwardedProps": {},
    }


def _annotate_recover_no_ask(client) -> None:
    """把 recover_execute 标注为免审批：AG-UI 流不经 ASK 一次跑完（流内无并发审批人）。"""
    catalog = unwrap(client.get("/api/openops/v1/admin/mcp-tools", headers=ADMIN_HEADERS))
    rec = next(t for t in catalog if t["tool_name"] == "recover_execute")
    unwrap(client.put(
        f"/api/openops/v1/admin/mcp-tools/{rec['tool_catalog_id']}/annotation",
        headers=ADMIN_HEADERS,
        json={"is_approval_required": False, "is_secret_required": False, "scope_mode": "required",
              "appid_arg_path": "$.appid", "status": "allowed"},
    ))


def _collect_events(client, run_id: str, body: dict) -> list[dict]:
    out: list[dict] = []
    with client.stream("POST", f"/api/openops/v1/agent-runs/{run_id}/agui",
                       headers=USER_HEADERS, json=body) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data:"):
                out.append(json.loads(line[5:].strip()))
    return out


def test_agui_full_flow_standard_and_custom_events(client):
    """AG-UI 流：RUN_STARTED 开、RUN_FINISHED 收；标准 TOOL_CALL/TEXT_MESSAGE 与 CUSTOM(openops.*) 并存（30.4）。"""
    _annotate_recover_no_ask(client)
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    evs = _collect_events(client, run["agent_run_id"], _run_input())

    types = [e["type"] for e in evs]
    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"
    assert "TOOL_CALL_START" in types and "TOOL_CALL_END" in types and "TOOL_CALL_RESULT" in types
    # 工具入参 → TOOL_CALL_ARGS（内网实测缺口：不发它前端工具卡 arguments 恒空）
    args_evs = [e for e in evs if e["type"] == "TOOL_CALL_ARGS"]
    assert args_evs, "tool.call.started 带 arguments 时必须翻译出 TOOL_CALL_ARGS"
    import json as _json

    assert isinstance(_json.loads(args_evs[0]["delta"]), dict)  # delta 是可解析的 JSON 入参
    start_ids = {e["toolCallId"] for e in evs if e["type"] == "TOOL_CALL_START"}
    assert all(a["toolCallId"] in start_ids for a in args_evs)  # 与所属调用配对
    # Result 正文=工具真实输出（result_summary），不是「xxx 返回」占位（内网实测缺口）
    summaries = [str((e["value"].get("payload_redacted_json") or {}).get("result_summary") or "")
                 for e in evs if e["type"] == "CUSTOM" and e["name"] == "openops.tool.call.succeeded"]
    results = [e["content"] for e in evs if e["type"] == "TOOL_CALL_RESULT"]
    assert results and any(s and s[:100] in r for s in summaries for r in results)
    # 对话主区有文本（mock 无增量 → task.completed 合成结论消息）
    assert "TEXT_MESSAGE_START" in types and "TEXT_MESSAGE_CONTENT" in types and "TEXT_MESSAGE_END" in types
    # openops.* 自定义事件透传（活动线 / RCA）
    customs = {e["name"] for e in evs if e["type"] == "CUSTOM"}
    assert "openops.tool.call.started" in customs
    assert "openops.rca.updated" in customs
    assert "openops.task.completed" in customs
    # envelope 原样在 value 里（sequence 供前端去重）
    rca = next(e for e in evs if e["type"] == "CUSTOM" and e["name"] == "openops.rca.updated")
    assert rca["value"]["sequence"] > 0
    assert rca["value"]["payload_redacted_json"]["revision"] >= 1

def test_agui_agentscope_stub_streams_reasoning_card(client, monkeypatch):
    """端到端（agentscope stub runtime）：stub 首步的 ThinkingBlock → 运行时事件桥
    openops.assistant.thinking.delta → AG-UI REASONING_MESSAGE_*（role=reasoning），且思考在正式答案
    文本之前。覆盖「运行时循环里 ThinkingBlockDeltaEvent→SSE」这段仅靠单测触不到的胶水。"""
    monkeypatch.setenv("OPENOPS_RUNTIME", "agentscope")
    _annotate_recover_no_ask(client)
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    evs = _collect_events(client, run["agent_run_id"], _run_input())

    types = [e["type"] for e in evs]
    assert "REASONING_MESSAGE_START" in types, types
    r_start = next(e for e in evs if e["type"] == "REASONING_MESSAGE_START")
    assert r_start["role"] == "reasoning"
    r_content = "".join(e["delta"] for e in evs if e["type"] == "REASONING_MESSAGE_CONTENT")
    assert "Redis" in r_content  # stub 首步思考文案（不校验全文，只认关键词，容忍文案微调）
    assert "REASONING_MESSAGE_END" in types
    # 思考卡在正式答案文本之前（reasoning 卡排在答案气泡上方）。
    assert types.index("REASONING_MESSAGE_START") < types.index("TEXT_MESSAGE_START")
    # 每个 REASONING_MESSAGE_START 都有配对的 END（不悬挂）。
    r_starts = [e["messageId"] for e in evs if e["type"] == "REASONING_MESSAGE_START"]
    r_ends = [e["messageId"] for e in evs if e["type"] == "REASONING_MESSAGE_END"]
    assert sorted(r_starts) == sorted(r_ends)
    assert types[-1] == "RUN_FINISHED"


def test_agui_stub_board_full_chain_emits_monotonic_rca_until_concluded(client, monkeypatch):
    """OPENOPS_DEMO_BOARD_STEP=1 stub 全链路：update_diagnosis_board → rca_board 状态机 →
    openops.rca.updated CUSTOM ≥2 次、revision 严格单调、末次 status=concluded 且 steps 合法；
    demo 剧本被模型面板接管后，完结不再用「根因 H1」demo 文案、工具提交的 conclusion 不被覆盖。"""
    import pytest as _pytest

    _pytest.importorskip("agentscope")  # 本机未装 agentscope 时跳过（见既有环境性失败清单）
    monkeypatch.setenv("OPENOPS_RUNTIME", "agentscope")
    monkeypatch.setenv("OPENOPS_DEMO_BOARD_STEP", "1")
    _annotate_recover_no_ask(client)
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    evs = _collect_events(client, run["agent_run_id"], _run_input())

    boards = [e["value"]["payload_redacted_json"] for e in evs
              if e["type"] == "CUSTOM" and e["name"] == "openops.rca.updated"]
    assert len(boards) >= 2
    revs = [b["revision"] for b in boards]
    assert revs == sorted(revs) and len(set(revs)) == len(revs), revs  # 服务端自增：严格单调不重号

    last = boards[-1]
    assert last["status"] == "concluded"
    assert [s["num"] for s in last["steps"]] == [1, 2, 3, 4, 5]
    assert all(s["state"] == "done" for s in last["steps"])
    assert {s["label"] for s in last["steps"]} == {"范围", "证据", "假设", "验证", "结论"}
    # 工具提交的定界结论原样保留（不被 _final_text 覆盖）；demo 剧本内容已被丢弃
    assert last["conclusion"].startswith("已确认 H1（Redis 连接泄漏）")
    assert last["title"] == "支付下单 P99 突增"

    done = next(e for e in evs if e["type"] == "CUSTOM" and e["name"] == "openops.task.completed")
    assert done["value"]["message"] == "任务完成"  # rca_source=model：不再用 demo「根因 H1」完结文案
    assert [e["type"] for e in evs][-1] == "RUN_FINISHED"


def test_agui_tool_call_pairing(client):
    """TOOL_CALL_START/END 按调用配对（toolCallId 一致）。"""
    _annotate_recover_no_ask(client)
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    evs = _collect_events(client, run["agent_run_id"], _run_input())
    starts = [e["toolCallId"] for e in evs if e["type"] == "TOOL_CALL_START"]
    ends = [e["toolCallId"] for e in evs if e["type"] == "TOOL_CALL_END"]
    assert starts and starts == ends  # 顺序执行、一一配对


def test_agui_streams_multiple_deltas_before_finish_without_terminal_duplicate():
    """模型增量逐条透传；收到过 delta 后 task.completed 的累计结论不能再合成一次。"""
    import asyncio

    from app import agui_service
    from runtime import events

    async def scenario() -> list[dict]:
        rid = "run-stream-delta-test"
        q, _replay, _ = events.subscribe(rid, None)
        ctx = {
            "queue": q, "run_id": rid, "task_id": "task-stream", "thread_id": "thread-stream",
            "agui_run_id": "agui-stream", "user": {},
        }
        for delta in ("第一段", "，第二段", "，第三段"):
            events.publish(rid, events.envelope(
                rid,
                "openops.assistant.delta",
                task_id="task-stream",
                payload={"delta": delta, "message_id": "assistant-stream"},
            ))
        events.publish(rid, events.envelope(
            rid,
            "openops.task.completed",
            task_id="task-stream",
            message="任务完成",
            payload={"conclusion": "第一段，第二段，第三段"},
        ))

        frames: list[dict] = []
        async for line in agui_service.stream(ctx):
            if line.startswith("data:"):
                frame = json.loads(line[5:].strip())
                frames.append(frame)
                if frame["type"] == "RUN_FINISHED":
                    break
        return frames

    frames = asyncio.run(scenario())
    deltas = [frame["delta"] for frame in frames if frame["type"] == "TEXT_MESSAGE_CONTENT"]
    assert deltas == ["第一段", "，第二段", "，第三段"]
    assert len([frame for frame in frames if frame["type"] == "TEXT_MESSAGE_START"]) == 1
    assert frames[-1]["type"] == "RUN_FINISHED"


def test_agui_translates_thinking_delta_to_reasoning_message():
    """模型思考增量 openops.assistant.thinking.delta → AG-UI REASONING_MESSAGE_*（role=reasoning，
    CopilotKit v2 原生折叠卡）；与文本块互斥：正式答案开始前思考块先 END（卡片落定「已思考 Xs」），
    思考 messageId 与文本 messageId 不同（前端是两条独立消息）。"""
    import asyncio

    from app import agui_service
    from runtime import events

    async def scenario() -> list[dict]:
        rid = "run-thinking-test"
        q, _replay, _ = events.subscribe(rid, None)
        ctx = {"queue": q, "run_id": rid, "task_id": "task-think", "thread_id": "thread-think",
               "agui_run_id": "agui-think", "user": {}}
        for delta in ("先看指标：", "Redis 连接接近饱和，", "假设 H1=连接泄漏。"):
            events.publish(rid, events.envelope(
                rid, "openops.assistant.thinking.delta", task_id="task-think",
                payload={"delta": delta, "message_id": "think-1"}))
        for delta in ("已定位根因，", "重启 svc-a 后 P99 恢复。"):
            events.publish(rid, events.envelope(
                rid, "openops.assistant.delta", task_id="task-think",
                payload={"delta": delta, "message_id": "assistant-1"}))
        events.publish(rid, events.envelope(
            rid, "openops.task.completed", task_id="task-think",
            message="done", payload={"conclusion": "已定位根因，重启 svc-a 后 P99 恢复。"}))

        frames: list[dict] = []
        async for line in agui_service.stream(ctx):
            if line.startswith("data:"):
                frame = json.loads(line[5:].strip())
                frames.append(frame)
                if frame["type"] == "RUN_FINISHED":
                    break
        return frames

    frames = asyncio.run(scenario())
    types = [f["type"] for f in frames]
    # reasoning 三件套齐全、role=reasoning、内容逐段透传
    r_start = next(f for f in frames if f["type"] == "REASONING_MESSAGE_START")
    assert r_start["role"] == "reasoning"
    r_deltas = [f["delta"] for f in frames if f["type"] == "REASONING_MESSAGE_CONTENT"]
    assert r_deltas == ["先看指标：", "Redis 连接接近饱和，", "假设 H1=连接泄漏。"]
    assert "REASONING_MESSAGE_END" in types
    # 单个 reasoning 块（消费顺序内未被打断成多段）
    assert len([t for t in types if t == "REASONING_MESSAGE_START"]) == 1
    # 思考 messageId 一致，且与文本消息 id 不同（两条独立消息）
    r_id = r_start["messageId"]
    assert all(f.get("messageId") == r_id for f in frames
               if f["type"].startswith("REASONING_MESSAGE_"))
    t_start = next(f for f in frames if f["type"] == "TEXT_MESSAGE_START")
    assert t_start["messageId"] != r_id
    # 互斥：思考块在正式答案（文本）开始前已闭合
    assert types.index("REASONING_MESSAGE_END") < types.index("TEXT_MESSAGE_START")
    # 文本仍完整、结论不被 task.completed 二次合成
    t_deltas = [f["delta"] for f in frames if f["type"] == "TEXT_MESSAGE_CONTENT"]
    assert t_deltas == ["已定位根因，", "重启 svc-a 后 P99 恢复。"]
    assert frames[-1]["type"] == "RUN_FINISHED"


def test_agui_reasoning_and_text_interleave_never_reuses_message_id():
    """真模型把 reasoning_content 与 content 混在同一 delta（AgentScope 先出 text 再出 thinking）时，
    文本/思考块会反复交错重开——每次重开必须换新 messageId，绝不复用已 END 的 id，守住 AG-UI
    「一个 messageId 只有一个 START…END 生命周期」，否则前端 @ag-ui/client 报错/串块/丢内容。"""
    import asyncio

    from app import agui_service
    from runtime import events

    async def scenario() -> list[dict]:
        rid = "run-interleave-test"
        q, _replay, _ = events.subscribe(rid, None)
        ctx = {"queue": q, "run_id": rid, "task_id": "task-il", "thread_id": "th-il",
               "agui_run_id": "ar-il", "user": {}}
        seq = [
            ("openops.assistant.thinking.delta", {"delta": "C1", "message_id": "r"}),
            ("openops.assistant.delta", {"delta": "X1", "message_id": "a"}),
            ("openops.assistant.thinking.delta", {"delta": "C2", "message_id": "r"}),  # 同源 id 重开
            ("openops.assistant.delta", {"delta": "X2", "message_id": "a"}),            # 同源 id 重开
        ]
        for et, payload in seq:
            events.publish(rid, events.envelope(rid, et, task_id="task-il", payload=payload))
        events.publish(rid, events.envelope(rid, "openops.task.completed", task_id="task-il",
                                            message="done", payload={"conclusion": "done"}))
        frames: list[dict] = []
        async for line in agui_service.stream(ctx):
            if line.startswith("data:"):
                f = json.loads(line[5:].strip())
                frames.append(f)
                if f["type"] == "RUN_FINISHED":
                    break
        return frames

    frames = asyncio.run(scenario())

    def _pairs(kind: str) -> tuple[list[str], list[str]]:
        starts = [f["messageId"] for f in frames if f["type"] == f"{kind}_MESSAGE_START"]
        ends = [f["messageId"] for f in frames if f["type"] == f"{kind}_MESSAGE_END"]
        return starts, ends

    for kind in ("REASONING", "TEXT"):
        starts, ends = _pairs(kind)
        assert len(starts) == len(set(starts)), f"{kind} messageId 被复用于多个 START：{starts}"
        assert sorted(starts) == sorted(ends), f"{kind} START/END 未一一配对：{starts} vs {ends}"
    # 交错场景下每块都被拆成独立生命周期（各 2 段）：不串块、无悬挂 START。
    r_starts, _ = _pairs("REASONING")
    t_starts, _ = _pairs("TEXT")
    assert len(r_starts) == 2 and len(t_starts) == 2
    # 每个 CONTENT 的 messageId 必属于某个已 START 的块（内容不落到未开的 id 上）。
    started = set(r_starts) | set(t_starts)
    for f in frames:
        if f["type"] in ("REASONING_MESSAGE_CONTENT", "TEXT_MESSAGE_CONTENT"):
            assert f["messageId"] in started
    assert frames[-1]["type"] == "RUN_FINISHED"


def test_agui_correlates_interleaved_tool_events_by_tool_call_id():
    """并发工具即使 A/B 启动、A/B 交错完成，也按 payload ID 关联各自结果。"""
    import asyncio

    from app import agui_service
    from runtime import events

    async def scenario() -> list[dict]:
        rid = "run-interleaved-tools"
        q, _replay, _ = events.subscribe(rid, None)
        ctx = {
            "queue": q, "run_id": rid, "task_id": "task-tools", "thread_id": "thread-tools",
            "agui_run_id": "agui-tools", "user": {},
        }
        envelopes = [
            events.envelope(rid, "openops.tool.call.started", task_id="task-tools",
                            payload={"tool": "tool_a", "tool_call_id": "call-a", "arguments": {"a": 1}}),
            events.envelope(rid, "openops.tool.call.started", task_id="task-tools",
                            payload={"tool": "tool_b", "tool_call_id": "call-b", "arguments": {"b": 2}}),
            events.envelope(rid, "openops.tool.call.succeeded", task_id="task-tools",
                            payload={"tool": "tool_a", "tool_call_id": "call-a", "result_summary": "result-a"}),
            events.envelope(rid, "openops.tool.call.failed", task_id="task-tools",
                            payload={"tool": "tool_b", "tool_call_id": "call-b", "error": "error-b"}),
            events.envelope(rid, "openops.task.completed", task_id="task-tools",
                            message="done", payload={"conclusion": "done"}),
        ]
        for envelope in envelopes:
            events.publish(rid, envelope)

        frames: list[dict] = []
        async for line in agui_service.stream(ctx):
            if line.startswith("data:"):
                frame = json.loads(line[5:].strip())
                frames.append(frame)
                if frame["type"] == "RUN_FINISHED":
                    break
        return frames

    frames = asyncio.run(scenario())
    assert [frame["toolCallId"] for frame in frames if frame["type"] == "TOOL_CALL_START"] == [
        "call-a", "call-b",
    ]
    assert [frame["toolCallId"] for frame in frames if frame["type"] == "TOOL_CALL_END"] == [
        "call-a", "call-b",
    ]
    results = {
        frame["toolCallId"]: frame["content"]
        for frame in frames
        if frame["type"] == "TOOL_CALL_RESULT"
    }
    assert results == {"call-a": "result-a", "call-b": "error-b"}


def test_agui_task_failed_synthesizes_chat_bubble(client):
    """任务失败→先合成 TEXT_MESSAGE 失败气泡再发 RUN_ERROR（对齐 completed/cancelled）：
    此前只发 RUN_ERROR，CopilotChat 只渲染 TEXT_MESSAGE_* → 聊天区空白「没响应」（内网 401 教训）。
    直测 service 层翻译（手喂 task.failed envelope，不依赖真失败的模型）。"""
    import asyncio

    from app import agui_service
    from runtime import events

    async def scenario() -> list[dict]:
        rid = "run-fail-test"
        q, _replay, _ = events.subscribe(rid, None)
        ctx = {"queue": q, "run_id": rid, "task_id": "tsk-fail", "thread_id": "th", "agui_run_id": "ar", "user": {}}
        # 手喂一条带真原因的 task.failed（模拟 401 透出）
        events.publish(rid, events.envelope(
            rid, "openops.task.failed", task_id="tsk-fail", severity="error",
            message="任务失败：Error code: 401 - Invalid API key", reason_code="MODEL_CALL_FAILED",
            payload={"error": "Error code: 401 - Invalid API key"}))
        frames: list[dict] = []
        agen = agui_service.stream(ctx)
        for _ in range(20):
            line = await asyncio.wait_for(agen.__anext__(), timeout=5)
            if line.startswith("data:"):
                f = json.loads(line[5:].strip())
                frames.append(f)
                if f.get("type") == "RUN_ERROR":
                    break
        await agen.aclose()
        return frames

    frames = asyncio.run(scenario())
    types = [f["type"] for f in frames]
    # 失败气泡先于 RUN_ERROR：三件套齐全且带原因文本
    assert "TEXT_MESSAGE_START" in types and "TEXT_MESSAGE_END" in types
    content = next(f for f in frames if f["type"] == "TEXT_MESSAGE_CONTENT")
    assert "401" in content["delta"] and "Invalid API key" in content["delta"]
    assert types.index("TEXT_MESSAGE_START") < types.index("RUN_ERROR")
    run_err = next(f for f in frames if f["type"] == "RUN_ERROR")
    assert run_err["code"] == "MODEL_CALL_FAILED" and "401" in run_err["message"]


def test_agui_owner_isolation(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    r = client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/agui",
                    headers=OTHER_HEADERS, json=_run_input())
    assert r.status_code == 403


def test_agui_closed_run_rejects(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}:close", headers=USER_HEADERS, json={}))
    r = client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/agui",
                    headers=USER_HEADERS, json=_run_input())
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "RUN_ALREADY_CLOSED"


def test_agui_empty_messages_rejected(client):
    """真实 connect 由 sidecar /connect 处理；/agui 的空消息不是合法的新 run 请求。"""
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    body = _run_input()
    body["messages"] = []
    response = client.post(
        f"/api/openops/v1/agent-runs/{run['agent_run_id']}/agui",
        headers=USER_HEADERS,
        json=body,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state", headers=USER_HEADERS))
    assert state.get("active_task") is None  # 未启动 task


def test_agui_disconnect_triggers_cancel_bridge(client):
    """连带 C：消费流后 aclose()（客户端断流的 GeneratorExit 路径）→ 取消桥收口任务。

    经 TestClient 关流不触发 ASGI 断连（实测阻塞至编排 ASK 超时），故直测 service 层生成器。
    """
    import asyncio

    from app import agui_service
    from runtime import task_registry

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    rid = run["agent_run_id"]
    user = {"user_id": "0026demo01"}
    body = {"threadId": rid, "runId": "r-def-c", "messages": [{"role": "user", "content": "断流测试"}]}

    async def scenario() -> str:
        ctx = await agui_service.start(user, rid, body)
        agen = agui_service.stream(ctx)
        first = await asyncio.wait_for(agen.__anext__(), timeout=5)
        assert "RUN_STARTED" in first
        await agen.aclose()  # 断流 → GeneratorExit → _schedule_cancel_on_disconnect
        for _ in range(100):  # 等 fire-and-forget 的取消桥落地
            st = task_registry.get_by_task(ctx["task_id"])
            if st is None or st.status != "running":
                break
            await asyncio.sleep(0.05)
        return st.status if st else "unregistered"

    status = asyncio.run(scenario())
    assert status == "cancelled"


def test_agui_terminal_frame_waits_for_runtime_persistence():
    """task.* publish 早于 AgentState finally 回写；最终 AG-UI 帧必须等待 orchestrator 收口。"""
    import asyncio

    from app import agui_service
    from runtime import task_registry
    from runtime.task_registry import TaskState

    async def scenario() -> None:
        release = asyncio.Event()
        persisted = False

        async def runtime_finally() -> None:
            nonlocal persisted
            await release.wait()
            persisted = True

        state = TaskState(
            task_id="terminal-persistence-task",
            run_id="terminal-persistence-run",
            user_id="user",
            instance_id="instance",
            input_text="question",
        )
        state.orchestrator = asyncio.create_task(runtime_finally())
        task_registry.put(state)
        waiter = asyncio.create_task(agui_service._await_terminal_persistence({"task_id": state.task_id}))
        await asyncio.sleep(0)
        assert not waiter.done() and not persisted
        release.set()
        await waiter
        assert persisted

    try:
        asyncio.run(scenario())
    finally:
        task_registry.reset()


def test_agui_project_transcript_filters_tool_noise():
    """project_transcript：只留 user/assistant 的 text block，滤掉 system/thinking/tool_call/tool_result；
    纯工具调用（无文本）的 assistant 消息跳过。"""
    from app.agui_service import project_transcript

    state = {"context": [
        {"role": "system", "content": [{"type": "text", "text": "SYS"}]},           # system 滤
        {"id": "msg-user", "role": "user", "content": [{"type": "text", "text": "查支付延迟"}]},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "先看日志"},                              # 思考 滤
            {"type": "text", "text": "我先查日志"},
            {"type": "tool_call", "id": "c1", "name": "sh", "input": "{}"},           # 工具调用 滤
            {"type": "tool_result", "id": "c1", "output": [{"type": "text", "text": "out"}]},  # 工具结果 滤
            {"type": "text", "text": "根因是连接池饱和"},
        ]},
        {"role": "assistant", "content": [{"type": "tool_call", "id": "c2", "name": "x", "input": "{}"}]},  # 无文本 跳过
    ]}
    assert project_transcript(state, "run-test") == [
        {"id": "msg-user", "role": "user", "content": "查支付延迟"},
        {"id": "history-run-test-2", "role": "assistant", "content": "我先查日志\n根因是连接池饱和"},
    ]
    assert project_transcript(None, "run-test") == [] and project_transcript({}, "run-test") == []


def test_agui_messages_endpoint_returns_transcript(client):
    """runner 的持久恢复接口返回稳定消息；空/closed run 可读，跨用户不可读。"""
    import asyncio

    from infra.repositories import agent_session_states, runs

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    rid = run["agent_run_id"]

    # 空会话（无 state）→ []
    assert unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/messages", headers=USER_HEADERS)) == []

    async def seed() -> None:
        r = await runs.get_run(rid)
        await agent_session_states.upsert_state_json(str(r["framework_session_id"]), {"context": [
            {"id": "msg-user-x", "role": "user", "content": [{"type": "text", "text": "问题X"}]},
            {"role": "assistant", "content": [
                {"type": "tool_call", "id": "c", "name": "n", "input": "{}"},  # 工具噪声滤掉
                {"type": "text", "text": "回答Y"}]},
        ]}, "main")

    asyncio.run(seed())
    got = unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/messages", headers=USER_HEADERS))
    assert got == [
        {"id": "msg-user-x", "role": "user", "content": "问题X"},
        {"id": f"history-{rid}-1", "role": "assistant", "content": "回答Y"},
    ]

    # 终态会话仍由同一接口恢复历史。
    unwrap(client.post(f"/api/openops/v1/agent-runs/{rid}:close", headers=USER_HEADERS, json={}))
    assert unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/messages", headers=USER_HEADERS)) == got

    # 跨用户 403（owned_run 守卫）
    assert client.get(f"/api/openops/v1/agent-runs/{rid}/messages", headers=OTHER_HEADERS).status_code == 403
