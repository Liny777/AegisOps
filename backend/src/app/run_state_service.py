"""Run / Task / ASK / state 聚合（28.1 / 28.5 / 28.8 / 30.7）。"""
from __future__ import annotations

import uuid
from typing import Any

from app import mcp_tool_annotation_service, model_gateway, runtime_adapter, scope_service
from domain.errors import ApiError, Err
from infra import idempotency
from infra.db import row_json
from infra.repositories import agent_teams, audit, runs, runtime_config
from runtime import events, task_registry
from runtime.task_registry import TaskState


async def owned_run(user_id: str, run_id: str) -> dict[str, Any]:
    run = await runs.get_run(run_id)
    if run is None:
        raise ApiError(Err.NOT_FOUND, "Run 不存在")
    if run["user_id"] != user_id:
        raise ApiError(Err.FORBIDDEN, "无权访问该 Run")  # IAM-005
    return run


async def list_pending(user: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    """当前 Run 的 pending 审批（owner 校验后返回，router 不直连 repo）。"""
    await owned_run(user["user_id"], run_id)
    return [row_json(a) for a in await runs.pending_approvals(run_id)]


async def create_run(user: dict[str, Any], req: Any) -> dict[str, Any]:
    uid = user["user_id"]
    cached = idempotency.get(uid, "create_run", req.client_request_id)
    if cached is not None:
        return cached
    inst = await agent_teams.get_instance(req.agent_team_instance_id)
    if inst is None or inst["owner_user_id"] != uid:
        raise ApiError(Err.FORBIDDEN, "无权在该 AgentTeam 上创建 Run")  # RUN-002
    if inst["status"] != "active":
        raise ApiError(Err.CONFIG_VERSION_INVALID, "实例不可用（disabled）")
    run = await runs.create_run(
        uid, req.agent_team_instance_id, str(inst["active_config_version_id"]),
        "agentscope-session-" + uuid.uuid4().hex[:8], str(uuid.uuid4()),
    )
    await audit.insert_event(
        audit_trace_id=str(run["audit_trace_id"]), event_type="agent_run.created", user_id=uid,
        run_id=str(run["agent_run_id"]), instance_id=req.agent_team_instance_id, action="create", actor_type="user",
    )
    result = {"run": row_json(run)}
    return idempotency.put(uid, "create_run", req.client_request_id, result)


async def start_task(user: dict[str, Any], run_id: str, req: Any) -> dict[str, Any]:
    uid = user["user_id"]
    run = await owned_run(uid, run_id)
    if run["run_status"] == "closed":
        raise ApiError(Err.RUN_ALREADY_CLOSED, "Run 已关闭，不能启动新任务")  # RUN-005 / CANCEL-006
    # 并发上限（RUN-004）：读平台运行配置
    cfg = {c["config_key"]: c["config_value_json"] for c in await runtime_config.get_domain("sandbox")}
    limit = int(cfg.get("per_user_running_task_limit", 2))
    if task_registry.running_count(uid) >= limit:
        raise ApiError(Err.USER_TASK_CONCURRENCY_LIMIT, f"并发任务数已达上限（{limit}）")

    inst = await agent_teams.get_instance(str(run["agent_team_instance_id"]))
    assert inst is not None
    task_id = "tsk_" + uuid.uuid4().hex[:10]

    # Scope resolve（RUN-003：先范围后任务，fail-closed 由 scope_service 抛错）
    scope = await scope_service.resolve_for_task(uid, inst, run_id, task_id, str(run["audit_trace_id"]))

    st = TaskState(task_id=task_id, run_id=run_id, user_id=uid,
                   instance_id=str(run["agent_team_instance_id"]), input_text=req.input_text)
    # task.started + scope.resolved 事件（审计+SSE）
    trace = str(run["audit_trace_id"])
    await audit.insert_event(
        audit_trace_id=trace, event_type="task.started", user_id=uid, run_id=run_id,
        instance_id=st.instance_id, task_id=task_id, action="start",
        payload_redacted={"input_chars": len(req.input_text)}, actor_type="user",
    )
    events.publish(run_id, events.envelope(run_id, "openops.task.started", task_id=task_id,
                                           message="任务已启动", audit_trace_id=trace))
    await audit.insert_event(
        audit_trace_id=trace, event_type="scope.resolved", user_id=uid, run_id=run_id,
        instance_id=st.instance_id, task_id=task_id, action="resolve",
        payload_redacted={"scope_snapshot_id": scope["scope_snapshot_id"], "appid_count": len(scope["effective_appids"])},
        external_request_id=scope["omodel_request_id"],
    )
    events.publish(run_id, events.envelope(
        run_id, "openops.scope.resolved", task_id=task_id,
        message=f"范围已解析（{len(scope['effective_appids'])} 个 APPID）",
        payload={"effective_appids": scope["effective_appids"], "scope_snapshot_id": scope["scope_snapshot_id"]},
        audit_trace_id=trace,
    ))

    # 平台模型元数据（无 Key）挂到 TaskState；按用户授权解析（B7 ACL），agentscope 后端据此建真模型或回退 stub
    st.model_spec = await model_gateway.resolve_runtime_model(st.selected_model, uid)
    # ScopeContext + 工具标注挂到 TaskState：Tool Gateway 按此做 标注/APPID/ASK/Secret 判定（B4；runtime 不回读 DB）
    st.scope_ctx = scope
    st.tool_annotations = await mcp_tool_annotation_service.runtime_annotations()
    runtime_adapter.submit_task(st, run)
    return {"task_id": task_id, "status": "running"}


async def cancel_task(user: dict[str, Any], task_id: str) -> dict[str, Any]:
    st = task_registry.get_by_task(task_id)
    if st is None:
        raise ApiError(Err.NOT_FOUND, "任务不存在或已结束")
    if st.user_id != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权取消该任务")
    if st.status == "running":
        st.status = "cancelled"
        # pending ASK 收口为 cancelled（CANCEL-002）
        if st.approval_id:
            await runs.decide_approval(st.approval_id, "cancelled", user["user_id"])
        st.approval_result = st.approval_result or "cancelled"
        st.approval_ev.set()
        if st.orchestrator and not st.orchestrator.done():
            st.orchestrator.cancel()
    return {"task_id": task_id, "status": "cancelled"}


async def close_run(user: dict[str, Any], run_id: str) -> dict[str, Any]:
    run = await owned_run(user["user_id"], run_id)
    st = task_registry.get_by_run(run_id)
    if st and st.status == "running":
        await cancel_task(user, st.task_id)  # CANCEL-005：先取消 running task
    await runs.set_run_status(run_id, "closed")
    await audit.insert_event(
        audit_trace_id=str(run["audit_trace_id"]), event_type="run.closed", user_id=user["user_id"],
        run_id=run_id, instance_id=str(run["agent_team_instance_id"]), action="close", actor_type="user",
    )
    events.publish(run_id, events.envelope(run_id, "openops.run.closed", message="会话已关闭"))
    return {"run_id": run_id, "run_status": "closed"}


async def decide_approval(user: dict[str, Any], approval_id: str, req: Any) -> dict[str, Any]:
    appr = await runs.get_approval(approval_id)
    if appr is None:
        raise ApiError(Err.NOT_FOUND, "审批请求不存在")
    if appr["user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权处理该审批")
    if appr["decision"] != "pending":
        return {"approval_request_id": approval_id, "decision": appr["decision"]}
    await runs.expire_stale_approvals(str(appr["agent_run_id"]))
    fresh = await runs.get_approval(approval_id)
    assert fresh is not None
    if fresh["decision"] == "timeout":
        return {"approval_request_id": approval_id, "decision": "timeout"}  # ASK-004

    await runs.decide_approval(approval_id, req.decision, user["user_id"])
    run_id = str(appr["agent_run_id"])
    run = await runs.get_run(run_id)
    assert run is not None
    await audit.insert_event(
        audit_trace_id=str(run["audit_trace_id"]),
        event_type=f"approval.{req.decision}", user_id=user["user_id"], run_id=run_id,
        instance_id=str(appr["agent_team_instance_id"]), task_id=appr["task_id"],
        action="decide", decision=req.decision, actor_type="user",
        payload_redacted={"approval_request_id": approval_id, "reason_chars": len(req.reason or "")},
    )
    events.publish(run_id, events.envelope(
        run_id, f"openops.approval.{req.decision}", task_id=appr["task_id"],
        message="已批准，恢复动作将执行" if req.decision == "approved" else "已拒绝，当前工具调用终止",
        payload={"approval_request_id": approval_id},
    ))
    st = task_registry.get_by_run(run_id)
    if st:
        st.approval_result = req.decision
        st.approval_ev.set()
    return {"approval_request_id": approval_id, "decision": req.decision}


async def get_state(user: dict[str, Any], run_id: str) -> dict[str, Any]:
    """聚合状态（30.7 恢复事实入口）。"""
    run = await owned_run(user["user_id"], run_id)
    await runs.expire_stale_approvals(run_id)
    inst = await agent_teams.get_instance(str(run["agent_team_instance_id"]))
    st = task_registry.get_by_run(run_id)
    pend = await runs.pending_approvals(run_id)
    recent = await audit.list_by_run(run_id, limit=100)
    return {
        "run": row_json(run),
        "instance": row_json(inst) if inst else None,
        "active_task": {
            "task_id": st.task_id, "status": st.status, "input_text": st.input_text,
            "started_at": st.started_at, "selected_model": st.selected_model,
        } if st else None,
        "rca": st.rca if st else None,
        "pending_approvals": [row_json(a) for a in pend],
        "recent_events": [row_json(e) for e in recent],
        "last_event_seq": events.snapshot(run_id)[-1]["sequence"] if events.snapshot(run_id) else 0,
    }


async def list_runs(user: dict[str, Any]) -> list[dict[str, Any]]:
    return [row_json(r) for r in await runs.list_runs_by_user(user["user_id"])]


async def select_model(user: dict[str, Any], run_id: str, req: Any) -> dict[str, Any]:
    """会话级临时模型选择（MODEL-005：不生成配置版本）。平台模型须经授权（B7 模型 ACL）。"""
    from app import model_asset_service  # 局部导入避免环

    run = await owned_run(user["user_id"], run_id)
    st = task_registry.get_by_run(run_id)
    # 平台模型（非用户自有 llm_config）：白名单外 fail-closed（MODEL-ACL-002）
    if not req.llm_config_id and req.model_source:
        if not await model_asset_service.is_authorized(user["user_id"], req.model_source):
            raise ApiError(Err.MODEL_NOT_AUTHORIZED, "该模型未对你授权，请联系管理员申请白名单")
    label = req.llm_config_id or req.model_source
    if st:
        st.selected_model = label
    events.publish(run_id, events.envelope(run_id, "openops.model.selected",
                                           message=f"模型已切换（会话级）：{label}"))
    await audit.insert_event(
        audit_trace_id=str(run["audit_trace_id"]), event_type="model.selected",
        user_id=user["user_id"], run_id=run_id, action="select_model",
        payload_redacted={"model": label}, actor_type="user",
    )
    return {"run_id": run_id, "selected_model": label}
