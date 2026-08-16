"""假设 checkpoint（diagnosis_checkpoint）：等待/超时/hold/决策路由与 HTTP 端点。

纯状态机部分仿 test_rca_board 的 board_env 口径（fake emit、不连 PG）；
HTTP 端点部分用 client 夹具 + 真 run 行 + 手工植入 TaskState。
conftest 已把 OPENOPS_DIAG_CHECKPOINT_TIMEOUT_S 默认置 0，本文件按用例 monkeypatch 常量显式开。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

import runtime.diagnosis_checkpoint as dc
from app import run_state_service as rss
from domain.schemas import CheckpointDecisionRequest
from runtime import task_registry
from runtime.task_registry import TaskState
from conftest import USER_HEADERS, create_instance, create_run, unwrap

RUN = {"audit_trace_id": "trace-ckpt", "agent_team_instance_id": "inst"}


def _st(task_id: str = "tsk_ckpt", run_id: str = "run_ckpt") -> TaskState:
    return TaskState(task_id=task_id, run_id=run_id, user_id="0026demo01",
                     instance_id="inst", input_text="诊断测试")


@pytest.fixture()
def ckpt_env(monkeypatch):
    """隔离 emit + 极小超时（本文件不测真实 10s，只测语义）。"""
    emitted: list[dict[str, Any]] = []

    async def _fake_emit(st, run, event_type, **kw):
        emitted.append({"st": st, "event_type": event_type, **kw})

    monkeypatch.setattr(dc, "emit", _fake_emit)
    monkeypatch.setattr(dc, "CHECKPOINT_TIMEOUT_S", 0.08)
    monkeypatch.setattr(dc, "CHECKPOINT_HOLD_S", 0.5)
    yield emitted
    task_registry.reset()


def _pause(st: TaskState, *, step: int = 3, completed: bool = False) -> str:
    return asyncio.run(dc.maybe_pause_for_user(st, RUN, step=step, step_completed=completed))


async def _pause_with_decision(st: TaskState, decisions: list[dict[str, Any]],
                               *, step: int = 3) -> str:
    """启动等待，待卡片挂起后按序注入决策（模拟 decide 端点置位）。"""
    job = asyncio.ensure_future(dc.maybe_pause_for_user(st, RUN, step=step, step_completed=False))
    for decision in decisions:
        for _ in range(200):
            if st.checkpoint_id and not st.checkpoint_ev.is_set():
                break
            await asyncio.sleep(0.005)
        st.checkpoint_result = decision
        st.checkpoint_ev.set()
    return await job


def test_timeout_auto_continues(ckpt_env) -> None:
    emitted = ckpt_env
    st = _st()
    text = _pause(st)
    assert "超时未操作" in text and "验证（Step 4）" in text
    assert [e["event_type"] for e in emitted] == [
        "openops.diagnosis.checkpoint.opened", "openops.diagnosis.checkpoint.closed"]
    opened, closed = emitted
    assert opened["payload"]["deadline_at"] and opened["payload"]["step"] == 3
    assert closed["payload"]["timed_out"] is True and closed["payload"]["action"] == "continue"
    assert closed["payload"]["checkpoint_id"] == opened["payload"]["checkpoint_id"]
    # 挂起态清理 + 只弹一次标记
    assert st.checkpoint_id is None and st.checkpoint_deadline is None
    assert st.checkpoint_done is True


def test_user_continue_closes_without_timeout(ckpt_env) -> None:
    emitted = ckpt_env
    st = _st()
    text = asyncio.run(_pause_with_decision(st, [{"action": "continue", "text": ""}]))
    assert "用户已确认继续排查" in text
    closed = emitted[-1]
    assert closed["event_type"] == "openops.diagnosis.checkpoint.closed"
    assert closed["payload"]["timed_out"] is False and closed["decision"] == "continue"


def test_add_hypothesis_feeds_text_back_to_model(ckpt_env) -> None:
    emitted = ckpt_env
    st = _st()
    text = asyncio.run(_pause_with_decision(
        st, [{"action": "add_hypothesis", "text": "H5 网关连接池打满"}]))
    # 回注文本带用户假设 + 重交 step=3 指令（不重弹靠 checkpoint_done）
    assert "H5 网关连接池打满" in text
    assert "重新调用 update_diagnosis_board(step=3)" in text
    closed = emitted[-1]
    assert closed["payload"]["action"] == "add_hypothesis"
    assert closed["payload"]["hypothesis_chars"] == len("H5 网关连接池打满")
    # 用户原文不进事件 payload（只回注模型）
    assert "H5" not in str(closed["payload"].get("summary", ""))


def test_hold_extends_window_then_decision_lands(ckpt_env) -> None:
    emitted = ckpt_env
    st = _st()

    async def _scenario() -> str:
        job = asyncio.ensure_future(dc.maybe_pause_for_user(st, RUN, step=3, step_completed=False))
        for _ in range(200):
            if st.checkpoint_id:
                break
            await asyncio.sleep(0.005)
        first_deadline = st.checkpoint_deadline
        st.checkpoint_result = {"action": "hold"}
        st.checkpoint_ev.set()
        # 超过首窗（0.08s）仍未收尾 = hold 生效
        await asyncio.sleep(0.15)
        assert not job.done(), "hold 后仍应挂起等待"
        assert st.checkpoint_deadline != first_deadline
        st.checkpoint_result = {"action": "add_hypothesis", "text": "H6 DNS 抖动"}
        st.checkpoint_ev.set()
        return await job

    text = asyncio.run(_scenario())
    assert "H6 DNS 抖动" in text
    kinds = [e["event_type"] for e in emitted]
    assert kinds == ["openops.diagnosis.checkpoint.opened",
                     "openops.diagnosis.checkpoint.extended",
                     "openops.diagnosis.checkpoint.closed"]


@pytest.mark.parametrize("kwargs", [
    {"step": 2},                            # 未到假设步
    {"step": 5, "completed": True},         # 一步收尾（跳步治理另管）
])
def test_no_pause_before_hypothesis_or_on_conclusion(ckpt_env, kwargs) -> None:
    emitted = ckpt_env
    st = _st()
    assert _pause(st, step=kwargs["step"], completed=kwargs.get("completed", False)) == ""
    assert emitted == [] and st.checkpoint_done is False


def test_no_pause_for_child_disabled_or_repeat(ckpt_env, monkeypatch) -> None:
    emitted = ckpt_env
    # 子任务不弹（连步骤都推不动）
    child = _st(task_id="tsk_ckpt.diagnose-cafe0001")
    child.leader_task_id = "tsk_ckpt"
    assert _pause(child) == "" and child.checkpoint_done is False
    # 只弹一次：补假设后模型重交 step=3 不再暂停（防自我死锁）
    st = _st()
    assert _pause(st) != ""
    assert _pause(st) == ""
    assert len([e for e in emitted if e["event_type"].endswith("opened")]) == 1
    # 特性关闭（超时 0）
    monkeypatch.setattr(dc, "CHECKPOINT_TIMEOUT_S", 0.0)
    st2 = _st(task_id="tsk_ckpt2", run_id="run_ckpt2")
    assert _pause(st2) == "" and st2.checkpoint_done is False


def test_cancel_propagates_and_clears_pending(ckpt_env, monkeypatch) -> None:
    emitted = ckpt_env
    monkeypatch.setattr(dc, "CHECKPOINT_TIMEOUT_S", 5.0)  # 长窗：取消必须先于超时
    st = _st()

    async def _scenario() -> None:
        job = asyncio.ensure_future(dc.maybe_pause_for_user(st, RUN, step=3, step_completed=False))
        for _ in range(200):
            if st.checkpoint_id:
                break
            await asyncio.sleep(0.005)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job

    asyncio.run(_scenario())
    assert st.checkpoint_id is None and st.checkpoint_deadline is None
    # 取消不发 closed（task.cancelled 收尾对话）
    assert [e["event_type"] for e in emitted] == ["openops.diagnosis.checkpoint.opened"]


# ---------- HTTP 端点（decide + /state 投影） ----------


def _plant_pending_state(run_id: str, *, user_id: str = "0026demo01") -> TaskState:
    st = TaskState(task_id="tsk_http", run_id=run_id, user_id=user_id,
                   instance_id="inst", input_text="诊断")
    st.checkpoint_id = "ckpt-http-1"
    st.checkpoint_deadline = "2099-01-01T00:00:00+00:00"
    task_registry.put(st)
    return st


def test_decide_endpoint_accepts_and_sets_handshake(client) -> None:
    inst = create_instance(client)
    run = create_run(client, inst["instance_id"])
    st = _plant_pending_state(run["agent_run_id"])
    body = {"client_request_id": f"crid_{time.time_ns()}", "checkpoint_id": "ckpt-http-1",
            "action": "add_hypothesis", "text": "H7 缓存击穿"}
    data = unwrap(client.post(
        f"/api/openops/v1/agent-runs/{run['agent_run_id']}/diagnosis-checkpoint:decide",
        headers=USER_HEADERS, json=body))
    assert data["status"] == "accepted" and data["action"] == "add_hypothesis"
    assert st.checkpoint_ev.is_set()
    assert st.checkpoint_result == {"action": "add_hypothesis", "text": "H7 缓存击穿"}

    # /state 挂起投影（决策后 checkpoint_id 由等待方清理，这里仍挂起中）
    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state",
                              headers=USER_HEADERS))
    assert state["diagnosis_checkpoint"] == {"checkpoint_id": "ckpt-http-1",
                                             "deadline_at": "2099-01-01T00:00:00+00:00"}


def test_decide_endpoint_idempotent_and_validates(client) -> None:
    inst = create_instance(client)
    run = create_run(client, inst["instance_id"])
    rid = run["agent_run_id"]
    crid = lambda: f"crid_{time.time_ns()}"  # noqa: E731

    # 无挂起卡：幂等 closed（双击/超时后迟到决策）
    data = unwrap(client.post(f"/api/openops/v1/agent-runs/{rid}/diagnosis-checkpoint:decide",
                              headers=USER_HEADERS,
                              json={"client_request_id": crid(), "checkpoint_id": "stale",
                                    "action": "continue"}))
    assert data["status"] == "closed"

    st = _plant_pending_state(rid)
    # 换卡（checkpoint_id 不匹配）同样幂等 closed，不误置握手
    data = unwrap(client.post(f"/api/openops/v1/agent-runs/{rid}/diagnosis-checkpoint:decide",
                              headers=USER_HEADERS,
                              json={"client_request_id": crid(), "checkpoint_id": "other",
                                    "action": "continue"}))
    assert data["status"] == "closed" and not st.checkpoint_ev.is_set()

    # 假设文本走面板同口径清洗：尖括号拒收，状态不动
    resp = client.post(f"/api/openops/v1/agent-runs/{rid}/diagnosis-checkpoint:decide",
                       headers=USER_HEADERS,
                       json={"client_request_id": crid(), "checkpoint_id": "ckpt-http-1",
                             "action": "add_hypothesis", "text": "<script>alert(1)</script>"})
    assert resp.status_code == 400
    assert not st.checkpoint_ev.is_set()

    # action 枚举外直接被 pydantic 拒（timed_out 只由服务端产生）
    resp = client.post(f"/api/openops/v1/agent-runs/{rid}/diagnosis-checkpoint:decide",
                       headers=USER_HEADERS,
                       json={"client_request_id": crid(), "checkpoint_id": "ckpt-http-1",
                             "action": "timed_out"})
    assert resp.status_code == 422


def test_decide_endpoint_enforces_run_ownership(client) -> None:
    inst = create_instance(client)
    run = create_run(client, inst["instance_id"])
    _plant_pending_state(run["agent_run_id"])
    resp = client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/diagnosis-checkpoint:decide",
                       headers={"X-OpenOps-Mock-User": "0099other", "X-OpenOps-Mock-Name": "Other"},
                       json={"client_request_id": f"crid_{time.time_ns()}",
                             "checkpoint_id": "ckpt-http-1", "action": "continue"})
    assert resp.status_code in (403, 404)  # owned_run：非 owner 不可决策


def test_decide_service_direct_matches_endpoint_contract(client) -> None:
    """service 层直调口径（与审批 decide 的测试形状对齐）：hold 也能置位。"""
    inst = create_instance(client)
    run = create_run(client, inst["instance_id"])
    st = _plant_pending_state(run["agent_run_id"])
    req = CheckpointDecisionRequest(client_request_id="crid_x", checkpoint_id="ckpt-http-1",
                                    action="hold")
    data = asyncio.run(rss.decide_diagnosis_checkpoint(
        {"user_id": "0026demo01"}, run["agent_run_id"], req))
    assert data["status"] == "accepted" and st.checkpoint_result == {"action": "hold", "text": ""}
