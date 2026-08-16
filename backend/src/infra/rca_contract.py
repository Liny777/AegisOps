"""诊断面板（update_diagnosis_board）的数据契约。

模型只能提交短文本、枚举 tone 与 0..1 置信度；steps/phaseLabel/status 完全由服务端派生
（rca_board 状态机），revision 由服务端自增——契约对模型提交的这些键一律拒收。与
chart_contract 同口径 deny-by-default：未知键（顶层与每个列表项）、HTML/控制字符、
超长/超量全部拒绝，中文错误信息回给模型自纠。
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any


class RcaBoardContractError(ValueError):
    """诊断面板参数不符合公开展示契约。"""


# 五步法固定标签（服务端唯一事实源；前端 stepper 直接渲染）
STEP_LABELS = ("范围", "证据", "假设", "验证", "结论")

_TONES = {"good", "warning", "danger", "neutral"}
# 顶层键 = 工具签名（除 step/step_completed 外全增量可选）；steps/revision/status 等派生键拒收
_TOP_LEVEL_KEYS = {
    "step", "step_completed", "step_summary", "title", "tiles", "current_question", "why",
    "facts", "unknowns", "sources", "hypotheses", "actions", "conclusion",
}
_TILE_KEYS = {"label", "value"}
_TEXT_ITEM_KEYS = {"text"}
_SOURCE_KEYS = {"name", "status", "tone"}
_HYPOTHESIS_KEYS = {"text", "tag", "tagTone", "conf"}
_ACTION_KEYS = {"tier", "text", "confirm", "impact", "status", "statusTone"}
_MAX_TILES = 6
_MAX_FACTS = 20
_MAX_SOURCES = 10
_MAX_HYPOTHESES = 8
_MAX_ACTIONS = 8


def _short_text(value: Any, field: str, *, limit: int, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise RcaBoardContractError(f"{field} 必须是字符串")
    if "<" in value or ">" in value:
        raise RcaBoardContractError(f"{field} 不支持 HTML 或尖括号")
    if any(unicodedata.category(char).startswith("C") and char not in "\t\r\n" for char in value):
        raise RcaBoardContractError(f"{field} 包含不支持的控制字符")
    text = re.sub(r"\s+", " ", value).strip()
    if required and not text:
        raise RcaBoardContractError(f"{field} 不能为空")
    if len(text) > limit:
        raise RcaBoardContractError(f"{field} 最长 {limit} 个字符")
    return text


def _only_keys(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        raise RcaBoardContractError(f"{field} 包含不支持的字段：{', '.join(unknown)}")


def _tone(value: Any, field: str) -> str:
    if value in (None, ""):
        return "neutral"
    if not isinstance(value, str) or value not in _TONES:
        raise RcaBoardContractError(f"{field} 只支持 good、warning、danger 或 neutral")
    return value


def _bool(value: Any, field: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise RcaBoardContractError(f"{field} 必须是布尔值（true/false）")
    return value


def _items(value: Any, field: str, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RcaBoardContractError(f"{field} 必须是对象数组")
    if len(value) > limit:
        raise RcaBoardContractError(f"{field} 最多 {limit} 项")
    out: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise RcaBoardContractError(f"{field}[{index}] 必须是对象")
        out.append(raw)
    return out


def normalize_board_arguments(value: Any) -> dict[str, Any]:
    """校验并返回可进入面板合并的规范参数。

    返回结构：``{"step": int, "step_completed": bool, …仅含本次提交了内容的展示字段}``；
    展示字段键名对齐前端 RcaCardData（current_question → currentQ）。空字符串 / 空数组 /
    None 视为「本次未提交」，由合并器保留上次的值（增量语义）。
    """
    if not isinstance(value, dict):
        raise RcaBoardContractError("诊断面板参数必须是对象")
    _only_keys(value, _TOP_LEVEL_KEYS, "诊断面板参数")

    raw_step = value.get("step")
    if isinstance(raw_step, float) and raw_step.is_integer():
        raw_step = int(raw_step)
    if isinstance(raw_step, bool) or not isinstance(raw_step, int):
        raise RcaBoardContractError("step 必须是 1..5 的整数（1=范围 2=证据 3=假设 4=验证 5=结论）")
    if raw_step < 1 or raw_step > 5:
        raise RcaBoardContractError("step 必须在 1..5 之间")

    out: dict[str, Any] = {
        "step": raw_step,
        "step_completed": _bool(value.get("step_completed"), "step_completed"),
    }

    scalars = (
        ("title", "title", 80),
        # 当前步骤一句话小结：归属本次提交的 step（str(step) 键），由 rca_board 逐键累积
        ("step_summary", "step_summary", 120),
        ("current_question", "currentQ", 200),
        ("why", "why", 300),
        ("conclusion", "conclusion", 1200),
    )
    for src, dst, limit in scalars:
        text = _short_text(value.get(src), src, limit=limit, required=False)
        if text:
            out[dst] = text

    raw_tiles = value.get("tiles")
    if raw_tiles:
        tiles = []
        for index, item in enumerate(_items(raw_tiles, "tiles", limit=_MAX_TILES)):
            field = f"tiles[{index}]"
            _only_keys(item, _TILE_KEYS, field)
            tiles.append({
                "label": _short_text(item.get("label"), f"{field}.label", limit=40),
                "value": _short_text(item.get("value"), f"{field}.value", limit=160),
            })
        out["tiles"] = tiles

    for key in ("facts", "unknowns"):
        raw_list = value.get(key)
        if not raw_list:
            continue
        rows = []
        for index, item in enumerate(_items(raw_list, key, limit=_MAX_FACTS)):
            field = f"{key}[{index}]"
            _only_keys(item, _TEXT_ITEM_KEYS, field)
            rows.append({"text": _short_text(item.get("text"), f"{field}.text", limit=300)})
        out[key] = rows

    raw_sources = value.get("sources")
    if raw_sources:
        sources = []
        for index, item in enumerate(_items(raw_sources, "sources", limit=_MAX_SOURCES)):
            field = f"sources[{index}]"
            _only_keys(item, _SOURCE_KEYS, field)
            sources.append({
                "name": _short_text(item.get("name"), f"{field}.name", limit=60),
                "status": _short_text(item.get("status"), f"{field}.status", limit=40, required=False) or "running",
                "tone": _tone(item.get("tone"), f"{field}.tone"),
            })
        out["sources"] = sources

    raw_hypotheses = value.get("hypotheses")
    if raw_hypotheses:
        hypotheses = []
        for index, item in enumerate(_items(raw_hypotheses, "hypotheses", limit=_MAX_HYPOTHESES)):
            field = f"hypotheses[{index}]"
            _only_keys(item, _HYPOTHESIS_KEYS, field)
            conf = item.get("conf")
            if conf is None:
                conf = 0.0
            if isinstance(conf, bool) or not isinstance(conf, (int, float)):
                raise RcaBoardContractError(f"{field}.conf 必须是 0..1 的数字")
            if not math.isfinite(conf) or conf < 0 or conf > 1:
                raise RcaBoardContractError(f"{field}.conf 必须在 0..1 之间")
            hypotheses.append({
                "text": _short_text(item.get("text"), f"{field}.text", limit=200),
                "tag": _short_text(item.get("tag"), f"{field}.tag", limit=40, required=False),
                "tagTone": _tone(item.get("tagTone"), f"{field}.tagTone"),
                "conf": float(conf),
            })
        out["hypotheses"] = hypotheses

    raw_actions = value.get("actions")
    if raw_actions:
        actions = []
        for index, item in enumerate(_items(raw_actions, "actions", limit=_MAX_ACTIONS)):
            field = f"actions[{index}]"
            _only_keys(item, _ACTION_KEYS, field)
            actions.append({
                "tier": _short_text(item.get("tier"), f"{field}.tier", limit=20, required=False) or "建议",
                "text": _short_text(item.get("text"), f"{field}.text", limit=200),
                "confirm": _bool(item.get("confirm"), f"{field}.confirm"),
                "impact": _short_text(item.get("impact"), f"{field}.impact", limit=120, required=False),
                "status": _short_text(item.get("status"), f"{field}.status", limit=40, required=False) or "建议",
                "statusTone": _tone(item.get("statusTone"), f"{field}.statusTone"),
            })
        out["actions"] = actions

    return out


def normalize_checkpoint_hypothesis(value: Any) -> str:
    """用户经假设 checkpoint 卡片补充的假设文本：与面板字段同口径清洗（拒尖括号/控制字符，≤300 字）。

    该文本会拼进给模型的工具返回值——必须先过这里，不得把裸输入透传。
    """
    return _short_text(value, "hypothesis", limit=300)


def derive_steps(step: int, completed: bool, summaries: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """服务端唯一 steps 生成器：i<step→done；i==step→completed?done:active；i>step→waiting。

    summaries 为「步号（str）→ 一句话小结」映射（rca_board 逐键累积）；有值才输出 summary 键——
    缺省不输出，旧快照/既有断言零影响。
    """
    out: list[dict[str, Any]] = []
    for num, label in enumerate(STEP_LABELS, start=1):
        if num < step:
            state = "done"
        elif num == step:
            state = "done" if completed else "active"
        else:
            state = "waiting"
        item: dict[str, Any] = {"num": num, "label": label, "state": state}
        summary = (summaries or {}).get(str(num))
        if summary:
            item["summary"] = summary
        out.append(item)
    return out


def derive_phase_label(step: int, completed: bool) -> str:
    """header 相位 chip 文案（与 steps 同源派生，不信模型自报）。"""
    if step >= 5 and completed:
        return "诊断完成"
    return f"第{step}步·{STEP_LABELS[step - 1]}"


def board_result_summary(board: dict[str, Any]) -> str:
    """回给模型的短结果（不回显面板内容，省上下文）。"""
    revision = board.get("revision")
    phase = board.get("phaseLabel", "")
    if board.get("status") == "concluded":
        return f"诊断面板已收尾（{phase}，revision {revision}），结论已同步到用户界面"
    return f"诊断面板已更新（{phase}，revision {revision}）"
