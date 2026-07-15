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


async def test_real_openai_compatible_model_is_constructed_with_streaming(monkeypatch):
    import agentscope.model

    captured: dict = {}

    class FakeOpenAIChatModel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(agentscope.model, "OpenAIChatModel", FakeOpenAIChatModel)
    monkeypatch.setenv("OPENOPS_TEST_MODEL_KEY", "test-key-not-a-secret")
    state = TaskState(
        task_id="stream-model-task",
        run_id="stream-model-run",
        user_id="stream-model-user",
        instance_id="stream-model-instance",
        input_text="question",
    )
    state.model_spec = {
        "model_id": "openai-compatible-test-model",
        "secret_env_var": "OPENOPS_TEST_MODEL_KEY",
        "base_url": "https://model.example.invalid/v1",
    }

    await agentscope_runtime._build_model(state)
    assert captured["stream"] is True
    await captured["client_kwargs"]["http_client"].aclose()
