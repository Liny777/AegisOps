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

def test_agui_tool_call_pairing(client):
    """TOOL_CALL_START/END 按调用配对（toolCallId 一致）。"""
    _annotate_recover_no_ask(client)
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    evs = _collect_events(client, run["agent_run_id"], _run_input())
    starts = [e["toolCallId"] for e in evs if e["type"] == "TOOL_CALL_START"]
    ends = [e["toolCallId"] for e in evs if e["type"] == "TOOL_CALL_END"]
    assert starts and starts == ends  # 顺序执行、一一配对


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
