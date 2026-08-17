from __future__ import annotations

from runtime import agentscope_runtime
from runtime.task_registry import TaskState


async def test_stub_model_emits_deterministic_text_deltas_and_cumulative_final():
    model = agentscope_runtime._build_stub_model()
    assert model.stream is True

    # 前三轮是脚本化工具调用；每轮也遵守 streaming 的增量 + 累计终块契约。
    for _ in range(3):
        response = await model._call_api(model.model, [])
        chunks = [chunk async for chunk in response]
        assert [chunk.is_last for chunk in chunks] == [False, True]
        assert chunks[0].content[0].id == chunks[1].content[0].id

    response = await model._call_api(model.model, [])
    chunks = [chunk async for chunk in response]
    assert [chunk.is_last for chunk in chunks] == [False, False, False, True]
    deltas = [chunk.content[0].text for chunk in chunks[:-1]]
    assert deltas == [
        "已确认根因 H1（Redis 连接泄漏）：",
        "重启 svc-a 后连接回落、",
        "P99 恢复 210ms，事件闭环。",
    ]
    assert chunks[-1].content[0].text == "".join(deltas)
    assert len({chunk.content[0].id for chunk in chunks}) == 1


async def test_stub_first_step_emits_thinking_block_before_tool_call():
    """首步在工具调用前附一段思考块（ThinkingBlock）——驱动 agent 产 ThinkingBlockDeltaEvent →
    runtime 发 openops.assistant.thinking.delta → AG-UI REASONING_MESSAGE_*（前端 CopilotKit 折叠卡）。
    仅首步附思考，避免每步刷屏；后续步骤只有工具调用。"""
    from agentscope.message import ThinkingBlock, ToolCallBlock

    model = agentscope_runtime._build_stub_model()

    # step 1（巡检）：首个非终块的 content —— 思考块在前，query_resource 工具调用在后。
    chunks = [c async for c in await model._call_api(model.model, [])]
    content = chunks[0].content
    assert isinstance(content[0], ThinkingBlock) and content[0].thinking.strip()
    tool = next((b for b in content if isinstance(b, ToolCallBlock)), None)
    assert tool is not None and tool.name == "query_resource"  # 思考不取代工具调用

    # step 2（诊断）：只有工具调用，不再附思考。
    step2 = [c async for c in await model._call_api(model.model, [])]
    assert all(not isinstance(b, ThinkingBlock) for b in step2[0].content)


async def test_real_openai_compatible_model_is_constructed_with_streaming(monkeypatch):
    import agentscope.model

    captured: dict = {}

    class FakeOpenAIChatModel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(agentscope.model, "OpenAIChatModel", FakeOpenAIChatModel)
    # 2026-08-17：平台 Key 走 PG 密文列，构建边界瞬时解密——这里桩掉那次解密（不打 DB）
    async def fake_decrypt(_asset_id: str):
        return "test-key-not-a-secret", "fp_test"

    monkeypatch.setattr(agentscope_runtime, "_decrypt_asset_secret", fake_decrypt)
    state = TaskState(
        task_id="stream-model-task",
        run_id="stream-model-run",
        user_id="stream-model-user",
        instance_id="stream-model-instance",
        input_text="question",
    )
    state.model_spec = {
        "model_id": "openai-compatible-test-model",
        "model_asset_id": "11111111-1111-1111-1111-111111111111",
        "base_url": "https://model.example.invalid/v1",
    }

    await agentscope_runtime._build_model(state)
    assert captured["stream"] is True
    await captured["client_kwargs"]["http_client"].aclose()
