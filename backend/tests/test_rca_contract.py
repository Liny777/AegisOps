from __future__ import annotations

import json
import math

import pytest

from infra.rca_contract import (
    RcaBoardContractError,
    board_result_summary,
    derive_phase_label,
    derive_steps,
    normalize_board_arguments,
)

VALID_BOARD = {
    "step": 2,
    "step_completed": False,
    "title": "支付下单 P99 突增",
    "tiles": [{"label": "症状", "value": "P99 180ms→1.4s"}],
    "current_question": "Redis 连接饱和是慢查询还是连接泄漏导致？",
    "why": "两者恢复动作不同：慢查询→限流，连接泄漏→重启释放。",
    "facts": [{"text": "10:02 起 P99 由 180ms 升至 1.4s"}],
    "unknowns": [{"text": "连接是否泄漏"}],
    "sources": [{"name": "Prometheus", "status": "done", "tone": "good"}],
    "hypotheses": [{"text": "H1 Redis 连接泄漏", "tag": "支持", "tagTone": "good", "conf": 0.72}],
    "actions": [{"tier": "立即", "text": "重启 svc-a", "confirm": True, "impact": "3 实例",
                 "status": "待确认", "statusTone": "warning"}],
    "conclusion": "",
}


def test_board_contract_normalizes_and_maps_to_card_field_names() -> None:
    source = {**VALID_BOARD, "title": "  支付下单   P99 突增 ", "step": 2.0}
    normalized = normalize_board_arguments(source)

    assert normalized["step"] == 2 and normalized["step_completed"] is False
    assert normalized["title"] == "支付下单 P99 突增"
    # current_question → currentQ（对齐前端 RcaCardData 键名）
    assert normalized["currentQ"].startswith("Redis 连接饱和")
    assert "current_question" not in normalized
    # 空 conclusion = 本次未提交（增量语义：合并器保留上次的值）
    assert "conclusion" not in normalized
    assert normalized["hypotheses"][0]["conf"] == 0.72
    assert normalized["actions"][0]["confirm"] is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update({"revision": 7}),                               # revision 服务端权威，拒收
        lambda b: b.update({"steps": [{"num": 1, "state": "done"}]}),      # steps 派生键拒收
        lambda b: b.update({"status": "concluded"}),                       # status 派生键拒收
        lambda b: b.update({"title": "<img src=x onerror=alert(1)>"}),     # HTML
        lambda b: b.update({"why": "隐藏\u202e文本"}),                     # 控制字符
        lambda b: b.update({"step": 0}),
        lambda b: b.update({"step": 6}),
        lambda b: b.update({"step": True}),                                # bool 不是步骤号
        lambda b: b.update({"step": 2.5}),
        lambda b: b.update({"step_completed": "yes"}),                     # 非布尔
        lambda b: b.update({"title": "长" * 81}),                          # 超长
        lambda b: b["tiles"][0].update({"html": "<script>1</script>"}),    # 列表项未知键
        lambda b: b["sources"][0].update({"tone": "red"}),                 # tone 非法
        lambda b: b["hypotheses"][0].update({"conf": 1.5}),                # conf 越界
        lambda b: b["hypotheses"][0].update({"conf": math.nan}),           # conf 非有限
        lambda b: b["hypotheses"][0].update({"conf": True}),               # conf bool
        lambda b: b["actions"][0].update({"confirm": "true"}),             # confirm 非布尔
        lambda b: b.update({"tiles": [{"label": f"L{i}", "value": "v"} for i in range(7)]}),   # tiles 超量
        lambda b: b.update({"facts": [{"text": f"f{i}"} for i in range(21)]}),                  # facts 超量
        lambda b: b.update({"actions": [{"text": f"a{i}"} for i in range(9)]}),                 # actions 超量
    ],
)
def test_board_contract_rejects_unknown_derived_or_invalid_values(mutate) -> None:  # noqa: ANN001
    board = json.loads(json.dumps(VALID_BOARD))
    mutate(board)
    with pytest.raises(RcaBoardContractError):
        normalize_board_arguments(board)


def test_board_contract_fills_safe_defaults_for_optional_item_fields() -> None:
    normalized = normalize_board_arguments({
        "step": 3,
        "sources": [{"name": "Loki"}],                       # status/tone 缺省
        "hypotheses": [{"text": "H2 慢查询占用连接"}],        # tag/tagTone/conf 缺省
        "actions": [{"text": "加连接回收配置"}],              # tier/status/tone/confirm 缺省
    })
    assert normalized["sources"][0] == {"name": "Loki", "status": "running", "tone": "neutral"}
    hyp = normalized["hypotheses"][0]
    assert hyp["tag"] == "" and hyp["tagTone"] == "neutral" and hyp["conf"] == 0.0
    act = normalized["actions"][0]
    assert act["confirm"] is False and act["statusTone"] == "neutral" and act["tier"] and act["status"]


def test_derive_steps_is_the_only_state_machine() -> None:
    assert derive_steps(3, False) == [
        {"num": 1, "label": "范围", "state": "done"},
        {"num": 2, "label": "证据", "state": "done"},
        {"num": 3, "label": "假设", "state": "active"},
        {"num": 4, "label": "验证", "state": "waiting"},
        {"num": 5, "label": "结论", "state": "waiting"},
    ]
    assert [s["state"] for s in derive_steps(1, False)] == ["active", "waiting", "waiting", "waiting", "waiting"]
    assert [s["state"] for s in derive_steps(5, True)] == ["done"] * 5
    assert [s["state"] for s in derive_steps(2, True)] == ["done", "done", "waiting", "waiting", "waiting"]


def test_derive_steps_attaches_summaries_only_for_reported_steps() -> None:
    steps = derive_steps(3, False, {"1": "范围已锁定：APP-A 支付链路", "3": "H1 连接泄漏领先"})
    assert steps[0]["summary"] == "范围已锁定：APP-A 支付链路"
    assert "summary" not in steps[1]  # 未上报小结的步不输出 summary 键（旧快照/前端 optional 兼容）
    assert steps[2]["summary"] == "H1 连接泄漏领先"
    assert all("summary" not in s for s in steps[3:])
    # 缺省/空映射与二参调用完全同形（既有 deepEqual 断言零影响）
    assert derive_steps(3, False, None) == derive_steps(3, False)
    assert derive_steps(5, True, {}) == derive_steps(5, True)


def test_step_summary_normalizes_and_rejects_invalid() -> None:
    normalized = normalize_board_arguments({"step": 2, "step_summary": "  证据确认：Redis   连接打满 "})
    assert normalized["step_summary"] == "证据确认：Redis 连接打满"
    # 空串 = 本次未提交（增量语义）；超长 / 非字符串拒收
    assert "step_summary" not in normalize_board_arguments({"step": 2, "step_summary": ""})
    with pytest.raises(RcaBoardContractError):
        normalize_board_arguments({"step": 2, "step_summary": "长" * 121})
    with pytest.raises(RcaBoardContractError):
        normalize_board_arguments({"step": 2, "step_summary": ["小结"]})


def test_derive_phase_label_and_result_summary() -> None:
    assert derive_phase_label(2, False) == "第2步·证据"
    assert derive_phase_label(5, False) == "第5步·结论"
    assert derive_phase_label(5, True) == "诊断完成"

    running = {"phaseLabel": "第2步·证据", "revision": 3, "status": "in_progress"}
    assert board_result_summary(running) == "诊断面板已更新（第2步·证据，revision 3）"
    concluded = {"phaseLabel": "诊断完成", "revision": 6, "status": "concluded"}
    assert "已收尾" in board_result_summary(concluded) and "revision 6" in board_result_summary(concluded)
