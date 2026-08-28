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


# ── 内联 think 切流（2026-08-21 内网换模型）────────────────────────────────────
# 内网网关（GLM-V5.1-DX-32K）把思考混在 delta.content 里、且**不带 `<think>` 开标签**，
# 只在思考结束处吐一个闭标签。下面这批用例照抄那条真流的形态，钉住 runtime/inline_think 的契约。

def _chunk(*blocks, is_last: bool = False):
    from agentscope.model import ChatResponse

    return ChatResponse(content=list(blocks), is_last=is_last)


def _text_stream(deltas: list[str]):
    """把一串正文增量做成 agentscope 契约的流：增量块 + 累计终块（同 block id）。"""
    from agentscope.message import TextBlock

    async def gen():
        for d in deltas:
            yield _chunk(TextBlock(text=d, id="blk-1"))
        yield _chunk(TextBlock(text="".join(deltas), id="blk-1"), is_last=True)

    return gen()


async def _drain(stream) -> tuple[list, list, object]:
    """吃干流 → (思考增量, 正文增量, 终块)。"""
    from agentscope.message import TextBlock, ThinkingBlock

    thinks, texts, final = [], [], None
    async for c in stream:
        if c.is_last:
            final = c
            continue
        # 思考与正文**绝不同块并存**：并存会让 agentscope 同 chunk 先发 TextDelta 再发
        # ThinkingDelta，AG-UI 侧文本/思考互斥就来回开合、块碎裂
        kinds = {type(b) for b in c.content}
        assert not (TextBlock in kinds and ThinkingBlock in kinds), "思考与正文串在同一个 chunk 里了"
        for b in c.content:
            if isinstance(b, ThinkingBlock):
                thinks.append(b.thinking)
            elif isinstance(b, TextBlock):
                texts.append(b.text)
    return thinks, texts, final


# 内网真流截短：无开标签起手，闭标签混在 "语气。</think>你好" 这个普通 chunk 里
_INTRANET_DELTAS = ["收到", "用户发", "来的问候。", "我应该", "用", "类似的",
                    "语气回应。", "语气。</think>你好", "！很高兴", "见到你。"]
_WANT_THINK = "收到用户发来的问候。我应该用类似的语气回应。语气。"
_WANT_TEXT = "你好！很高兴见到你。"


async def test_inline_think_splits_intranet_stream_shape():
    """闭标签之前进思考通道、之后进正文通道，标签本身两边都不留。"""
    from runtime import inline_think

    thinks, texts, final = await _drain(inline_think.split_stream(_text_stream(_INTRANET_DELTAS)))
    assert "".join(thinks) == _WANT_THINK
    assert "".join(texts) == _WANT_TEXT
    assert "</think>" not in "".join(thinks) + "".join(texts)
    # 终块（agentscope 用它拼最终 Msg → _final_text → 结论/告警摘要）必须已经是干净的
    from agentscope.message import TextBlock, ThinkingBlock

    assert [type(b) for b in final.content] == [ThinkingBlock, TextBlock]
    assert final.content[0].thinking == _WANT_THINK
    assert final.content[1].text == _WANT_TEXT


async def test_inline_think_close_tag_split_across_chunks():
    """闭标签被 chunk 切断（"</thi" + "nk>"）→ 结果与不切断时逐字相同。

    不留住尾缀就会把半截标签当正文发出去、后半截再也拼不上——本模块最容易改坏的一处。
    """
    from runtime import inline_think

    split = ["收到", "用户发", "来的问候。", "我应该", "用", "类似的",
             "语气回应。", "语气。</thi", "nk>你好", "！很高兴", "见到你。"]
    thinks, texts, final = await _drain(inline_think.split_stream(_text_stream(split)))
    assert "".join(thinks) == _WANT_THINK
    assert "".join(texts) == _WANT_TEXT
    assert "</thi" not in "".join(thinks) and "nk>" not in "".join(texts)
    assert final.content[1].text == _WANT_TEXT


async def test_inline_think_strips_explicit_open_tag():
    """别的网关若真带 `<think>` 开标签，剥掉不让它漏进思考正文。"""
    from runtime import inline_think

    thinks, texts, _ = await _drain(
        inline_think.split_stream(_text_stream(["<think>先看指标", "。</think>结论：正常"])))
    assert "".join(thinks) == "先看指标。"
    assert "".join(texts) == "结论：正常"


async def test_inline_think_without_close_tag_keeps_answer_as_text():
    """整轮没等到闭标签（白名单误配到不内联思考的模型）→ 终块把内容当正文，答案绝不丢。"""
    from agentscope.message import TextBlock, ThinkingBlock
    from runtime import inline_think

    _thinks, _texts, final = await _drain(
        inline_think.split_stream(_text_stream(["这是一段", "普通答案。"])))
    assert [type(b) for b in final.content] == [TextBlock]
    assert final.content[0].text == "这是一段普通答案。"
    assert not [b for b in final.content if isinstance(b, ThinkingBlock)]


async def test_inline_think_passes_through_tool_calls_and_native_thinking():
    """工具调用块原样透传；网关哪天改吐 reasoning_content，原生思考块也不丢。"""
    from agentscope.message import TextBlock, ThinkingBlock, ToolCallBlock
    from runtime import inline_think

    async def gen():
        yield _chunk(ThinkingBlock(thinking="原生思考"), TextBlock(text="补充。</think>", id="b"))
        yield _chunk(ToolCallBlock(id="tc-1", name="query_resource", input="{}"))
        yield _chunk(TextBlock(text="原生思考补充。", id="b"),
                     ToolCallBlock(id="tc-1", name="query_resource", input="{}"), is_last=True)

    tools, thinks, texts = [], [], []
    async for c in inline_think.split_stream(gen()):
        for b in c.content:
            if isinstance(b, ToolCallBlock):
                tools.append(b)
            elif isinstance(b, ThinkingBlock):
                thinks.append(b.thinking)
            elif isinstance(b, TextBlock):
                texts.append(b.text)
    assert [t.name for t in tools] == ["query_resource", "query_resource"]  # 增量 + 终块各一次
    assert "原生思考" in "".join(thinks) and "补充。" in "".join(thinks)
    assert "</think>" not in "".join(thinks) + "".join(texts)


def test_inline_think_enabled_for_matches_whitelist(monkeypatch):
    """白名单口径：逗号分隔 / `*` 全开 / 空=全关 / 大小写不敏感 / 精确或前缀。

    前缀是刻意的——填 `GLM` 就覆盖 `GLM-V5.1-DX-32K`，网关升版本号不用改配置。
    """
    from runtime import inline_think

    def _set(v: str | None):
        if v is None:
            monkeypatch.delenv(inline_think.ENV_KEY, raising=False)
        else:
            monkeypatch.setenv(inline_think.ENV_KEY, v)

    _set(None)
    assert inline_think.enabled_for("GLM-V5.1-DX-32K") is False  # 未设=全关（默认不改任何模型行为）
    _set("   ")
    assert inline_think.enabled_for("GLM-V5.1-DX-32K") is False
    _set("GLM")
    assert inline_think.enabled_for("GLM-V5.1-DX-32K") is True   # 前缀命中
    assert inline_think.enabled_for("qwen-max") is False          # 同后端上的其他模型零影响
    _set("qwen-max,glm-v5.1-dx-32k")
    assert inline_think.enabled_for("GLM-V5.1-DX-32K") is True   # 多条 + 大小写不敏感
    _set("*")
    assert inline_think.enabled_for("any-model") is True
    assert inline_think.enabled_for("") is False                  # 无 model_id 不匹配 `*`


async def test_build_model_wraps_only_whitelisted_model(monkeypatch):
    """`_build_model` 只对白名单命中的模型换切流子类；不命中时构造的仍是原生类。

    这条守的是「不误伤其他模型」——本平台是多模型形态（平台资产多条 / 模板主子异模型 /
    用户自带 BYO），切流类误套到不内联思考的模型上会让答案迟到（见 finish 的兜底）。
    """
    import agentscope.model

    from runtime import inline_think

    class FakeOpenAIChatModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(agentscope.model, "OpenAIChatModel", FakeOpenAIChatModel)

    async def fake_decrypt(_asset_id: str):
        return "test-key-not-a-secret", "fp_test"

    monkeypatch.setattr(agentscope_runtime, "_decrypt_asset_secret", fake_decrypt)

    async def _build(model_id: str):
        state = TaskState(task_id="t", run_id="r", user_id="u", instance_id="i", input_text="q")
        state.model_spec = {"model_id": model_id,
                            "model_asset_id": "11111111-1111-1111-1111-111111111111",
                            "base_url": "https://model.example.invalid/v1"}
        m = await agentscope_runtime._build_model(state)
        await m.kwargs["client_kwargs"]["http_client"].aclose()
        return m

    monkeypatch.setenv(inline_think.ENV_KEY, "GLM")
    hit = await _build("GLM-V5.1-DX-32K")
    assert type(hit) is not FakeOpenAIChatModel and isinstance(hit, FakeOpenAIChatModel)  # 切流子类
    miss = await _build("qwen-max")
    assert type(miss) is FakeOpenAIChatModel  # 未命中 → 原生类，行为逐字不变
    assert hit.kwargs["stream"] is True and miss.kwargs["stream"] is True  # 构造参数一致


async def test_inline_think_reaches_agent_as_thinking_events_and_clean_conclusion():
    """端到端过一遍真 agentscope Agent：思考出 ThinkingBlockDeltaEvent、正文出 TextBlockDeltaEvent，
    且**最终 Msg 正文干净**——`_final_text` 读的就是它，它会流进 rca.conclusion / 告警 result_summary
    / WeLink 通知摘要。上面那些用例只钉 split_stream 的输出形状，这条钉住「agentscope 真会按我们
    期望的方式把它转成事件」这个假设（agentscope 升版本时这条会先红）。
    """
    from agentscope.agent import Agent
    from agentscope.event import TextBlockDeltaEvent, ThinkingBlockDeltaEvent
    from agentscope.message import Msg, TextBlock
    from agentscope.model import ChatModelBase, ChatResponse
    from agentscope.tool import Toolkit

    from runtime import inline_think

    class _InlineThinkFakeModel(ChatModelBase):  # type: ignore[misc]
        def __init__(self) -> None:
            # 跳过 super().__init__（无真 credential）；手设 agent 循环会读的属性（同 StubRcaModel）
            self.model = "GLM-V5.1-DX-32K"
            self.stream = True
            self.max_retries = 0
            self.retry_delay = 0.0
            self.context_size = 128000
            self.parameters = None
            self.credential = None

        async def _call_api(self, *_a, **_kw):
            async def raw():
                for d in _INTRANET_DELTAS:
                    yield ChatResponse(content=[TextBlock(text=d, id="b1")], is_last=False)
                yield ChatResponse(content=[TextBlock(text="".join(_INTRANET_DELTAS), id="b1")],
                                   is_last=True)

            return inline_think.split_stream(raw())

    agent = Agent(name="t", system_prompt="s", model=_InlineThinkFakeModel(), toolkit=Toolkit())
    seq: list[tuple[str, str]] = []
    async for ev in agent.reply_stream(
            Msg(name="user", role="user", content=[TextBlock(type="text", text="你好啊.")])):
        if isinstance(ev, ThinkingBlockDeltaEvent):
            seq.append(("think", ev.delta))
        elif isinstance(ev, TextBlockDeltaEvent):
            seq.append(("text", ev.delta))

    assert list(dict.fromkeys(k for k, _ in seq)) == ["think", "text"]  # 先思考后正文，不来回横跳
    assert "".join(d for k, d in seq if k == "think") == _WANT_THINK
    assert "".join(d for k, d in seq if k == "text") == _WANT_TEXT
    assert agent.state.context[-1].get_text_content() == _WANT_TEXT  # _final_text 读这里 → 结论


# ── 轮次启发（2026-08-25 二期）────────────────────────────────────────────────
# 一期让每次模型调用都以「思考起手」，结果 ReAct 后续轮（工具结果之后）若模型直接作答、
# 整轮没有 </think>，正文全被当思考流进折叠卡。二期按轮次定相位：末条消息是工具结果 →
# 「正文起手」。下面这批钉 start_phase_for 判定矩阵与 text 起手的各形态。

def test_start_phase_for_round_matrix():
    """末条含 tool_result → 续轮「正文起手」；末条 user / 空 / 形状不认识 → 首轮「思考起手」。

    形状按 agentscope 真实上下文来：工具结果被 _save_to_context **并进同一条 assistant Msg**
    （last_msg.content.extend），不是独立的 tool 角色消息——判定函数认的是「末条消息里有没有
    tool_result 块」，恰好覆盖这个形状。
    """
    from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
    from runtime import inline_think

    user = Msg(name="user", role="user", content=[TextBlock(type="text", text="查一下")])
    # 首轮结束后的 assistant Msg：正文 + 工具调用 + （工具执行完）工具结果，同一条消息里
    asst = Msg(name="t", role="assistant", content=[
        TextBlock(type="text", text="我来查询。"),
        ToolCallBlock(type="tool_call", id="tc-1", name="query_resource", input='{"appid": "APP-A"}'),
        ToolResultBlock(type="tool_result", id="tc-1", name="query_resource",
                        output=[TextBlock(type="text", text="P99=850ms")]),
    ])
    asst_no_result = Msg(name="t", role="assistant",
                         content=[TextBlock(type="text", text="我来查询。")])
    assert inline_think.start_phase_for([user]) == "thinking"
    assert inline_think.start_phase_for([user, asst]) == "text"       # 工具结果之后的续轮
    assert inline_think.start_phase_for([user, asst, user]) == "thinking"  # 新用户轮又回思考起手
    assert inline_think.start_phase_for([user, asst_no_result]) == "thinking"  # 无结果块不算续轮
    assert inline_think.start_phase_for([]) == "thinking"
    assert inline_think.start_phase_for([{"role": "tool"}]) == "thinking"  # 形状不认识 → 保守回落


async def test_followup_round_plain_text_streams_as_text():
    """本次 bug 的回归测试：续轮纯正文（无任何标签）→ 零思考增量、正文逐字直播、终块干净。

    一期这里整轮进思考折叠卡（终块兜底救结论、救不了显示）——「有一些正文被放进 think」。
    """
    from agentscope.message import TextBlock, ThinkingBlock
    from runtime import inline_think

    deltas = ["根因已确认：", "Redis 连接泄漏。", "建议重启 svc-a。"]
    thinks, texts, final = await _drain(
        inline_think.split_stream(_text_stream(deltas), start_phase="text"))
    assert thinks == []                                   # 一个思考增量都不许有
    assert len(texts) > 1 and "".join(texts) == "".join(deltas)  # 正文逐字直播
    assert [type(b) for b in final.content] == [TextBlock]
    assert final.content[0].text == "".join(deltas)
    assert not [b for b in final.content if isinstance(b, ThinkingBlock)]


async def test_followup_round_explicit_think_prefix_goes_to_thinking():
    """续轮以显式 <think> 开头（模板改用显式标签）→ 思考进思考通道、标签两边都不留。"""
    from runtime import inline_think

    thinks, texts, final = await _drain(inline_think.split_stream(
        _text_stream(["<think>先核对指标", "。</think>结论：", "正常"]), start_phase="text"))
    assert "".join(thinks) == "先核对指标。"
    assert "".join(texts) == "结论：正常"
    assert final.content[0].thinking == "先核对指标。" and final.content[1].text == "结论：正常"


async def test_followup_round_interleaved_think_midtext():
    """续轮正文中段的显式 <think>...</think>（interleaved thinking）→ 切进思考通道。"""
    from runtime import inline_think

    thinks, texts, final = await _drain(inline_think.split_stream(
        _text_stream(["先说结论。", "<think>补充推理", "</think>", "详情如下。"]),
        start_phase="text"))
    assert "".join(thinks) == "补充推理"
    assert "".join(texts) == "先说结论。详情如下。"
    assert "<think>" not in "".join(texts) and "</think>" not in "".join(texts)
    # 终块同样干净：思考归思考、正文归正文
    assert final.content[0].thinking == "补充推理"
    assert final.content[1].text == "先说结论。详情如下。"


async def test_followup_round_stray_close_reclassifies_final_block(caplog):
    """安全网（轮次启发赌输）：续轮无开标签却中途冒 </think> ＝ 该轮其实是隐式思考。

    已直播的显示救不回来（认栽），但终块必须重归类——conclusion / 告警摘要 / WeLink 通知
    靠的全是终块，这条守住数据面。warning 日志是「该网关不符合轮次假设、考虑回退整流缓冲
    方案」的信号。
    """
    import logging

    from agentscope.message import TextBlock, ThinkingBlock
    from runtime import inline_think

    with caplog.at_level(logging.WARNING, logger="openops.inline_think"):
        _thinks, texts, final = await _drain(inline_think.split_stream(
            _text_stream(["其实我在想", "问题出在哪。</think>根因：", "连接泄漏。"]),
            start_phase="text", model_id="GLM-V5.1-DX-32K"))
    assert "其实我在想问题出在哪。" in "".join(texts)  # 增量已按正文直播（显示层认栽，明示于此）
    # 终块重归类：闭标签前进思考、之后是干净正文
    assert [type(b) for b in final.content] == [ThinkingBlock, TextBlock]
    assert final.content[0].thinking == "其实我在想问题出在哪。"
    assert final.content[1].text == "根因：连接泄漏。"
    assert any("续轮出现隐式思考" in r.message for r in caplog.records)


async def test_two_round_react_followup_text_not_treated_as_thinking():
    """端到端照抄现场失败形态：两轮 ReAct——首轮隐式思考+工具调用，续轮纯正文。

    过真 agentscope Agent 断言：续轮**没有** ThinkingBlockDeltaEvent、正文以
    TextBlockDeltaEvent 直播、最终 Msg 正文干净（_final_text 读它 → 结论/告警摘要）。
    相位由 start_phase_for 按 messages 现算——这条同时钉住判定逻辑接对了线。
    """
    from agentscope.agent import Agent
    from agentscope.event import TextBlockDeltaEvent, ThinkingBlockDeltaEvent, ToolCallStartEvent
    from agentscope.message import Msg, TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse
    from agentscope.tool import FunctionTool, Toolkit

    from runtime import inline_think

    ROUND1 = ["查一下指标", "。</think>好的，我来查询。"]      # 隐式思考 + 闭标签 + 正文
    ROUND2 = ["根因：", "Redis 连接泄漏，", "建议重启 svc-a。"]  # 续轮直接作答，无任何标签

    class _TwoRoundModel(ChatModelBase):  # type: ignore[misc]
        def __init__(self) -> None:
            self.model = "GLM-V5.1-DX-32K"
            self.stream = True
            self.max_retries = 0
            self.retry_delay = 0.0
            self.context_size = 128000
            self.parameters = None
            self.credential = None
            self.calls = 0

        async def _call_api(self, _model_name, messages, **_kw):
            self.calls += 1
            phase = inline_think.start_phase_for(messages)  # 与 patched 子类同一条判定线
            first = self.calls == 1
            assert phase == ("thinking" if first else "text"), f"第 {self.calls} 轮相位判错：{phase}"

            async def raw():
                deltas = ROUND1 if first else ROUND2
                for d in deltas:
                    yield ChatResponse(content=[TextBlock(text=d, id="b")], is_last=False)
                blocks = [TextBlock(text="".join(deltas), id="b")]
                if first:  # 首轮末尾带工具调用，驱动 Agent 进第二轮
                    tc = ToolCallBlock(id="tc-1", name="query_resource", input='{"appid": "APP-A"}')
                    yield ChatResponse(content=[tc], is_last=False)
                    blocks.append(tc)
                yield ChatResponse(content=blocks, is_last=True)

            return inline_think.split_stream(raw(), phase, self.model)

    async def query_resource(appid: str) -> str:
        """查询指定应用的可观测数据。

        Args:
            appid: 目标应用 ID。
        """
        return f"{appid}: P99=850ms redis_conn=95%"

    # agentscope 默认无规则即 ask（RequireUserConfirm），按仓库 _permission_context 的口径
    # 给该工具挂 tool 级 allow 规则放行——本测试测的是切流相位，不测审批桥
    from agentscope.permission import PermissionBehavior, PermissionContext, PermissionRule
    from agentscope.state import AgentState

    perm = PermissionContext(allow_rules={"query_resource": [PermissionRule(
        tool_name="query_resource", rule_content=None,
        behavior=PermissionBehavior.ALLOW, source="platform")]}, ask_rules={})
    agent = Agent(name="t", system_prompt="s", model=_TwoRoundModel(),
                  toolkit=Toolkit(tools=[FunctionTool(query_resource, name="query_resource",
                                                      is_read_only=True)]),
                  state=AgentState(session_id="s-inline-think", permission_context=perm))
    events: list[tuple[str, str]] = []
    async for ev in agent.reply_stream(
            Msg(name="user", role="user", content=[TextBlock(type="text", text="查 APP-A")])):
        if isinstance(ev, ThinkingBlockDeltaEvent):
            events.append(("think", ev.delta))
        elif isinstance(ev, TextBlockDeltaEvent):
            events.append(("text", ev.delta))
        elif isinstance(ev, ToolCallStartEvent):
            events.append(("tool", ""))

    itool = next(i for i, (k, _) in enumerate(events) if k == "tool")
    round2 = events[itool + 1:]
    assert round2 and all(k == "text" for k, _ in round2), f"续轮混进了非正文事件：{round2}"
    assert "".join(d for _, d in round2) == "".join(ROUND2)   # 续轮正文逐字直播
    assert "".join(d for k, d in events[:itool] if k == "think") == "查一下指标。"  # 首轮思考照旧
    # 最终 Msg：同一条 assistant Msg 跨轮 extend，get_text_content 拼两轮正文——
    # 首轮正文 + 续轮结论都在、思考一个字不在（_final_text 读它 → 结论/告警摘要）
    final_text = agent.state.context[-1].get_text_content()
    assert final_text.endswith("".join(ROUND2)) and "好的，我来查询。" in final_text
    assert "查一下指标" not in final_text
