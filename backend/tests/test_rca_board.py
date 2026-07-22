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
                     instance_id="inst", input_text="定界测试")


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
    # 收尾必须带定界结论（本次或此前已提交）——缺失即契约错误、状态不动
    with pytest.raises(RcaBoardContractError, match="conclusion"):
        _apply(st, step=5, step_completed=True)
    assert st.rca["status"] == "in_progress" and st.rca["revision"] == 1

    text = _apply(st, step=5, step_completed=True,
                  conclusion="已确认 H1：重启 svc-a 后连接回落，建议补连接回收配置。")
    assert st.rca["status"] == "concluded"
    assert st.rca["phaseLabel"] == "已定界"
    assert all(s["state"] == "done" for s in st.rca["steps"])
    assert "已收尾" in text


def test_child_updates_merge_into_leader_and_snapshot_main_task(board_env) -> None:
    emitted, snapshots = board_env
    leader = _st(task_id="tsk_leader", run_id="run_lead")
    task_registry.put(leader)
    child = _st(task_id="tsk_leader.diagnose-cafe0001", run_id="run_lead")
    child.agent_key, child.leader_task_id = "diagnose", "tsk_leader"
    task_registry.register_subtask(child)

    _apply(child, step=2, title="子 Agent 定界", facts=[{"text": "拓扑证据"}])
    # 面板落在 leader（get_state 恢复与快照零改动生效）；子自身不持有面板
    assert leader.rca is not None and leader.rca["title"] == "子 Agent 定界"
    assert leader.rca_source == "model" and child.rca is None
    # 事件用调用方（子）st 发——活动栏归组到子 Agent 角色；payload 是 owner 面板
    assert emitted[-1]["st"] is child and emitted[-1]["payload"] is leader.rca
    # emit 的主任务守卫不落子路径快照 → 显式补 owner 快照
    assert len(snapshots) == 1 and snapshots[0][0] is leader and snapshots[0][2] == "trace-board"

    # 主 Agent 自己更新：owner 即自身，无需补快照（emit 主任务守卫本就覆盖）
    _apply(leader, step=3)
    assert len(snapshots) == 1
    assert leader.rca["revision"] == 2


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

    _apply(child, step=1, title="真实定界")
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


def test_demo_payload_shape_is_internally_consistent() -> None:
    """demo 与真流程（rca_board 派生）同形约定：concluded 时不得存在 active 步。"""
    for revision in (1, 2, 3):
        payload = demo_rca(revision, "阶段")
        states = [s["state"] for s in payload["steps"]]
        if payload["status"] == "concluded":
            assert "active" not in states and set(states) == {"done"}
        else:
            assert "active" in states
