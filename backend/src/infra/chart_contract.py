"""受控图表展示契约。

模型只能提交数值与短文本；颜色、尺寸、SVG 与交互完全由前端固定实现。该模块同时被
AgentScope 工具和活动事件脱敏层调用，确保进入 AG-UI 的结构已经过同一套严格校验。
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any


class ChartContractError(ValueError):
    """图表参数不符合公开展示契约。"""


_TOP_LEVEL_KEYS = {"chart_type", "title", "description", "unit", "series"}
_SERIES_KEYS = {"name", "data"}
_POINT_KEYS = {"label", "value"}
_CHART_TYPES = {"line", "bar", "pie"}
_MAX_SERIES = 6
_MAX_POINTS_PER_SERIES = 60
_MAX_PIE_POINTS = 12
_MAX_TOTAL_POINTS = 240
_MAX_ABS_VALUE = 1_000_000_000_000_000


def _short_text(value: Any, field: str, *, limit: int, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ChartContractError(f"{field} 必须是字符串")
    if "<" in value or ">" in value:
        raise ChartContractError(f"{field} 不支持 HTML 或尖括号")
    if any(unicodedata.category(char).startswith("C") and char not in "\t\r\n" for char in value):
        raise ChartContractError(f"{field} 包含不支持的控制字符")
    text = re.sub(r"\s+", " ", value).strip()
    if required and not text:
        raise ChartContractError(f"{field} 不能为空")
    if len(text) > limit:
        raise ChartContractError(f"{field} 最长 {limit} 个字符")
    return text


def _only_keys(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        raise ChartContractError(f"{field} 包含不支持的字段：{', '.join(unknown)}")


def normalize_chart_arguments(value: Any) -> dict[str, Any]:
    """校验并返回可公开进入 AG-UI 的规范图表对象。

    支持 line/bar/pie；最多 6 个序列、折线/柱状单序列 60 点、合计 240 点，饼图最多
    12 个扇区。折线和柱状图各序列必须使用相同的横轴标签；饼图只能有一个序列，
    且数值必须非负并至少有一个正数。
    """
    if not isinstance(value, dict):
        raise ChartContractError("图表参数必须是对象")
    _only_keys(value, _TOP_LEVEL_KEYS, "图表参数")

    raw_type = value.get("chart_type")
    chart_type = raw_type.strip().lower() if isinstance(raw_type, str) else ""
    if chart_type not in _CHART_TYPES:
        raise ChartContractError("chart_type 只支持 line、bar 或 pie")

    title = _short_text(value.get("title"), "title", limit=120)
    description = _short_text(value.get("description"), "description", limit=300, required=False)
    unit = _short_text(value.get("unit"), "unit", limit=24, required=False)

    raw_series = value.get("series")
    if not isinstance(raw_series, list) or not raw_series:
        raise ChartContractError("series 必须是非空数组")
    if len(raw_series) > _MAX_SERIES:
        raise ChartContractError(f"series 最多 {_MAX_SERIES} 个序列")
    if chart_type == "pie" and len(raw_series) != 1:
        raise ChartContractError("饼图只能包含一个序列")

    normalized_series: list[dict[str, Any]] = []
    seen_series: set[str] = set()
    total_points = 0
    expected_labels: list[str] | None = None
    for series_index, raw_item in enumerate(raw_series):
        field = f"series[{series_index}]"
        if not isinstance(raw_item, dict):
            raise ChartContractError(f"{field} 必须是对象")
        _only_keys(raw_item, _SERIES_KEYS, field)
        name = _short_text(raw_item.get("name"), f"{field}.name", limit=60)
        if name in seen_series:
            raise ChartContractError("序列名称不能重复")
        seen_series.add(name)

        raw_data = raw_item.get("data")
        if not isinstance(raw_data, list) or not raw_data:
            raise ChartContractError(f"{field}.data 必须是非空数组")
        if len(raw_data) > _MAX_POINTS_PER_SERIES:
            raise ChartContractError(f"{field}.data 最多 {_MAX_POINTS_PER_SERIES} 个点")
        if chart_type == "pie" and len(raw_data) > _MAX_PIE_POINTS:
            raise ChartContractError(f"饼图最多 {_MAX_PIE_POINTS} 个扇区")
        total_points += len(raw_data)
        if total_points > _MAX_TOTAL_POINTS:
            raise ChartContractError(f"图表合计最多 {_MAX_TOTAL_POINTS} 个点")

        points: list[dict[str, Any]] = []
        labels: list[str] = []
        seen_labels: set[str] = set()
        positive_pie_value = False
        for point_index, raw_point in enumerate(raw_data):
            point_field = f"{field}.data[{point_index}]"
            if not isinstance(raw_point, dict):
                raise ChartContractError(f"{point_field} 必须是对象")
            _only_keys(raw_point, _POINT_KEYS, point_field)
            label = _short_text(raw_point.get("label"), f"{point_field}.label", limit=80)
            if label in seen_labels:
                raise ChartContractError(f"{field} 的数据标签不能重复")
            seen_labels.add(label)
            labels.append(label)

            number = raw_point.get("value")
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise ChartContractError(f"{point_field}.value 必须是数字")
            if not math.isfinite(number) or abs(number) > _MAX_ABS_VALUE:
                raise ChartContractError(f"{point_field}.value 超出允许范围")
            if chart_type == "pie" and number < 0:
                raise ChartContractError("饼图数值不能为负数")
            positive_pie_value = positive_pie_value or number > 0
            points.append({"label": label, "value": number})

        if chart_type == "pie" and not positive_pie_value:
            raise ChartContractError("饼图至少需要一个大于 0 的数值")
        if chart_type != "pie":
            if expected_labels is None:
                expected_labels = labels
            elif labels != expected_labels:
                raise ChartContractError("折线图和柱状图的各序列必须使用相同标签顺序")
        normalized_series.append({"name": name, "data": points})

    result: dict[str, Any] = {
        "chart_type": chart_type,
        "title": title,
        "series": normalized_series,
    }
    if description:
        result["description"] = description
    if unit:
        result["unit"] = unit
    return result


def chart_result_summary(chart: dict[str, Any]) -> str:
    """生成不含原始数值的短结果，供工具结果与活动栏展示。"""
    point_count = sum(len(item["data"]) for item in chart["series"])
    type_label = {"line": "折线图", "bar": "柱状图", "pie": "饼图"}[chart["chart_type"]]
    return f"已生成{type_label}“{chart['title']}”（{len(chart['series'])} 个序列，{point_count} 个数据点）"
