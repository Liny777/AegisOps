from __future__ import annotations

import asyncio
import json
import math

import pytest

from infra.chart_contract import ChartContractError, chart_result_summary, normalize_chart_arguments
from infra.redact import sanitize_activity_payload


VALID_CHART = {
    "chart_type": "line",
    "title": "支付接口 P99",
    "description": "最近三次采样",
    "unit": "ms",
    "series": [
        {
            "name": "svc-payment-api",
            "data": [
                {"label": "10:00", "value": 210},
                {"label": "10:05", "value": 680},
                {"label": "10:10", "value": 320},
            ],
        },
    ],
}


def test_chart_contract_normalizes_supported_numeric_chart() -> None:
    source = {**VALID_CHART, "chart_type": " LINE ", "title": "  支付接口   P99 "}
    normalized = normalize_chart_arguments(source)

    assert normalized["chart_type"] == "line"
    assert normalized["title"] == "支付接口 P99"
    assert normalized["series"][0]["data"][1] == {"label": "10:05", "value": 680}
    assert chart_result_summary(normalized) == "已生成折线图“支付接口 P99”（1 个序列，3 个数据点）"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda chart: chart.update({"style": {"color": "red"}}),
        lambda chart: chart.update({"title": "<img src=x onerror=alert(1)>"}),
        lambda chart: chart.update({"description": "隐藏\u202e文本"}),
        lambda chart: chart["series"][0].update({"html": "<script>alert(1)</script>"}),
        lambda chart: chart["series"][0]["data"][0].update({"value": math.inf}),
        lambda chart: chart["series"].append({
            "name": "不一致横轴",
            "data": [{"label": "11:00", "value": 1}],
        }),
    ],
)
def test_chart_contract_rejects_executable_unknown_or_invalid_values(mutate) -> None:  # noqa: ANN001
    chart = json.loads(json.dumps(VALID_CHART))
    mutate(chart)
    with pytest.raises(ChartContractError):
        normalize_chart_arguments(chart)


def test_pie_contract_requires_one_non_negative_non_zero_series() -> None:
    chart = {
        "chart_type": "pie",
        "title": "错误类型",
        "series": [{"name": "错误数", "data": [{"label": "超时", "value": -1}]}],
    }
    with pytest.raises(ChartContractError, match="不能为负数"):
        normalize_chart_arguments(chart)
    chart["series"][0]["data"][0]["value"] = 0
    with pytest.raises(ChartContractError, match="大于 0"):
        normalize_chart_arguments(chart)
    chart["series"][0]["data"] = [
        {"label": f"类型 {index}", "value": index + 1} for index in range(13)
    ]
    with pytest.raises(ChartContractError, match="最多 12"):
        normalize_chart_arguments(chart)


def test_only_render_chart_gets_structured_arguments_after_redaction() -> None:
    chart = json.loads(json.dumps(VALID_CHART))
    chart["description"] = "token=super-secret-token"
    structured = sanitize_activity_payload(
        "openops.tool.call.started",
        {"tool": "render_chart", "arguments": chart},
    )

    assert structured["arguments"]["chart_type"] == "line"
    assert structured["arguments"]["description"] == "token=[REDACTED]"
    assert "super-secret-token" not in json.dumps(structured, ensure_ascii=False)

    generic = sanitize_activity_payload(
        "openops.tool.call.started",
        {"tool": "query_resource", "arguments": VALID_CHART},
    )
    assert set(generic["arguments"]) == {"summary"}

    unsafe_chart = {**VALID_CHART, "style": {"background": "url(javascript:alert(1))"}}
    downgraded = sanitize_activity_payload(
        "openops.tool.call.started",
        {"tool": "render_chart", "arguments": unsafe_chart},
    )
    assert set(downgraded["arguments"]) == {"summary"}


def test_agui_preserves_the_validated_chart_envelope() -> None:
    """受控参数经标准 TOOL_CALL_ARGS 到浏览器，其他活动仍走 CUSTOM。"""
    from app import agui_service

    async def scenario() -> list[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        chart_payload = sanitize_activity_payload(
            "openops.tool.call.started",
            {"tool": "render_chart", "arguments": VALID_CHART},
        )
        await queue.put({
            "event_type": "openops.tool.call.started",
            "event_id": "chart-call-1",
            "payload_redacted_json": chart_payload,
        })
        await queue.put({
            "event_type": "openops.tool.call.succeeded",
            "event_id": "chart-done-1",
            "message": "图表已生成",
            "payload_redacted_json": {
                "tool": "render_chart",
                "result_summary": "图表已生成",
            },
        })
        await queue.put({
            "event_type": "openops.task.completed",
            "event_id": "task-done-1",
            "message": "完成",
            "payload_redacted_json": {"conclusion": "完成"},
        })
        ctx = {
            "queue": queue,
            "run_id": "chart-run",
            "task_id": "chart-task",
            "thread_id": "chart-thread",
            "agui_run_id": "chart-agui-run",
            "user": {},
        }
        frames: list[dict] = []
        async for line in agui_service.stream(ctx):
            if line.startswith("data:"):
                frames.append(json.loads(line[5:].strip()))
        return frames

    frames = asyncio.run(scenario())
    start = next(frame for frame in frames if frame["type"] == "TOOL_CALL_START")
    args = next(frame for frame in frames if frame["type"] == "TOOL_CALL_ARGS")
    result = next(frame for frame in frames if frame["type"] == "TOOL_CALL_RESULT")

    assert start["toolCallName"] == "render_chart"
    assert args["toolCallId"] == start["toolCallId"] == result["toolCallId"]
    assert json.loads(args["delta"]) == normalize_chart_arguments(VALID_CHART)
