"""内联 `<think>` 切流：把混在 `delta.content` 里的思考摘出来走 ThinkingBlock 通道。

**为什么需要**（2026-08-21 内网换模型后新增）：agentscope 的 OpenAI 解析器只认
`delta.reasoning_content` / `delta.reasoning`（`model/_openai_chat/_model.py`），
而内网这台网关（实测 `GLM-V5.1-DX-32K`）把思考**内联在 `delta.content` 里**：

- 流的第一个 chunk 就是思考正文，**没有 `<think>` 开标签**——chat template 预填了开标签并从
  输出里剥掉，只在思考结束处吐一个闭标签。故本模块的状态机**以「思考中」起手**。
- 闭标签混在普通 content chunk 里，形如 `{"delta":{"content":"语气。</think>你好"}}`。

不切的后果不止是界面难看：`_final_text` 取最后一条 assistant 文本当结论，这段文本会流进
transcript / `rca.conclusion` / 告警单 `result_summary` / WeLink 通知摘要；思考还会以纯文本
留在 AgentState 上下文里每轮回灌。切在模型层（`_call_api` 出口）一次性治住这些——顺带，
agentscope 的 OpenAI formatter 对 ThinkingBlock 是**显式跳过**的（不随历史回传），省窗口。

**只对白名单里的 model_id 生效**（`OPENOPS_MODEL_INLINE_THINK`）：本平台是多模型形态
（平台资产可多条、模型模板主/子可绑不同模型、用户还能自带 BYO 模型），全局开关会误伤——
对一台不内联思考的模型，它的回复里永远等不到 `</think>`，整段答案会被当思考缓冲住。
（真配错了也不丢数据，见 `_Splitter.finish` 的兜底。）
"""
from __future__ import annotations

import inspect
import os
from typing import Any, AsyncGenerator

ENV_KEY = "OPENOPS_MODEL_INLINE_THINK"

# 闭标签是切换点；开标签只在别的网关带它时用于剥除（本网关不带）。
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


def _find_close(buf: str) -> tuple[int, int]:
    """最早出现的闭标签 → (起点, 标签长度)；没有则 (-1, 0)。"""
    best, blen = -1, 0
    for m in _CLOSE_MARKS:
        i = buf.find(m)
        if i >= 0 and (best < 0 or i < best):
            best, blen = i, len(m)
    return best, blen


def _tail_hold(buf: str) -> int:
    """尾部需要留住不发的字符数：最长的「是某个标签真前缀」的尾缀长度。

    闭标签会被 chunk 切断（`"</thi"` + `"nk>"`），不留住就会把半截标签当正文发出去、
    且后半截再也拼不上——这是本模块最容易被改坏的一处。
    """
    for k in range(min(_MAX_MARK - 1, len(buf)), 0, -1):
        tail = buf[-k:]
        if any(m.startswith(tail) and len(tail) < len(m) for m in _ALL_MARKS):
            return k
    return 0


class _Splitter:
    """单次模型调用（ReAct 的一轮）的切流状态；每轮都重新以「思考中」起手。"""

    def __init__(self) -> None:
        self.phase = "thinking"
        self.hold = ""          # 可能被切断的标签尾缀，暂不下发
        self.opened = False     # 是否已处理过开标签（只在首个非空白输出时判一次）
        self.acc_think: list[str] = []
        self.acc_text: list[str] = []

    def _strip_open(self, s: str) -> str:
        """剥掉首段的 `<think>`（本网关不带；别的网关带时不让标签漏进思考正文）。"""
        if self.opened:
            return s
        stripped = s.lstrip()
        for m in _OPEN_MARKS:
            if stripped.startswith(m):
                self.opened = True
                return stripped[len(m):]
        if stripped:  # 首段已有实质内容 → 定型，后文中间再出现 <think> 不再当开标签剥
            self.opened = True
        return s

    def feed(self, delta: str) -> tuple[str, str]:
        """吃一段正文增量，返回 (思考增量, 正文增量)。两者都可能为空串。"""
        if not delta:
            return "", ""
        buf = self.hold + delta
        self.hold = ""
        if self.phase == "text":
            self.acc_text.append(buf)
            return "", buf

        idx, mlen = _find_close(buf)
        if idx >= 0:  # 本 chunk 里思考结束：标签本身两边都不要
            think = self._strip_open(buf[:idx])
            # 闭标签后常跟换行（模型排版），去掉免得正文以空行起手
            text = buf[idx + mlen:].lstrip("\n")
            self.phase = "text"
            if think:
                self.acc_think.append(think)
            if text:
                self.acc_text.append(text)
            return think, text

        keep = _tail_hold(buf)
        if keep:
            self.hold = buf[len(buf) - keep:]
            buf = buf[: len(buf) - keep]
        think = self._strip_open(buf)
        if think:
            self.acc_think.append(think)
        return think, ""

    def finish(self) -> tuple[str, str]:
        """终块用的累计 (思考, 正文)。

        兜底：整轮都没等到闭标签（白名单误配到一台不内联思考的模型）→ **全部当正文**，
        保证答案绝不丢。此时增量已按思考发出去了，前端表现是「答案先出现在折叠卡里、
        最后再以结论气泡补出」（`agui_service` 的 streamed 未置位 → task.completed 合成气泡）：
        难看，但不丢数据，且只在配错时才发生。
        """
        if self.phase != "text":
            return "", "".join(self.acc_think) + self.hold
        return "".join(self.acc_think), "".join(self.acc_text) + self.hold


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
    """增量块改写：思考与正文**分成两个 ChatResponse 先后 yield**，绝不同块并存。

    并存会让 agentscope 在同一 chunk 里同时发 TextBlockDelta 与 ThinkingBlockDelta
    （`agent/_agent.py` 是先文本后思考），AG-UI 侧文本/思考互斥就会来回开合、块碎裂
    （`agui_service._wire` 那段注释描述的正是这个现象）。分开发即天然有序。
    """
    from agentscope.message import TextBlock, ThinkingBlock

    texts, thinks, others = _blocks(chunk)
    if not texts:  # 无正文块（纯工具调用/纯原生思考）→ 原样透传，不动
        return [chunk]

    think, text = "", ""
    for b in texts:
        t1, t2 = sp.feed(b.text)
        think += t1
        text += t2

    out: list[Any] = []
    if thinks or think:  # 原生思考块与切出来的思考并作一发
        native = "".join(b.thinking for b in thinks)
        blk = ThinkingBlock(thinking=native + think)
        out.append(_resp(chunk, [blk], is_last=False))
    if text or others:
        content: list[Any] = []
        if text:
            content.append(TextBlock(text=text, id=texts[0].id))
        content.extend(others)
        out.append(_resp(chunk, content, is_last=False, usage=chunk.usage))
    elif out:  # 思考单发时把 usage 挂在它身上，别丢
        out[-1].usage = chunk.usage
    return out


async def split_stream(stream: AsyncGenerator) -> AsyncGenerator:
    """把内联 think 的流式响应改写成 思考块 → 正文块 两段。"""
    sp = _Splitter()
    async for chunk in stream:
        if chunk.is_last:
            yield _resp(chunk, _final_content(chunk, sp), is_last=True, usage=chunk.usage)
            continue
        for out in _delta_responses(chunk, sp):
            yield out


def split_response(res: Any) -> Any:
    """非流式响应的同构改写（本平台恒 stream=True，这条是防御性分支）。"""
    sp = _Splitter()
    texts, _thinks, _others = _blocks(res)
    for b in texts:
        sp.feed(b.text)
    return _resp(res, _final_content(res, sp), is_last=res.is_last, usage=res.usage)


_CLS_CACHE: dict[str, Any] = {}


def patched_model_cls() -> Any:
    """惰性建 `OpenAIChatModel` 子类（agentscope 是可选 extra，模块级不能 import）。

    只覆写 `_call_api`：base 的重试壳 `__call__` 与 `_call_api_with_structured_output`
    都经 `self._call_api`，覆写这一处两条路径全覆盖。
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
            res = await super()._call_api(model_name, messages, tools=tools,
                                          tool_choice=tool_choice, **generate_kwargs)
            if inspect.isasyncgen(res):
                return split_stream(res)
            return split_response(res)

    _CLS_CACHE["cls"] = InlineThinkOpenAIChatModel
    return InlineThinkOpenAIChatModel
