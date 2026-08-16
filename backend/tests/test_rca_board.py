from __future__ import annotations

import asyncio
from typing import Any

import pytest

import runtime.rca_board as rb
from infra.rca_contract import RcaBoardContractError
from runtime import task_registry
from runtime.rca_demo import rca as demo_rca
from runtime.task_registry import TaskState

RUN = {"audit_trace_id": "trace-board", "agent_team_instance_id": "inst"}


def _st(task_id: str = "tsk_board", run_id: str = "run_board") -> TaskState:
    return TaskState(task_id=task_id, run_id=run_id, user_id="0026demo01",
                     instance_id="inst", input_text="诊断测试")


@pytest.fixture()
def board_env(monkeypatch):
    """隔离 emit / 主任务快照落库（本文件只测合并状态机，不依赖 PG）。"""
    emitted: list[dict[str, Any]] = []
    snapshots: list[Any] = []

    async def _fake_emit(st, run, event_type, **kw):
        emitted.append({"st": st, "event_type": event_type, **kw})

    async def _fake_upsert(st, status, trace):
        snapshots.append((st, status, trace))

    monkeypatch.setattr(rb, "emit", _fake_emit)
    monkeypatch.setattr(rb.task_states, "upsert_snapshot", _fake_upsert)
    yield emitted, snapshots
    task_registry.reset()


def _apply(st: TaskState, **raw: Any) -> str:
    raw.setdefault("step", 1)
    return asyncio.run(rb.apply_board_update(st, RUN, raw))


def test_revision_is_server_owned_and_model_submission_rejected(board_env) -> None:
    emitted, _ = board_env
    st = _st()
    _apply(st, step=1, title="支付下单 P99 突增")
    _apply(st, step=2, facts=[{"text": "P99 1.4s"}])
    assert st.rca["revision"] == 2 and st.rca_source == "model"
    assert [e["payload"]["revision"] for e in emitted] == [1, 2]

    # 模型提交 revision → 契约拒收（服务端权威）
    with pytest.raises(RcaBoardContractError):
        _apply(st, step=3, revision=9)
    assert st.rca["revision"] == 2  # 拒收即不落任何状态变更


def test_step_clamps_forward_only_but_allows_skipping(board_env) -> None:
    emitted, _ = board_env
    st = _st()
    _apply(st, step=3, title="支付下单 P99 突增")
    text = _apply(st, step=1)  # 回退：不报错、钳制并在返回文本注明
    assert "只能前进不能回退" in text
    steps = {s["num"]: s["state"] for s in st.rca["steps"]}
    assert steps[3] == "active" and steps[1] == "done" and steps[4] == "waiting"
    assert st.rca["phaseLabel"] == "第3步·假设"

    # 同步骤 completed 也不可回退：done 后再报 active 保持 done
    _apply(st, step=3, step_completed=True)
    _apply(st, step=3)
    assert {s["num"]: s["state"] for s in st.rca["steps"]}[3] == "done"

    # 允许跳步（证据充分快进合理）
    _apply(st, step=5)
    assert st.rca["phaseLabel"] == "第5步·结论"
    assert [e["payload"]["revision"] for e in emitted] == [1, 2, 3, 4, 5]  # 钳制调用照样计一次修订


def test_field_level_merge_keeps_unsubmitted_and_replaces_lists_wholly(board_env) -> None:
    board_env
    st = _st()
    _apply(st, step=1, title="支付下单 P99 突增",
           facts=[{"text": "事实A"}, {"text": "事实B"}],
           sources=[{"name": "Prometheus", "status": "done", "tone": "good"}])
    _apply(st, step=2, hypotheses=[{"text": "H1 连接泄漏", "conf": 0.7}])
    # 未提交字段保留；新字段并入
    assert st.rca["title"] == "支付下单 P99 突增"
    assert [f["text"] for f in st.rca["facts"]] == ["事实A", "事实B"]
    assert st.rca["hypotheses"][0]["text"] == "H1 连接泄漏"
    # 提交的列表整体替换（不是逐项追加）
    _apply(st, step=2, facts=[{"text": "事实C（修正）"}])
    assert [f["text"] for f in st.rca["facts"]] == ["事实C（修正）"]
    # 骨架键齐全（前端必填字段不缺键）
    assert {"tiles", "unknowns", "actions", "currentQ", "why", "conclusion"} <= set(st.rca)


def test_step5_completed_concludes_and_requires_conclusion(board_env) -> None:
    board_env
    st = _st()
    _apply(st, step=4, title="支付下单 P99 突增")
    # 收尾必须带诊断结论（本次或此前已提交）——缺失即契约错误、状态不动
    with pytest.raises(RcaBoardContractError, match="conclusion"):
        _apply(st, step=5, step_completed=True)
    assert st.rca["status"] == "in_progress" and st.rca["revision"] == 1

    text = _apply(st, step=5, step_completed=True,
                  conclusion="已确认 H1：重启 svc-a 后连接回落，建议补连接回收配置。")
    assert st.rca["status"] == "concluded"
    assert st.rca["phaseLabel"] == "诊断完成"
    assert all(s["state"] == "done" for s in st.rca["steps"])
    assert "已收尾" in text


def test_child_updates_merge_into_leader_and_snapshot_main_task(board_env) -> None:
    emitted, snapshots = board_env
    leader = _st(task_id="tsk_leader", run_id="run_lead")
    task_registry.put(leader)
    child = _st(task_id="tsk_leader.diagnose-cafe0001", run_id="run_lead")
    child.agent_key, child.leader_task_id = "diagnose", "tsk_leader"
    task_registry.register_subtask(child)

    _apply(child, step=2, title="子 Agent 诊断", facts=[{"text": "拓扑证据"}])
    # 面板落在 leader（get_state 恢复与快照零改动生效）；子自身不持有面板
    assert leader.rca is not None and leader.rca["title"] == "子 Agent 诊断"
    assert leader.rca_source == "model" and child.rca is None
    # 事件用调用方（子）st 发——活动栏归组到子 Agent 角色；payload 是 owner 面板
    assert emitted[-1]["st"] is child and emitted[-1]["payload"] is leader.rca
    # emit 的主任务守卫不落子路径快照 → 显式补 owner 快照
    assert len(snapshots) == 1 and snapshots[0][0] is leader and snapshots[0][2] == "trace-board"

    # 主 Agent 自己更新：owner 即自身，无需补快照（emit 主任务守卫本就覆盖）
    _apply(leader, step=3)
    assert len(snapshots) == 1
    assert leader.rca["revision"] == 2


def test_child_cannot_advance_step_or_conclude_the_board(board_env) -> None:
    """子任务只并内容：并行子 Agent 自认为查完就报 step=5 曾把面板一步打成「诊断完成」。"""
    board_env
    leader = _st(task_id="tsk_leader", run_id="run_lead")
    task_registry.put(leader)
    child = _st(task_id="tsk_leader.diagnose-cafe0001", run_id="run_lead")
    child.agent_key, child.leader_task_id = "diagnose", "tsk_leader"
    task_registry.register_subtask(child)

    _apply(leader, step=2, title="支付下单 P99 突增", step_summary="证据：P99 1.4s")
    text = _apply(child, step=5, step_completed=True, step_summary="子 Agent 自认为收尾",
                  conclusion="根因已确认，建议重启", facts=[{"text": "拓扑证据"}])

    # 步骤/状态/结论一律不动；内容照常并入
    assert leader.rca["status"] == "in_progress"
    assert leader.rca["phaseLabel"] == "第2步·证据"
    assert leader.rca["conclusion"] == ""
    assert [f["text"] for f in leader.rca["facts"]] == ["拓扑证据"]
    # 子小结不落位（否则会提前种在第5步上，等面板推进到 5 时突然冒出来）
    assert leader.rca["step_summaries"] == {"2": "证据：P99 1.4s"}
    assert "子任务只能提交内容" in text and "步骤推进" in text and "conclusion" in text
    # 降权不是回退钳制，不得混报
    assert "只能前进不能回退" not in text

    # 主任务照常收尾（子任务的降权不污染 owner 权限）
    _apply(leader, step=5, step_completed=True, conclusion="根因 H1 已确认，建议补连接回收配置。")
    assert leader.rca["status"] == "concluded"


def test_child_update_without_owner_board_lands_on_step1_not_concluded(board_env) -> None:
    """owner 尚无模型面板时子任务先到：内容并入，但不伪造进度（落第1步、非闭环）。"""
    board_env
    leader = _st(task_id="tsk_leader2", run_id="run_lead2")
    task_registry.put(leader)
    child = _st(task_id="tsk_leader2.metric-cafe0002", run_id="run_lead2")
    child.agent_key, child.leader_task_id = "metric", "tsk_leader2"
    task_registry.register_subtask(child)

    _apply(child, step=4, step_completed=True, facts=[{"text": "CPU 95%"}])
    assert leader.rca["phaseLabel"] == "第1步·范围"
    assert leader.rca["status"] == "in_progress"
    assert [f["text"] for f in leader.rca["facts"]] == ["CPU 95%"]


def test_child_content_only_update_has_no_demotion_notice(board_env) -> None:
    """子任务老实只提内容（step=owner 现值）时不加降权噪音。"""
    board_env
    leader = _st(task_id="tsk_leader3", run_id="run_lead3")
    task_registry.put(leader)
    child = _st(task_id="tsk_leader3.log-cafe0003", run_id="run_lead3")
    child.agent_key, child.leader_task_id = "log", "tsk_leader3"
    task_registry.register_subtask(child)

    _apply(leader, step=2, title="支付下单 P99 突增")
    text = _apply(child, step=2, sources=[{"name": "log-agent", "status": "已完成", "tone": "good"}])
    assert "子任务只能提交内容" not in text
    assert leader.rca["sources"][0]["name"] == "log-agent"


def test_first_model_call_discards_demo_board_but_continues_revision(board_env) -> None:
    emitted, _ = board_env
    st = _st()
    st.rca = demo_rca(2, "验证 H1")  # demo 剧本先跑过（revision 2，含剧本 facts/结论）
    st.rca_source = "demo"

    _apply(st, step=1, title="真实事件标题")
    # 内容从骨架起步：demo 剧本的事实/假设/结论全部丢弃
    assert st.rca["title"] == "真实事件标题"
    assert st.rca["facts"] == [] and st.rca["hypotheses"] == [] and st.rca["conclusion"] == ""
    # revision 接续 demo（2+1）：同 task 前端单调守卫不丢这次更新
    assert st.rca["revision"] == 3
    assert st.rca_source == "model"
    assert emitted[-1]["payload"]["revision"] == 3


def test_revision_base_covers_demo_left_on_caller_child(board_env) -> None:
    """demo 剧本把面板写在调用方子 Agent 自己的 st 上（rev 1/2 已随子 task_id 发出过事件）：
    revision 基数必须取 owner 与调用方的 max，否则模型面板从 1 重计撞号，
    前端同段单调守卫会把前几次真实更新静默丢弃。"""
    emitted, _ = board_env
    leader = _st(task_id="tsk_leader2", run_id="run_lead2")
    task_registry.put(leader)
    child = _st(task_id="tsk_leader2.diagnose-cafe0002", run_id="run_lead2")
    child.agent_key, child.leader_task_id = "diagnose", "tsk_leader2"
    task_registry.register_subtask(child)
    child.rca = demo_rca(2, "验证 H1")  # demo 工具落在子 st（query_resource 第 2 次）
    child.rca_source = "demo"

    _apply(child, step=1, title="真实诊断")
    assert leader.rca["revision"] == 3  # max(owner=0, caller demo=2) + 1
    assert emitted[-1]["payload"]["revision"] == 3


def test_payload_carries_owner_board_task_id_for_frontend_segmentation(board_env) -> None:
    """事件 envelope 的 task_id 是调用方（子 Agent 归组用）；前端 revision 分段必须用
    payload.board_task_id（owner 主任务身份），否则子/主交替发事件被误判「新任务」重置守卫。"""
    emitted, _ = board_env
    leader = _st(task_id="tsk_leader3", run_id="run_lead3")
    task_registry.put(leader)
    child = _st(task_id="tsk_leader3.diagnose-cafe0003", run_id="run_lead3")
    child.agent_key, child.leader_task_id = "diagnose", "tsk_leader3"
    task_registry.register_subtask(child)

    _apply(child, step=2, title="子报进度")
    _apply(leader, step=3)
    assert [e["payload"]["board_task_id"] for e in emitted] == ["tsk_leader3", "tsk_leader3"]


def test_reopen_with_conclusion_rederives_steps_and_bumps_revision(board_env) -> None:
    """安全兜底（恢复被拒/被拦）只改 status 会留下「五步全 done + in_progress」矛盾形状：
    reopen 必须把 steps/phaseLabel 与 status 一起重派生，revision 自增防前端幂等丢弃。"""
    st = _st(task_id="tsk_reopen")
    _apply(st, step=5, step_completed=True, title="闭环", conclusion="根因已定位，建议重启释放连接")
    assert st.rca["status"] == "concluded"

    reopened = rb.reopen_with_conclusion(st.rca, "恢复动作被拒绝：保持观察。")
    assert reopened["status"] == "in_progress"
    assert reopened["conclusion"] == "恢复动作被拒绝：保持观察。"
    assert reopened["phaseLabel"] == "第5步·结论"
    assert [s["state"] for s in reopened["steps"]] == ["done", "done", "done", "done", "active"]
    assert reopened["revision"] == st.rca["revision"] + 1
    assert reopened["board_task_id"] == "tsk_reopen"  # 分段身份原样保留

    # 未收尾面板：步骤位置不动，只覆盖结论并拉回 in_progress
    st2 = _st(task_id="tsk_reopen2")
    _apply(st2, step=3, title="进行中")
    reopened2 = rb.reopen_with_conclusion(st2.rca, "恢复未执行。")
    assert reopened2["phaseLabel"] == "第3步·假设"
    assert [s["state"] for s in reopened2["steps"]] == ["done", "done", "active", "waiting", "waiting"]


def test_step_summaries_accumulate_across_steps(board_env) -> None:
    """步骤小结逐键累积：历史步小结一直保留；未提交小结的步不输出 summary 键。"""
    board_env
    st = _st(task_id="tsk_sum")
    _apply(st, step=1, title="支付下单 P99 突增", step_summary="范围锁定：APP-A 支付链路")
    _apply(st, step=2, step_summary="证据确认：Redis 连接打满")
    _apply(st, step=3)  # 本次未提交小结
    by_num = {s["num"]: s for s in st.rca["steps"]}
    assert by_num[1]["summary"] == "范围锁定：APP-A 支付链路"
    assert by_num[2]["summary"] == "证据确认：Redis 连接打满"
    assert "summary" not in by_num[3] and "summary" not in by_num[4]
    # 内部累积载体随面板保留（快照恢复后不丢）；同步骤重复提交覆盖旧小结
    assert st.rca["step_summaries"] == {"1": "范围锁定：APP-A 支付链路", "2": "证据确认：Redis 连接打满"}
    _apply(st, step=3, step_summary="H1 连接泄漏领先")
    assert {s["num"]: s.get("summary") for s in st.rca["steps"]}[3] == "H1 连接泄漏领先"


def test_step_summaries_cleared_when_demo_board_discarded(board_env) -> None:
    """demo 剧本 steps 自带 summary：模型首调接管时与其他内容一起整体丢弃、从空表起步。"""
    board_env
    st = _st(task_id="tsk_sum_demo")
    st.rca = demo_rca(2, "验证 H1")
    st.rca_source = "demo"
    _apply(st, step=1, title="真实事件", step_summary="真实范围小结")
    assert st.rca["step_summaries"] == {"1": "真实范围小结"}
    assert [s.get("summary") for s in st.rca["steps"]] == ["真实范围小结", None, None, None, None]


def test_reopen_keeps_step_summaries(board_env) -> None:
    """reopen 重派生 steps 时必须回传小结映射，否则安全兜底一次就把全部步骤摘要清空。"""
    board_env
    st = _st(task_id="tsk_sum_reopen")
    _apply(st, step=5, step_completed=True, title="闭环", step_summary="结论：H1 确认",
           conclusion="根因已定位，建议重启释放连接")
    reopened = rb.reopen_with_conclusion(st.rca, "恢复动作被拒绝：保持观察。")
    assert reopened["step_summaries"] == {"5": "结论：H1 确认"}
    assert {s["num"]: s.get("summary") for s in reopened["steps"]}[5] == "结论：H1 确认"


def test_clamped_regression_still_lands_summary_at_submitted_step(board_env) -> None:
    """钳制回退时小结仍按提交步号落位：补交历史步小结合理，不能被钳到当前步上。"""
    board_env
    st = _st(task_id="tsk_sum_clamp")
    _apply(st, step=3, title="支付下单 P99 突增")
    text = _apply(st, step=2, step_summary="证据补充：Loki 日志比对完成")  # 回退：步骤钳在第3步
    assert "只能前进不能回退" in text
    by_num = {s["num"]: s for s in st.rca["steps"]}
    assert by_num[2]["summary"] == "证据补充：Loki 日志比对完成"
    assert by_num[3]["state"] == "active" and "summary" not in by_num[3]


def test_demo_payload_shape_is_internally_consistent() -> None:
    """demo 与真流程（rca_board 派生）同形约定：concluded 时不得存在 active 步。"""
    for revision in (1, 2, 3):
        payload = demo_rca(revision, "阶段")
        states = [s["state"] for s in payload["steps"]]
        if payload["status"] == "concluded":
            assert "active" not in states and set(states) == {"done"}
        else:
            assert "active" in states


def test_reopen_preserves_demo_inline_step_summaries() -> None:
    """demo 面板（rca_demo 产）小结只内嵌在 steps[].summary、无 step_summaries 载体：
    reopen 必须从现有 steps 反推小结，否则一次审批拒绝就把五步小结整体抹掉。"""
    panel = demo_rca(3, "已闭环")
    demo_summaries = {s["num"]: s["summary"] for s in panel["steps"] if s.get("summary")}
    assert demo_summaries  # 前置：demo 剧本确实带小结

    reopened = rb.reopen_with_conclusion(panel, "恢复动作被拒绝：保持观察。")
    assert reopened["status"] == "in_progress"
    reopened_summaries = {s["num"]: s.get("summary") for s in reopened["steps"] if s.get("summary")}
    assert reopened_summaries == demo_summaries
