"""Run / Task / ASK / state 聚合（28.1 / 28.5 / 28.8 / 30.7）。"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from app import mcp_tool_annotation_service, model_gateway, runtime_adapter, scope_service
from domain.errors import ApiError, Err
from infra import idempotency
from infra.db import row_json
from infra.redact import redact_text, sanitize_activity_payload, sanitize_approval_arguments
from infra.repositories import agent_teams, audit, delegations, runs, runtime_config, secrets, task_states, templates
from runtime import events, task_registry
from runtime.emit import activity_context_of_task, expire_stale_approvals_and_audit
from runtime.task_registry import TaskState
from sandbox.executor import executor as sandbox_executor

log = logging.getLogger("openops.run")


def _auto_title(text: str) -> str:
    """会话自动起名：输入单行化取前 30 字（与前端本地即时显示同规则）。"""
    t = " ".join(text.split())
    return t[:30] + ("…" if len(t) > 30 else "")


def _agent_key_of_task(task_id: str, st: TaskState | None) -> str:
    """事件归属解析（DEF-6）：优先存活 TaskState；已注销时从 task_id 形状推——
    主任务无 "."；子任务 `{leader}.{key}-{hash8}`（DEF-2 后格式，旧 `{key}{seq}` 也兼容）。"""
    if st is not None:
        return st.agent_key
    if "." not in task_id:
        return "main"
    tail = task_id.split(".", 1)[1]
    tail = re.sub(r"-[0-9a-f]{8}$", "", tail)      # DEF-2 新格式去 -hash8
    return re.sub(r"\d+$", "", tail) or tail       # 旧 {key}{seq} 格式去尾 seq


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
    return [_approval_json(a) for a in await runs.pending_approvals(run_id)]


def _event_json(row: dict[str, Any]) -> dict[str, Any]:
    """审计行对外别名：event_id 与实时 envelope 共用 audit_event_id。"""
    item = row_json(row)
    item["event_id"] = item["audit_event_id"]
    item["payload_redacted_json"] = sanitize_activity_payload(
        str(item.get("event_type") or ""),
        item.get("payload_redacted_json"),
        external_request_id=item.get("external_request_id"),
    )
    return item


def _approval_json(row: dict[str, Any]) -> dict[str, Any]:
    item = row_json(row)
    item["arguments_redacted_json"] = sanitize_approval_arguments(
        item.get("arguments_redacted_json") or {}
    )
    return item


async def list_events(user: dict[str, Any], run_id: str, *, before: str | None,
                      limit: int) -> dict[str, Any]:
    """Owner 可见的活动历史游标页（最新页起，页内正序）。"""
    await owned_run(user["user_id"], run_id)
    if before is not None:
        try:
            uuid.UUID(before)
        except ValueError as exc:
            raise ApiError(Err.VALIDATION_FAILED, "事件游标格式无效") from exc
    try:
        rows, has_more = await audit.list_page_by_run(run_id, before=before, limit=limit)
    except ValueError as exc:
        raise ApiError(Err.VALIDATION_FAILED, str(exc)) from exc
    items = [_event_json(row) for row in rows]
    return {
        "items": items,
        "next_cursor": items[0]["audit_event_id"] if has_more and items else None,
        "has_more": has_more,
    }


def _delegation_json(row: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    """活动栏账本投影：只给关联/终态与脱敏摘要，不外放完整任务和汇报正文。"""
    item = row_json(row)
    leader = str(item["leader_task_id"])
    agent_key = str(item["agent_key"])
    delegation_id = str(item["delegation_id"])
    return {
        "delegation_id": delegation_id,
        "run_id": item["run_id"],
        "leader_task_id": leader,
        "child_task_id": f"{leader}.{agent_key}-{delegation_id[:8]}",
        "agent_key": agent_key,
        "agent_label": labels.get(agent_key) or agent_key,
        "dispatch_batch_id": item.get("dispatch_batch_id"),
        "dispatch_batch_no": item.get("dispatch_batch_no"),
        "delegation_status": item["delegation_status"],
        "had_final_report": item["had_final_report"],
        "deadline": item.get("deadline"),
        "creation_date": item.get("creation_date"),
        "last_update_date": item.get("last_update_date"),
        "task_summary": redact_text(row.get("task_text") or "", max_length=300),
        "report_summary": (redact_text(row["report_text"], max_length=300)
                           if row.get("report_text") else None),
    }


async def create_run(user: dict[str, Any], req: Any) -> dict[str, Any]:
    uid = user["user_id"]
    # DEF-3：先占位抢锁（并发同 key 只有一个赢家；同 key 异体 409；重放命中缓存）
    cached = await idempotency.begin(uid, "create_run", req.client_request_id,
                                     req.model_dump(exclude={"client_request_id"}))
    if cached is not None:
        return cached
    try:
        return await _create_run_body(user, req)
    except Exception:
        await idempotency.rollback(uid, "create_run", req.client_request_id)  # 失败放行重试
        raise


async def _create_run_body(user: dict[str, Any], req: Any) -> dict[str, Any]:
    uid = user["user_id"]
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
    return await idempotency.commit(uid, "create_run", req.client_request_id, result)


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
    task_message = "任务已启动"
    task_eid = await audit.insert_event(
        audit_trace_id=trace, event_type="task.started", user_id=uid, run_id=run_id,
        instance_id=st.instance_id, task_id=task_id, action="start",
        payload_redacted={"input_chars": len(req.input_text), "summary": task_message}, actor_type="user",
    )
    events.publish(run_id, events.envelope(run_id, "openops.task.started", task_id=task_id,
                                           message=task_message, audit_trace_id=trace, event_id=task_eid))
    scope_message = f"范围已解析（{len(scope['effective_appids'])} 个 APPID）"
    scope_eid = await audit.insert_event(
        audit_trace_id=trace, event_type="scope.resolved", user_id=uid, run_id=run_id,
        instance_id=st.instance_id, task_id=task_id, action="resolve",
        payload_redacted={"scope_snapshot_id": scope["scope_snapshot_id"],
                          "appid_count": len(scope["effective_appids"]), "summary": scope_message},
        external_request_id=scope["omodel_request_id"],
    )
    events.publish(run_id, events.envelope(
        run_id, "openops.scope.resolved", task_id=task_id,
        message=scope_message,
        payload={"scope_snapshot_id": scope["scope_snapshot_id"],
                 "appid_count": len(scope["effective_appids"]), "summary": scope_message},
        audit_trace_id=trace, event_id=scope_eid,
        external_request_id=scope["omodel_request_id"],
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
    active_cv = await agent_teams.get_config_version(str(inst["active_config_version_id"]))
    _overlay = (active_cv or {}).get("overlay_json") or {}  # user_llm_config_id + platform_model_id + main_role_append 同源
    if not st.selected_model:
        bound_llm = _overlay.get("user_llm_config_id")
        bound_platform = _overlay.get("platform_model_id")  # InitWizard 选中的平台模型（管理员注册且授权）
        if bound_llm:
            st.selected_model = str(bound_llm)
        elif bound_platform:
            # 平台模型 model_id 交给 resolve_runtime_model 按用户授权解析（越权/失效 → 回平台默认，fail-safe）
            st.selected_model = str(bound_platform)
    # 平台模型元数据（无 Key）挂到 TaskState；按用户授权解析（B7 ACL），agentscope 后端据此建真模型或回退 stub
    st.model_spec = await model_gateway.resolve_runtime_model(st.selected_model, uid)
    # ScopeContext + 工具标注挂到 TaskState：Tool Gateway 按此做 标注/APPID/ASK/Secret 判定（B4）
    st.scope_ctx = scope
    # 模板工具集（B7·二）：RuntimePlan 只装配模板 default_tools 内的平台工具；标注快照按此过滤
    tpl_ver = await templates.get_version(str(inst["template_version_id"]))
    _content = (tpl_ver or {}).get("content_json") or {}
    _main = _content.get("main", {})
    st.template_tools = set(_main.get("default_tools", []))
    # 主 Agent 人设：模板 main.role + 实例 overlay main_role_append → run_task 装配进 system_prompt
    # （对齐子 Agent 的 sub['role']，修「主 Agent 人设运行时被丢弃、自行发挥」）
    st.main_role = _main.get("role")
    st.main_role_append = _overlay.get("main_role_append")
    st.activity_tool_labels = dict((_content.get("activity_labels") or {}).get("tools") or {})
    # D 块：sub_agents 画像装配到 main task（子 task 恒 None=禁二层派发）；派发预算随模板
    _subs = _content.get("sub_agents") or []
    st.sub_agents = [s for s in _subs if isinstance(s, dict) and s.get("key")] or None
    st.dispatch_cfg = {"max_children": _main.get("max_children", 3),
                       "delegation_max_spawns": _main.get("delegation_max_spawns", 10)}
    anns = await mcp_tool_annotation_service.runtime_annotations()
    # E1：标注快照按 main ∪ 全部 sub 白名单过滤——子角色可绑 main 集之外的平台工具（如恢复
    # 工具只绑恢复 Agent），否则子 toolkit 构建时拿不到标注被 fail-closed 裁剪。
    # main 自身的 toolkit 边界仍只按 st.template_tools（不扩大）。
    _all_tools = set(st.template_tools or set()) | {
        t for s in _subs if isinstance(s, dict) for t in (s.get("mcp_tools") or [])}
    st.tool_annotations = {k: v for k, v in anns.items() if k in _all_tools}
    st.sandbox_cfg = cfg  # 容器内 Bash 工具的 deny 前缀/配置（B8·补2）
    # Agent 可调 Skill（C1）：平台 active + 该实例 main 绑定的用户 Skill（skill_key→版本/checksum）。
    # skills_pool=未过滤全集（子 Agent 画像切片源）；available_skills=再过 main.skills 白名单
    # （只收窄 main 自己；available-skills 端点吃同一过滤，展示=可执行）
    st.skills_pool = await resolve_available_skills(uid, str(inst["active_config_version_id"]))
    st.available_skills = filter_main_skills(st.skills_pool, _content)
    # skill_hint（/<skill> 显式触发）：显式字段优先；否则从 input_text 前导 /<token> 推导（前端 / 菜单只把
    # skill 名塞进文本、不发结构化 hint，故服务端兜底解析）。仅当命中本轮可执行集才生效（未命中/未传→忽略，防脏注入）。
    _hint = getattr(req, "skill_hint", None)
    if not _hint:
        _txt = (req.input_text or "").lstrip()
        if _txt.startswith("/") and len(_txt) > 1:
            _hint = _txt[1:].split(None, 1)[0]  # / 后首个空白分隔 token（skill_key 无空格）
    if _hint and _hint in (st.available_skills or {}):
        st.skill_hint = _hint
    # P2：初始 running 快照（task.started 审计在上方直发不走 emit，此处补单点落盘）；失败降级不阻断
    try:
        await task_states.upsert_snapshot(st, "running", trace)
    except Exception:  # noqa: BLE001
        log.warning("[OpenOps][snapshot] 初始任务快照写入失败（旧库重跑 sql/openops_v1_core.sql 补表）")
    runtime_adapter.submit_task(st, run)
    return {"task_id": task_id, "status": "running"}


def filter_main_skills(skills: dict[str, dict[str, Any]], content: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """模板 main.skills 白名单（编排对称化）：非空 list=交集过滤；空/缺省=不限（存量模板兼容）。

    注意与 main.default_tools「空=零工具」语义相反——skills 执行另有装配校验+沙箱受控，
    白名单只是可选收窄；空表不限也保证编辑器往返（未填 skills 存成 []）不清光技能面。"""
    ms = (content.get("main") or {}).get("skills")
    if not isinstance(ms, list) or not ms:
        return skills
    allow = {str(x) for x in ms}
    # 白名单只收窄平台 skill；用户个人 skill（已绑定/自动挂载、执行受沙箱管控）恒保留——否则真实模板设了
    # 平台白名单后，用户绑定的个人 skill 会被静默剔除、Agent 运行时看不到（左树却显示已装配，不同源）。
    return {k: v for k, v in skills.items() if v.get("source_type") == "user" or k in allow}


async def resolve_available_skills(uid: str, config_version_id: str) -> dict[str, dict[str, Any]]:
    """Agent 可执行的 Skill 集合：平台 active（模板层提供）∪ 本实例 main 绑定的用户 Skill。

    键统一为 **skill_key**（run_bound_skill 精确按键匹配、LLM 工具描述按键注入、composer "/" 按键插入
    ——三面必须同源同键；旧数据无 skill_key 时回退 display_name）。公有：available-skills 端点复用。"""
    from infra.repositories import assets

    out: dict[str, dict[str, Any]] = {}
    # skill_key → SKILL.md 的 description（发现链路：注入 run_platform_skill 工具描述，让 Agent 知道每个
    # skill 干什么、何时用）。list_skills(uid) 覆盖平台 + 本人 user skill 且带 manifest_json，二者的 description
    # 都从这里取；binding 明细无 manifest，故第二循环覆盖 user skill 时也从此 map 回填。
    desc_by_key: dict[str, str | None] = {}
    for s in await assets.list_skills(uid, include_platform=True):
        key = s.get("skill_key")
        if not key:
            continue
        desc_by_key[str(key)] = (s.get("manifest_json") or {}).get("description")
        if s.get("status") == "active":
            out[str(key)] = {"version_no": s.get("version_no"), "checksum": s.get("checksum_sha256"),
                             "source_type": s.get("source_type"),
                             "display_name": s.get("display_name") or str(key),
                             "description": desc_by_key.get(str(key))}
    details = await agent_teams.list_binding_details(config_version_id)
    for b in details:
        if b.get("asset_type") == "skill" and b.get("skill_status") in ("enabled", "validated", "uploaded"):
            key = b.get("skill_key") or b.get("skill_display_name")  # 键基统一：skill_key 优先，旧数据回退
            if not key:
                continue
            out[str(key)] = {"version_no": b.get("skill_version_no"), "checksum": None, "source_type": "user",
                             "display_name": b.get("skill_display_name") or str(key),
                             "description": desc_by_key.get(str(key))}  # 覆盖后仍带 description（binding 无 manifest）
    # 个人 skill「解绑」= muted 绑定行：从可执行集剔除（守卫 source_type=='user'——平台 skill 永不可被 mute）
    for b in details:
        if b.get("asset_type") == "skill" and b.get("status") == "muted":
            key = b.get("skill_key") or b.get("skill_display_name")
            if key and out.get(str(key), {}).get("source_type") == "user":
                out.pop(str(key), None)
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
    message = "平台模板已升级：本次任务起按新模板配置执行（你的角色追加与资产绑定已保留）"
    eid = await audit.insert_event(
        audit_trace_id=trace, event_type="config.version.derived", user_id=user["user_id"],
        run_id=str(run["agent_run_id"]), instance_id=instance_id, action="template_upgrade",
        payload_redacted={"from_template_version": cur_ver, "to_template_version": active_ver,
                          "config_version_id": str(out["config_version"]["config_version_id"]),
                          "summary": message},
    )
    events.publish(str(run["agent_run_id"]), events.envelope(
        str(run["agent_run_id"]), "openops.config.changed_notice",
        message=message, payload={"to_template_version": active_ver}, audit_trace_id=trace,
        event_id=eid,
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
            await audit.insert_event(  # DEF-4：改状态必须留审计（审计为事实源）
                audit_trace_id=str(snap["audit_trace_id"]), event_type="task.cancelled",
                user_id=user["user_id"], run_id=str(snap["run_id"]), task_id=task_id,
                action="cancel_orphan", actor_type="user",
            )
            return {"task_id": task_id, "status": "cancelled"}
        # DEF-4：终态行不谎报 cancelled——返回真实状态并标记 already_terminal
        return {"task_id": task_id, "status": snap["task_status"], "already_terminal": True}
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
    message = "会话已删除"
    eid = await audit.insert_event(
        audit_trace_id=str(run["audit_trace_id"]), event_type="run.deleted", user_id=uid,
        run_id=run_id, instance_id=str(run["agent_team_instance_id"]), action="delete", actor_type="user",
        payload_redacted={"summary": message},
    )
    events.publish(run_id, events.envelope(run_id, "openops.run.deleted", message=message,
                                           audit_trace_id=str(run["audit_trace_id"]), event_id=eid))
    return {"agent_run_id": run_id, "deleted": True}


async def rename_run(user: dict[str, Any], run_id: str, req: Any) -> dict[str, Any]:
    """会话重命名：trim + 截 60 字；审计 run.renamed + SSE（侧栏/多端同步）。"""
    uid = user["user_id"]
    run = await owned_run(uid, run_id)
    title = " ".join(req.title.split())[:60]
    if not title:
        raise ApiError(Err.VALIDATION_FAILED, "会话名称不能为空")
    await runs.set_run_title(run_id, title, uid)
    message = redact_text(f"会话已重命名：{title}", max_length=200)
    eid = await audit.insert_event(
        audit_trace_id=str(run["audit_trace_id"]), event_type="run.renamed", user_id=uid,
        run_id=run_id, instance_id=str(run["agent_team_instance_id"]), action="rename",
        actor_type="user", payload_redacted={"summary": message},
    )
    events.publish(run_id, events.envelope(run_id, "openops.run.renamed",
                                           message=message, payload={"summary": message},
                                           audit_trace_id=str(run["audit_trace_id"]), event_id=eid))
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
    message = "会话已关闭"
    eid = await audit.insert_event(
        audit_trace_id=str(run["audit_trace_id"]), event_type="run.closed", user_id=user["user_id"],
        run_id=run_id, instance_id=str(run["agent_team_instance_id"]), action="close", actor_type="user",
        payload_redacted={"summary": message},
    )
    events.publish(run_id, events.envelope(run_id, "openops.run.closed", message=message,
                                           audit_trace_id=str(run["audit_trace_id"]), event_id=eid))
    return {"run_id": run_id, "run_status": "closed"}


async def decide_approval(user: dict[str, Any], approval_id: str, req: Any) -> dict[str, Any]:
    appr = await runs.get_approval(approval_id)
    if appr is None:
        raise ApiError(Err.NOT_FOUND, "审批请求不存在")
    if appr["user_id"] != user["user_id"]:
        raise ApiError(Err.FORBIDDEN, "无权处理该审批")
    if appr["decision"] != "pending":
        return {"approval_request_id": approval_id, "decision": appr["decision"]}
    await expire_stale_approvals_and_audit(str(appr["agent_run_id"]))  # 连带 B：翻转即审计
    fresh = await runs.get_approval(approval_id)
    assert fresh is not None
    if fresh["decision"] == "timeout":
        return {"approval_request_id": approval_id, "decision": "timeout"}  # ASK-004

    await runs.decide_approval(approval_id, req.decision, user["user_id"])
    run_id = str(appr["agent_run_id"])
    run = await runs.get_run(run_id)
    assert run is not None
    # E1 审批桥（DEF-1 修）：只按审批行 task_id 精确路由——旧 get_by_run 兜底会在子任务终态后
    # 把迟到 decide 错置到主任务握手信号上（审批门旁路）。无人在等时决策只落库，不发握手。
    task_id = str(appr["task_id"])
    st = task_registry.get_by_task(task_id)
    if st is not None and st.task_id != task_id:  # 双保险：registry 语义漂移也不误置
        st = None
    agent_key = _agent_key_of_task(task_id, st)  # DEF-6：决策事件带归属，前端活动栏才能归对组
    ctx = {**(await activity_context_of_task(task_id)), "agent_key": agent_key}
    message = "已批准，恢复动作将执行" if req.decision == "approved" else "已拒绝，当前工具调用终止"
    payload = {"approval_request_id": approval_id, "reason_chars": len(req.reason or ""),
               **ctx, "summary": message}
    eid = await audit.insert_event(
        audit_trace_id=str(run["audit_trace_id"]),
        event_type=f"approval.{req.decision}", user_id=user["user_id"], run_id=run_id,
        instance_id=str(appr["agent_team_instance_id"]), task_id=appr["task_id"],
        action="decide", decision=req.decision, actor_type="user",
        payload_redacted=payload,
    )
    events.publish(run_id, events.envelope(
        run_id, f"openops.approval.{req.decision}", task_id=appr["task_id"],
        message=message, payload=payload, audit_trace_id=str(run["audit_trace_id"]), event_id=eid,
    ))
    if st is not None:
        st.approval_result = req.decision
        st.approval_ev.set()
    return {"approval_request_id": approval_id, "decision": req.decision}


async def get_state(user: dict[str, Any], run_id: str) -> dict[str, Any]:
    """聚合状态（30.7 恢复事实入口）。"""
    run = await owned_run(user["user_id"], run_id)
    await expire_stale_approvals_and_audit(run_id)
    inst = await agent_teams.get_instance(str(run["agent_team_instance_id"]))
    st = task_registry.get_by_run(run_id)
    pend = await runs.pending_approvals(run_id)
    recent, events_has_more = await audit.list_page_by_run(run_id, limit=100)
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
    recent_items = [_event_json(e) for e in recent]
    delegation_rows = await delegations.list_by_run(run_id, limit=100)
    agent_labels = {
        str(sub["key"]): str(sub.get("label") or sub["key"])
        for sub in ((st.sub_agents if st is not None else None) or [])
        if isinstance(sub, dict) and sub.get("key")
    }
    # 重启后内存画像为空时，从最近的持久化事件恢复角色展示名；账本只存稳定 key。
    for event in recent_items:
        payload = event.get("payload_redacted_json") or {}
        key = payload.get("agent_key") if isinstance(payload, dict) else None
        label = payload.get("agent_label") if isinstance(payload, dict) else None
        if key and label and str(label) != str(key):
            agent_labels.setdefault(str(key), str(label))
    return {
        "run": row_json(run),
        "instance": row_json(inst) if inst else None,
        "active_task": active_task,
        "rca": rca,
        "pending_approvals": [_approval_json(a) for a in pend],
        "recent_events": recent_items,
        "events_next_cursor": (recent_items[0]["audit_event_id"]
                               if events_has_more and recent_items else None),
        "events_has_more": events_has_more,
        "delegations": [_delegation_json(d, agent_labels) for d in delegation_rows],
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
    message = f"模型已切换（会话级）：{label}"
    eid = await audit.insert_event(
        audit_trace_id=str(run["audit_trace_id"]), event_type="model.selected",
        user_id=user["user_id"], run_id=run_id, action="select_model",
        payload_redacted={"model": label, "summary": message}, actor_type="user",
    )
    events.publish(run_id, events.envelope(run_id, "openops.model.selected", message=message,
                                           audit_trace_id=str(run["audit_trace_id"]), event_id=eid))
    return {"run_id": run_id, "selected_model": label}
