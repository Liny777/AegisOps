"""Run / Task / ASK / state 聚合（28.1 / 28.5 / 28.8 / 30.7）。"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from app import mcp_tool_annotation_service, model_gateway, runtime_adapter, scope_service
from domain.errors import ApiError, Err
from infra import idempotency
from infra.db import row_json
from infra.repositories import agent_teams, audit, runs, runtime_config, secrets, task_states, templates
from runtime import events, task_registry
from runtime.task_registry import TaskState
from sandbox.executor import executor as sandbox_executor

log = logging.getLogger("openops.run")


def _auto_title(text: str) -> str:
    """会话自动起名：输入单行化取前 30 字（与前端本地即时显示同规则）。"""
    t = " ".join(text.split())
    return t[:30] + ("…" if len(t) > 30 else "")


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
    cached = await idempotency.get(uid, "create_run", req.client_request_id)
    if cached is not None:
        return cached
    inst = await agent_teams.get_instance(req.agent_team_instance_id)
    if inst is None or inst["owner_user_id"] != uid:
        raise ApiError(Err.FORBIDDEN, "无权在该 AgentTeam 上创建 Run")  # RUN-002
    if inst["status"] != "active":
        raise ApiError(Err.CONFIG_VERSION_INVALID, "实例不可用（disabled）")
    # 会话期常驻（B8）：run 开启边界做沙箱容量准入并确保用户容器（在写 run 前——满则 fail-closed 不建空 run）
    cfg = {c["config_key"]: c["config_value_json"] for c in await runtime_config.get_domain("sandbox")}
    run_id = str(uuid.uuid4())
    await sandbox_executor.ensure_user_container(uid, run_id, cfg)  # SANDBOX_CAPACITY_FULL / SANDBOX_CONTAINER_FAILED
    run = await runs.create_run(
        uid, req.agent_team_instance_id, str(inst["active_config_version_id"]),
        "agentscope-session-" + uuid.uuid4().hex[:8], str(uuid.uuid4()), run_id=run_id,
    )
    await audit.insert_event(
        audit_trace_id=str(run["audit_trace_id"]), event_type="agent_run.created", user_id=uid,
        run_id=str(run["agent_run_id"]), instance_id=req.agent_team_instance_id, action="create", actor_type="user",
    )
    await audit.insert_event(
        audit_trace_id=str(run["audit_trace_id"]), event_type="sandbox.container.ready", user_id=uid,
        run_id=str(run["agent_run_id"]), instance_id=req.agent_team_instance_id, action="ensure_container",
        actor_type="system",
    )
    result = {"run": row_json(run)}
    return await idempotency.put(uid, "create_run", req.client_request_id, result)


async def start_task(user: dict[str, Any], run_id: str, req: Any) -> dict[str, Any]:
    uid = user["user_id"]
    run = await owned_run(uid, run_id)
    if run["run_status"] == "closed":
        raise ApiError(Err.RUN_ALREADY_CLOSED, "Run 已关闭，不能启动新任务")  # RUN-005 / CANCEL-006
    # 并发上限（RUN-004）：读平台运行配置
    cfg = {c["config_key"]: c["config_value_json"] for c in await runtime_config.get_domain("sandbox")}
    limit = int(cfg.get("per_user_running_task_limit", 2))
    running = task_registry.running_count(uid)
    try:  # P2：与快照取 max（重启后内存归零时防瞬时超发；旧库未迁移降级内存值）
        running = max(running, await task_states.count_running(uid))
    except Exception:  # noqa: BLE001
        pass
    if running >= limit:
        raise ApiError(Err.USER_TASK_CONCURRENCY_LIMIT, f"并发任务数已达上限（{limit}）")

    inst = await agent_teams.get_instance(str(run["agent_team_instance_id"]))
    assert inst is not None
    task_id = "tsk_" + uuid.uuid4().hex[:10]
    inst = await _derive_if_template_upgraded(user, run, inst)  # 28.7：模板升级 → 边界自动派生（保留 overlay/绑定）

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

    # 会话自动起名：run 首个任务的输入作 run_title（用户可随时改名覆盖）。失败不阻断任务
    # （旧库未跑 sql/migrate-2026-07-12-run-title.sql 时列缺失——改名入口会显式报错提示）。
    if not run.get("run_title"):
        try:
            await runs.set_run_title(run_id, _auto_title(req.input_text), uid)
        except Exception:  # noqa: BLE001
            log.warning("[OpenOps][run] 自动起名失败（run_title 列缺失？见 sql/migrate-2026-07-12-run-title.sql）")

    # 实例默认模型：active 配置 overlay 绑定的用户自定义 LLM 作为该实例默认（InitWizard custom 分支 / 30.5）。
    # 会话级 select-model 在本 task 内直接改 st.selected_model 覆盖；无绑定则 None→平台默认（B7 ACL 解析）。
    if not st.selected_model:
        active_cv = await agent_teams.get_config_version(str(inst["active_config_version_id"]))
        bound_llm = ((active_cv or {}).get("overlay_json") or {}).get("user_llm_config_id")
        if bound_llm:
            st.selected_model = str(bound_llm)
    # 平台模型元数据（无 Key）挂到 TaskState；按用户授权解析（B7 ACL），agentscope 后端据此建真模型或回退 stub
    st.model_spec = await model_gateway.resolve_runtime_model(st.selected_model, uid)
    # ScopeContext + 工具标注挂到 TaskState：Tool Gateway 按此做 标注/APPID/ASK/Secret 判定（B4）
    st.scope_ctx = scope
    # 模板工具集（B7·二）：RuntimePlan 只装配模板 default_tools 内的平台工具；标注快照按此过滤
    tpl_ver = await templates.get_version(str(inst["template_version_id"]))
    st.template_tools = set(((tpl_ver or {}).get("content_json") or {}).get("main", {}).get("default_tools", []))
    anns = await mcp_tool_annotation_service.runtime_annotations()
    st.tool_annotations = {k: v for k, v in anns.items() if k in st.template_tools}
    st.sandbox_cfg = cfg  # 容器内 Bash 工具的 deny 前缀/配置（B8·补2）
    # Agent 可调 Skill（C1）：平台 active + 该实例 main 绑定的用户 Skill（skill_key→版本/checksum）
    st.available_skills = await resolve_available_skills(uid, str(inst["active_config_version_id"]))
    # P2：初始 running 快照（task.started 审计在上方直发不走 emit，此处补单点落盘）；失败降级不阻断
    try:
        await task_states.upsert_snapshot(st, "running", trace)
    except Exception:  # noqa: BLE001
        log.warning("[OpenOps][snapshot] 初始任务快照写入失败（见 sql/migrate-2026-07-12-persistence.sql）")
    runtime_adapter.submit_task(st, run)
    return {"task_id": task_id, "status": "running"}


async def resolve_available_skills(uid: str, config_version_id: str) -> dict[str, dict[str, Any]]:
    """Agent 可执行的 Skill 集合：平台 active（模板层提供）∪ 本实例 main 绑定的用户 Skill。

    键统一为 **skill_key**（run_bound_skill 精确按键匹配、LLM 工具描述按键注入、composer "/" 按键插入
    ——三面必须同源同键；旧数据无 skill_key 时回退 display_name）。公有：available-skills 端点复用。"""
    from infra.repositories import assets

    out: dict[str, dict[str, Any]] = {}
    for s in await assets.list_skills(uid, include_platform=True):
        if s.get("status") == "active" and s.get("skill_key"):
            out[str(s["skill_key"])] = {"version_no": s.get("version_no"), "checksum": s.get("checksum_sha256"),
                                        "source_type": s.get("source_type"),
                                        "display_name": s.get("display_name") or str(s["skill_key"])}
    for b in await agent_teams.list_binding_details(config_version_id):
        if b.get("asset_type") == "skill" and b.get("skill_status") in ("enabled", "validated", "uploaded"):
            key = b.get("skill_key") or b.get("skill_display_name")  # 键基统一：skill_key 优先，旧数据回退
            if not key:
                continue
            out[str(key)] = {"version_no": b.get("skill_version_no"), "checksum": None, "source_type": "user",
                             "display_name": b.get("skill_display_name") or str(key)}
    return out


async def _derive_if_template_upgraded(user: dict[str, Any], run: dict[str, Any], inst: dict[str, Any]) -> dict[str, Any]:
    """28.7 使用时派生：平台模板发布新版本后，实例在下一次任务边界自动派生新配置版本。

    保留用户 main overlay 与用户资产绑定（derive_config_version 结转），回写实例模板版本指针；
    写审计 config.version.derived + 推送 openops.config.changed_notice（30.4）。
    """
    from app import agent_team_service  # 局部导入避免环

    tpl = await templates.get_template(str(inst["template_id"]))
    active_ver = str(tpl["active_template_version_id"]) if tpl and tpl["active_template_version_id"] else None
    cur_ver = str(inst["template_version_id"])
    if not active_ver or active_ver == cur_ver:
        return inst
    instance_id = str(inst["agent_team_instance_id"])
    out = await agent_team_service.derive_config_version(
        inst, user["user_id"], "template upgraded", template_version_id=active_ver,
    )
    await agent_teams.update_template_version(instance_id, active_ver, user["user_id"])
    trace = str(run["audit_trace_id"])
    await audit.insert_event(
        audit_trace_id=trace, event_type="config.version.derived", user_id=user["user_id"],
        run_id=str(run["agent_run_id"]), instance_id=instance_id, action="template_upgrade",
        payload_redacted={"from_template_version": cur_ver, "to_template_version": active_ver,
                          "config_version_id": str(out["config_version"]["config_version_id"])},
    )
    events.publish(str(run["agent_run_id"]), events.envelope(
        str(run["agent_run_id"]), "openops.config.changed_notice",
        message="平台模板已升级：本次任务起按新模板配置执行（你的角色追加与资产绑定已保留）",
        payload={"to_template_version": active_ver}, audit_trace_id=trace,
    ))
    refreshed = await agent_teams.get_instance(instance_id)
    assert refreshed is not None
    return refreshed


async def cancel_task(user: dict[str, Any], task_id: str) -> dict[str, Any]:
    st = task_registry.get_by_task(task_id)
    if st is None:
        # P2 收敛：内存 miss（进程重启过）→ 按快照收口而非 404，用户可把孤儿任务显式关掉
        snap = await task_states.get_by_task(task_id)
        if snap is None:
            raise ApiError(Err.NOT_FOUND, "任务不存在或已结束")
        if snap["user_id"] != user["user_id"]:
            raise ApiError(Err.FORBIDDEN, "无权取消该任务")
        if snap["task_status"] in ("running", "interrupted"):
            await task_states.mark_status(task_id, "cancelled", user["user_id"])
        return {"task_id": task_id, "status": "cancelled"}
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


async def delete_run(user: dict[str, Any], run_id: str) -> dict[str, Any]:
    """会话删除（软删）：running task 先取消、释放容器（同 close_run 收尾），再打 deleted_at + 审计。"""
    uid = user["user_id"]
    run = await owned_run(uid, run_id)
    st = task_registry.get_by_run(run_id)
    if st and st.status == "running":
        await cancel_task(user, st.task_id)
    if run["run_status"] != "closed":  # 已 close 的 run 容器早已释放，勿重复 release
        cfg = {c["config_key"]: c["config_value_json"] for c in await runtime_config.get_domain("sandbox")}
        await sandbox_executor.release_user_container(uid, run_id)
        await sandbox_executor.sweep_idle(cfg)
    await runs.soft_delete_run(run_id, uid)
    await audit.insert_event(
        audit_trace_id=str(run["audit_trace_id"]), event_type="run.deleted", user_id=uid,
        run_id=run_id, instance_id=str(run["agent_team_instance_id"]), action="delete", actor_type="user",
    )
    events.publish(run_id, events.envelope(run_id, "openops.run.deleted", message="会话已删除"))
    return {"agent_run_id": run_id, "deleted": True}


async def rename_run(user: dict[str, Any], run_id: str, req: Any) -> dict[str, Any]:
    """会话重命名：trim + 截 60 字；审计 run.renamed + SSE（侧栏/多端同步）。"""
    uid = user["user_id"]
    run = await owned_run(uid, run_id)
    title = " ".join(req.title.split())[:60]
    if not title:
        raise ApiError(Err.VALIDATION_FAILED, "会话名称不能为空")
    await runs.set_run_title(run_id, title, uid)
    await audit.insert_event(
        audit_trace_id=str(run["audit_trace_id"]), event_type="run.renamed", user_id=uid,
        run_id=run_id, instance_id=str(run["agent_team_instance_id"]), action="rename",
        actor_type="user", payload_redacted={"title": title},
    )
    events.publish(run_id, events.envelope(run_id, "openops.run.renamed",
                                           message=f"会话已重命名：{title}", payload={"title": title}))
    return {"agent_run_id": run_id, "run_title": title}


async def close_run(user: dict[str, Any], run_id: str) -> dict[str, Any]:
    run = await owned_run(user["user_id"], run_id)
    st = task_registry.get_by_run(run_id)
    if st and st.status == "running":
        await cancel_task(user, st.task_id)  # CANCEL-005：先取消 running task
    await runs.set_run_status(run_id, "closed")
    # 会话期常驻（B8）：末个活跃 run 关闭后容器置 idle 交 TTL 回收
    cfg = {c["config_key"]: c["config_value_json"] for c in await runtime_config.get_domain("sandbox")}
    await sandbox_executor.release_user_container(user["user_id"], run_id)
    await sandbox_executor.sweep_idle(cfg)
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
    active_task: dict[str, Any] | None = None
    rca: Any = None
    if st is not None:
        active_task = {"task_id": st.task_id, "status": st.status, "input_text": st.input_text,
                       "started_at": st.started_at, "selected_model": st.selected_model}
        rca = st.rca
    else:
        # P2 回退：内存 miss（进程重启过）读影子快照——恢复面能看到最后任务与 RCA，
        # interrupted 态由启动收敛写入，前端据此停「运行中」转圈
        try:
            snap = await task_states.get_latest_by_run(run_id)
        except Exception:  # noqa: BLE001 —— 旧库未迁移
            snap = None
        if snap is not None:
            active_task = {"task_id": snap["task_id"], "status": snap["task_status"],
                           "input_text": snap["input_text"], "started_at": snap["started_at"],
                           "selected_model": snap["selected_model"]}
            rca = snap["rca_json"]
    return {
        "run": row_json(run),
        "instance": row_json(inst) if inst else None,
        "active_task": active_task,
        "rca": rca,
        "pending_approvals": [row_json(a) for a in pend],
        "recent_events": [row_json(e) for e in recent],
        "last_event_seq": events.snapshot(run_id)[-1]["sequence"] if events.snapshot(run_id) else 0,
    }


async def converge_orphan_tasks() -> int:
    """P2 启动收敛：上个进程遗留的 running 快照 → interrupted + 审计（不做协程恢复）。

    正常终态在 emit 边界已落快照，这里剩下的 running 行都是重启孤儿。收敛后 get_state
    回退展示 interrupted、cancel 可显式关掉、并发计数不再被幽灵任务占用。
    """
    try:
        orphans = await task_states.list_running()
    except Exception:  # noqa: BLE001 —— 旧库未迁移，静默跳过
        return 0
    for row in orphans:
        await task_states.mark_status(str(row["task_id"]), "interrupted", "system")
        try:
            await audit.insert_event(
                audit_trace_id=str(row["audit_trace_id"] or uuid.uuid4()), event_type="task.interrupted",
                user_id=str(row["user_id"]), run_id=str(row["run_id"]), instance_id=str(row["instance_id"]),
                task_id=str(row["task_id"]), action="converge", actor_type="system",
                payload_redacted={"reason": "backend_restart"},
            )
        except Exception:  # noqa: BLE001 —— 审计失败不阻断启动
            log.warning("[OpenOps][snapshot] 孤儿任务审计写入失败 task=%s", row["task_id"])
    if orphans:
        log.warning("[OpenOps][snapshot] 启动收敛 %d 个重启孤儿任务 → interrupted", len(orphans))
    return len(orphans)


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
    # 用户自定义 LLM（C2：修静默回退——选中前校验归属+可用，避免选无效配置后跑成平台模型）
    if req.llm_config_id:
        cfg = await secrets.get_llm_config(req.llm_config_id)
        if cfg is None or cfg["user_id"] != user["user_id"] or cfg.get("status") != "active":
            raise ApiError(Err.SECRET_REQUIRED, "所选自定义 LLM 不可用（不存在/未激活/非本人），请重新选择")
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
