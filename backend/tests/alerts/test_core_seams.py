"""core 接缝守卫：并发分池与共享沙箱的注入面/计数口径/寻址维度（行为级验证在 test_dispatch_e2e）。"""
from __future__ import annotations

from domain.schemas import CreateRunRequest, StartTaskRequest
from infra.repositories.task_states import _params
from runtime import task_registry
from runtime.task_registry import TaskState
from sandbox.executor import ALERT_SANDBOX_UID


def test_http_start_task_request_has_no_origin_field():
    """铁律：HTTP DTO 不得出现 origin——外部请求恒落 user 池，防伪造绕开并发闸。

    若未来有人把 origin 挪进公开 DTO，这条会响；内部派发只允许走 dataclass 的 getattr 缝隙
    （alerts dispatcher 的 _TaskReq，同 skill_hint 先例）。
    """
    assert "origin" not in StartTaskRequest.model_fields


def test_http_create_run_request_has_no_sandbox_owner_field():
    """铁律：HTTP CreateRunRequest 不得出现 sandbox_owner——防伪造把交互 run 挂进共享告警沙箱。
    内部派发走 dispatcher._AlertCreateRunReq（独立子类，不进公开 DTO）。"""
    assert "sandbox_owner" not in CreateRunRequest.model_fields


def test_running_by_sandbox_reverse_lookup():
    """销毁反查维度（共享沙箱难点③）：告警任务 user_id=owner ≠ 容器键，按 sandbox_uid 可查到。"""
    task_registry.reset()
    try:
        alert_st = TaskState(task_id="tsk_sb1", run_id="rsb1", user_id="owner-a",
                             instance_id="i1", input_text="x", origin="alert",
                             sandbox_uid=ALERT_SANDBOX_UID)
        user_st = TaskState(task_id="tsk_sb2", run_id="rsb2", user_id="owner-a",
                            instance_id="i1", input_text="y", sandbox_uid="owner-a")
        task_registry.put(alert_st)
        task_registry.put(user_st)
        assert [s.task_id for s in task_registry.running_by_sandbox(ALERT_SANDBOX_UID)] == ["tsk_sb1"]
        assert task_registry.running_by_user("owner-a") == [alert_st, user_st] or \
               set(s.task_id for s in task_registry.running_by_user("owner-a")) == {"tsk_sb1", "tsk_sb2"}
    finally:
        task_registry.reset()


def test_task_state_default_sandbox_uid_empty():
    st = TaskState(task_id="t", run_id="r", user_id="u", instance_id="i", input_text="x")
    assert st.sandbox_uid == ""  # 空=旧路径兜底（使用方 or st.user_id）


def test_registry_running_count_splits_pools():
    task_registry.reset()
    try:
        user_st = TaskState(task_id="tsk_u1", run_id="r1", user_id="u1",
                            instance_id="i1", input_text="x")
        alert_st = TaskState(task_id="tsk_a1", run_id="r2", user_id="u1",
                             instance_id="i1", input_text="y", origin="alert")
        task_registry.put(user_st)
        task_registry.put(alert_st)
        assert task_registry.running_count("u1") == 2          # 无参 = 全部（sandbox_admin 展示口径）
        assert task_registry.running_count("u1", origin="user") == 1   # 并发闸口径
        assert task_registry.running_count("u1", origin="alert") == 1
    finally:
        task_registry.reset()


def test_task_state_default_origin_is_user():
    st = TaskState(task_id="t", run_id="r", user_id="u", instance_id="i", input_text="x")
    assert st.origin == "user"


def test_snapshot_params_carry_origin_with_getattr_fallback():
    st = TaskState(task_id="t", run_id="r", user_id="u", instance_id="i", input_text="x",
                   origin="alert")
    assert _params(st, "running", "trace")["o"] == "alert"

    class _Legacy:  # 手搓 st 的旧单测形态：无 origin 字段 → 回退 user
        task_id = "t2"
        run_id = "r2"
        user_id = "u"
        instance_id = "i"
        input_text = ""
        rca = None
        selected_model = None
        selected_sub_model = None  # main 模型模板化新列（_params 非 getattr 访问，假对象须带）
        scope_ctx = None
        approval_id = None
        started_at = "2026-07-30T00:00:00+00:00"

    assert _params(_Legacy(), "running", "trace")["o"] == "user"
