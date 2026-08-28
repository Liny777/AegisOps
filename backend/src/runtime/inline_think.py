"""内联 `<think>` 切流：把混在 `delta.content` 里的思考摘出来走 ThinkingBlock 通道。

**为什么需要**（2026-08-21 内网换模型后新增）：agentscope 的 OpenAI 解析器只认
`delta.reasoning_content` / `delta.reasoning`（`model/_openai_chat/_model.py`），
而内网这台网关（实测 `GLM-V5.1-DX-32K`）把思考**内联在 `delta.content` 里**：

- 首轮流的第一个 chunk 就是思考正文，**没有 `<think>` 开标签**——chat template 预填了开标签
  并从输出里剥掉，只在思考结束处吐一个闭标签。
- 闭标签混在普通 content chunk 里，形如 `{"delta":{"content":"语气。</think>你好"}}`。

不切的后果不止是界面难看：`_final_text` 取最后一条 assistant 文本当结论，这段文本会流进
transcript / `rca.conclusion` / 告警单 `result_summary` / WeLink 通知摘要；思考还会以纯文本
留在 AgentState 上下文里每轮回灌。切在模型层（`_call_api` 出口）一次性治住这些——顺带，
agentscope 的 OpenAI formatter 对 ThinkingBlock 是**显式跳过**的（不随历史回传），省窗口。

**轮次启发**（2026-08-25 二期）：一期让每次模型调用都以「思考起手」，结果 ReAct 的**后续轮**
（工具结果之后）若模板不再预填思考、模型直接作答，整轮没有 `</think>`，正文全被当思考流进
折叠卡（终块兜底救得了结论、救不了已流出去的显示）。「隐式思考」与「直接正文」在看到闭标签
或流结束前文本上不可区分，故按轮次赌：**末条消息是工具结果 → 正文起手**（见
[[start_phase_for]]），除非流以显式 `<think>` 开头；赌输（后续轮冒出落单 `</think>`）时终块
重归类保住结论并打 warning（见 `_Splitter._reclassify`）——那行日志就是「该回退整流缓冲判定
方案」的信号。

**只对白名单里的 model_id 生效**（`OPENOPS_MODEL_INLINE_THINK`）：本平台是多模型形态
（平台资产可多条、模型模板主/子可绑不同模型、用户还能自带 BYO 模型），全局开关会误伤——
对一台不内联思考的模型，它的回复里永远等不到 `</think>`，整段答案会被当思考缓冲住。
（真配错了也不丢数据，见 `_Splitter.finish` 的兜底。）
"""
from __future__ import annotations

import inspect
import logging
import os
from typing import Any, AsyncGenerator

log = logging.getLogger("openops.inline_think")

ENV_KEY = "OPENOPS_MODEL_INLINE_THINK"

# 闭标签是切换点；开标签用于「显式思考」判定（首轮网关不带；后续轮/别的网关可能带）。
_CLOSE_MARKS = ("</think>", "</thinking>")
_OPEN_MARKS = ("<think>", "<thinking>")
_ALL_MARKS = _CLOSE_MARKS + _OPEN_MARKS
_MAX_MARK = max(len(m) for m in _ALL_MARKS)


def enabled_for(model_id: str | None) -> bool:
    """该 model_id 是否要走内联 think 切流。

    `OPENOPS_MODEL_INLINE_THINK`：逗号分隔，`*`=全开，空/未设=全关；条目大小写不敏感，
    **精确或前缀**匹配——填 `GLM` 即可覆盖 `GLM-V5.1-DX-32K`（网关升版本号不用改配置）。
    """
    raw = (os.environ.get(ENV_KEY) or "").strip()
    if not raw or not model_id:
        return False
    mid = str(model_id).strip().lower()
    for entry in raw.split(","):
        e = entry.strip().lower()
        if e and (e == "*" or mid == e or mid.startswith(e)):
            return True
    return False


def start_phase_for(messages: Any) -> str:
    """按轮次定切流起手相位：末条消息含工具结果 → 后续轮，「正文起手」；否则首轮「思考起手」。

    依据（内网实测）：网关模板只在新用户轮预填 `<think>`，工具结果之后的续轮模型直接作答；
    续轮若真要思考会带显式 `<think>` 开标签（`_Splitter` 的 text 相位认它）。审批恢复流
    （UserConfirmResult → 工具执行 → 结果入上下文）末条同样是 tool_result，天然归为续轮。
    取块失败一律回落「思考起手」——与一期行为相同，保守。
    """
    try:
        if messages and messages[-1].get_content_blocks("tool_result"):
            return "text"
    except Exception:  # noqa: BLE001 —— messages 形状不认识就按一期口径来
        pass
    return "thinking"


def _find_mark(buf: str, marks: tuple[str, ...]) -> tuple[int, int]:
    """buf 里最早出现的标签 → (起点, 标签长度)；没有则 (-1, 0)。"""
    best, blen = -1, 0
    for m in marks:
        i = buf.find(m)
        if i >= 0 and (best < 0 or i < best):
            best, blen = i, len(m)
    return best, blen


def _tail_hold(buf: str) -> int:
    """尾部需要留住不发的字符数：最长的「是某个标签真前缀」的尾缀长度。

    标签会被 chunk 切断（`"</thi"` + `"nk>"`），不留住就会把半截标签当正文发出去、
    且后半截再也拼不上——这是本模块最容易被改坏的一处。
    """
    for k in range(min(_MAX_MARK - 1, len(buf)), 0, -1):
        tail = buf[-k:]
        if any(m.startswith(tail) and len(tail) < len(m) for m in _ALL_MARKS):
            return k
    return 0


class _Splitter:
    """单次模型调用（ReAct 的一轮）的切流状态；相位由 start_phase_for 按轮次决定。

    feed 返回按**原文顺序**排列的 (kind, str) 事件列表（kind ∈ think/text）——text 相位里
    「正文 → 显式 <think> → 思考」在同一 chunk 内就是 text 事件在前、think 事件在后，
    调用方按序各自成块下发，思考与正文绝不同块并存。
    """

    def __init__(self, start_phase: str = "thinking", model_id: str = "") -> None:
        self.phase = start_phase
        self.start_phase = start_phase
        self.model_id = model_id      # 只用于赌输 warning 的定位信息
        self.hold = ""                # 可能被切断的标签尾缀，暂不下发
        self.opened = False           # thinking 相位：是否已过了「剥开标签」的窗口
        self.explicit_open = False    # 本轮是否消费过显式 <think> 开标签
        self.saw_think = False        # 本轮是否已产生过思考段（安全网重归类的前提是还没有）
        self.acc_think: list[str] = []
        self.acc_text: list[str] = []

    def _strip_open(self, s: str) -> str:
        """剥掉思考相位首段的 `<think>`（首轮网关不带；带了也不让标签漏进思考正文）。"""
        if self.opened:
            return s
        stripped = s.lstrip()
        for m in _OPEN_MARKS:
            if stripped.startswith(m):
                self.opened = True
                self.explicit_open = True
                return stripped[len(m):]
        if stripped:  # 首段已有实质内容 → 定型，思考正文里再出现 <think> 字样不再剥
            self.opened = True
        return s

    def _reclassify(self) -> None:
        """安全网（轮次启发赌输）：text 相位冒出**落单** `</think>` ＝ 续轮其实发生了隐式思考。

        已按正文直播出去的增量收不回来（显示层认栽），但把累计正文重归类为思考，
        终块重建即干净——conclusion / 告警摘要 / WeLink 通知靠的全是终块。
        """
        leaked = "".join(self.acc_text)
        self.acc_think.append(leaked)
        self.acc_text = []
        log.warning(
            "[inline_think] 续轮出现隐式思考（</think> 无配对开标签，model=%s，泄漏 %d 字）："
            "该轮思考已按正文直播、显示无法撤回，终块已重归类保住结论。轮次启发对该网关不成立，"
            "复现请抓续轮原始流并考虑回退整流缓冲判定方案。", self.model_id or "-", len(leaked))

    def feed(self, delta: str) -> list[tuple[str, str]]:
        """吃一段正文增量，返回按原文顺序的 (kind, str) 事件；kind ∈ {"think","text"}。"""
        if not delta:
            return []
        buf = self.hold + delta
        self.hold = ""
        out: list[tuple[str, str]] = []

        def emit(kind: str, s: str) -> None:
            if not s:
                return
            (self.acc_think if kind == "think" else self.acc_text).append(s)
            if out and out[-1][0] == kind:  # 邻接同类合并，少发几个事件
                out[-1] = (kind, out[-1][1] + s)
            else:
                out.append((kind, s))

        while buf:
            if self.phase == "thinking":
                idx, mlen = _find_mark(buf, _CLOSE_MARKS)
                if idx >= 0:  # 思考结束：闭标签本身两边都不要；其后常跟排版换行，剥掉
                    emit("think", self._strip_open(buf[:idx]))
                    self.saw_think = True
                    self.phase = "text"
                    buf = buf[idx + mlen:].lstrip("\n")
                    continue
                keep = _tail_hold(buf)
                if keep:
                    self.hold = buf[len(buf) - keep:]
                    buf = buf[: len(buf) - keep]
                emit("think", self._strip_open(buf))
                buf = ""
            else:  # text 相位：直播正文，同时盯显式开标签（interleaved）与落单闭标签（安全网）
                oidx, omlen = _find_mark(buf, _OPEN_MARKS)
                cidx, cmlen = _find_mark(buf, _CLOSE_MARKS)
                if cidx >= 0 and (oidx < 0 or cidx < oidx):
                    pre = buf[:cidx]
                    if pre.strip():
                        emit("text", pre)
                    if not self.saw_think:  # 已有过配对思考段后的落单闭标签只剥不归类
                        self._reclassify()
                    self.saw_think = True
                    buf = buf[cidx + cmlen:].lstrip("\n")
                    continue
                if oidx >= 0:  # 显式 <think>：切进思考相位直到闭标签（标签前的正文先发）
                    pre = buf[:oidx]
                    if pre.strip():
                        emit("text", pre)
                    self.phase = "thinking"
                    self.opened = True       # 开标签已在此消费，思考相位不再剥
                    self.explicit_open = True
                    self.saw_think = True
                    buf = buf[oidx + omlen:]
                    continue
                keep = _tail_hold(buf)
                if keep:
                    self.hold = buf[len(buf) - keep:]
                    buf = buf[: len(buf) - keep]
                emit("text", buf)
                buf = ""
        return out

    def finish(self) -> tuple[str, str]:
        """终块用的累计 (思考, 正文)。

        兜底：**隐式**思考起手（首轮）却整轮没等到闭标签（白名单误配到不内联思考的模型）→
        全部当正文，答案绝不丢；此时增量已按思考直播过，前端表现是「答案先出现在折叠卡里、
        最后再以结论气泡补出」——难看，但只在配错时发生。显式 `<think>` 未闭合（截断在思考里）
        不走兜底：内容本来就在思考里，保持思考归属。
        """
        if self.phase == "thinking" and not self.saw_think and not self.explicit_open:
            return "", "".join(self.acc_think) + self.hold
        think = "".join(self.acc_think) + (self.hold if self.phase == "thinking" else "")
        text = "".join(self.acc_text) + (self.hold if self.phase != "thinking" else "")
        return think, text


def _blocks(chunk: Any) -> tuple[list[Any], list[Any], list[Any]]:
    """把 chunk.content 拆成 (文本块, 思考块, 其它块)——其它块一律原样透传。"""
    from agentscope.message import TextBlock, ThinkingBlock

    texts, thinks, others = [], [], []
    for b in chunk.content:
        if isinstance(b, TextBlock):
            texts.append(b)
        elif isinstance(b, ThinkingBlock):
            thinks.append(b)
        else:
            others.append(b)
    return texts, thinks, others


def _resp(chunk: Any, content: list[Any], *, is_last: bool, usage: Any = None) -> Any:
    """按原 chunk 的身份重建一个 ChatResponse（id/metadata 保留，便于对账）。"""
    from agentscope.model import ChatResponse

    return ChatResponse(content=content, is_last=is_last, id=chunk.id,
                        usage=usage, metadata=dict(getattr(chunk, "metadata", {}) or {}))


def _final_content(chunk: Any, sp: _Splitter) -> list[Any]:
    """终块内容：ThinkingBlock(累计思考) + TextBlock(干净正文) + 原样的工具调用等。

    终块带的是**累计**块（agentscope 契约），所以这里直接用 splitter 的累计结果替换，
    不再逐块改写。原生 ThinkingBlock（网关哪天改吐 reasoning_content 时才有）拼在前面。
    """
    from agentscope.message import TextBlock, ThinkingBlock

    texts, thinks, others = _blocks(chunk)
    think, text = sp.finish()
    native = "".join(b.thinking for b in thinks)
    out: list[Any] = []
    if native or think:
        kw: dict[str, Any] = {"thinking": native + think}
        if thinks:  # 有原生思考块就沿用它的 id，别凭空换
            kw["id"] = thinks[0].id
        out.append(ThinkingBlock(**kw))
    if text:
        out.append(TextBlock(text=text, id=texts[0].id) if texts else TextBlock(text=text))
    out.extend(others)
    return out


def _delta_responses(chunk: Any, sp: _Splitter) -> list[Any]:
    """增量块改写：思考与正文按 feed 给出的原文顺序**各自成块先后 yield**，绝不同块并存。

    并存会让 agentscope 在同一 chunk 里同时发 TextBlockDelta 与 ThinkingBlockDelta
    （`agent/_agent.py` 是先文本后思考），AG-UI 侧文本/思考互斥就会来回开合、块碎裂
    （`agui_service._wire` 那段注释描述的正是这个现象）。
    """
    from agentscope.message import TextBlock, ThinkingBlock

    texts, thinks, others = _blocks(chunk)
    if not texts:  # 无正文块（纯工具调用/纯原生思考）→ 原样透传，不动
        return [chunk]

    events: list[tuple[str, str]] = []
    for b in texts:
        events.extend(sp.feed(b.text))
    if thinks:  # 原生思考块（reasoning_content 通道）排最前，与切出来的思考各自成段
        events.insert(0, ("think", "".join(b.thinking for b in thinks)))

    out: list[Any] = []
    for kind, s in events:
        blk = ThinkingBlock(thinking=s) if kind == "think" else TextBlock(text=s, id=texts[0].id)
        out.append(_resp(chunk, [blk], is_last=False))
    if others:  # 工具调用等原样透传（本 chunk 事件之后）
        out.append(_resp(chunk, list(others), is_last=False))
    if out:
        out[-1].usage = chunk.usage  # usage 挂在本 chunk 最后一发上，别丢
    return out


async def split_stream(stream: AsyncGenerator, start_phase: str = "thinking",
                       model_id: str = "") -> AsyncGenerator:
    """把内联 think 的流式响应按相位切成 思考块 / 正文块。"""
    sp = _Splitter(start_phase, model_id)
    async for chunk in stream:
        if chunk.is_last:
            yield _resp(chunk, _final_content(chunk, sp), is_last=True, usage=chunk.usage)
            continue
        for out in _delta_responses(chunk, sp):
            yield out


def split_response(res: Any, start_phase: str = "thinking", model_id: str = "") -> Any:
    """非流式响应的同构改写（本平台恒 stream=True，这条是防御性分支）。"""
    sp = _Splitter(start_phase, model_id)
    texts, _thinks, _others = _blocks(res)
    for b in texts:
        sp.feed(b.text)
    return _resp(res, _final_content(res, sp), is_last=res.is_last, usage=res.usage)


_CLS_CACHE: dict[str, Any] = {}


def patched_model_cls() -> Any:
    """惰性建 `OpenAIChatModel` 子类（agentscope 是可选 extra，模块级不能 import）。

    只覆写 `_call_api`：base 的重试壳 `__call__` 与 `_call_api_with_structured_output`
    都经 `self._call_api`，覆写这一处两条路径全覆盖。相位按本次调用的 messages 现算——
    ReAct 每轮都是一次独立的 _call_api。
    """
    from agentscope.model import OpenAIChatModel

    cached = _CLS_CACHE.get("cls")
    # 基类变了（单测 monkeypatch agentscope.model.OpenAIChatModel）就重建，别拿旧的
    if cached is not None and cached.__bases__[0] is OpenAIChatModel:
        return cached

    class InlineThinkOpenAIChatModel(OpenAIChatModel):  # type: ignore[misc, valid-type]
        """内联 `</think>` 切流版；构造参数与基类完全一致。"""

        async def _call_api(self, model_name: str, messages: list, tools: list | None = None,
                            tool_choice: Any = None, **generate_kwargs: Any) -> Any:
            phase = start_phase_for(messages)
            res = await super()._call_api(model_name, messages, tools=tools,
                                          tool_choice=tool_choice, **generate_kwargs)
            if inspect.isasyncgen(res):
                return split_stream(res, phase, model_name)
            return split_response(res, phase, model_name)

    _CLS_CACHE["cls"] = InlineThinkOpenAIChatModel
    return InlineThinkOpenAIChatModel
