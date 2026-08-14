"""TaskRuntimeRegistry（进程内运行态；PG 不存 task 表，task 上下文活在 Run 内）。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class TaskState:
    task_id: str
    run_id: str
    user_id: str
    instance_id: str
    input_text: str
    status: str = "running"  # running/cancel_requested/cancelled/completed/failed
    # 任务来源池：user=交互（占 per_user_running_task_limit）/ alert=告警自动诊断（闸门在 alerts
    # dispatcher 的并发闸，不占用户交互额度）。HTTP StartTaskRequest 刻意无此字段（防伪造绕闸），
    # 仅内部派发方（alerts dispatcher）经 getattr 缝隙传入——同 skill_hint 的 _TaskReq 先例。
    origin: str = "user"
    # 沙箱容器寻址键（**单一事实源**，执行期/自愈重建/管理台反查一律走它）：
    # alert-origin=共享告警沙箱伪用户键（executor.ALERT_SANDBOX_UID）；user-origin=本人工号。
    # 空串=旧路径兜底（使用方 getattr(st,"sandbox_uid","") or st.user_id）。
    sandbox_uid: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    orchestrator: asyncio.Task[None] | None = None
    rca: dict[str, Any] | None = None  # RCA 快照（/state 恢复用）
    # 面板数据来源（A4 双向守卫）："demo"=剧本（rca_demo）/"model"=模型自报（update_diagnosis_board）。
    # model 一经接管，demo 工具不得覆写；model 首调时丢弃 demo 内容从骨架起步（revision 仍接续）。
    rca_source: str | None = None
    selected_model: str | None = None
    model_spec: dict[str, Any] | None = None  # 平台模型元数据（无 API Key）；agentscope 后端据此建真模型/回退 stub
    # 模型模板 sub 槽位（overlay.model_template_id 绑定时由 start_task 解析）；None=子 Agent 跟随主模型。
    # 子 TaskState 经 _child_state 取 `sub_model_spec or model_spec` 作为自己的 model_spec，自身不再持有本字段。
    selected_sub_model: str | None = None
    sub_model_spec: dict[str, Any] | None = None
    scope_ctx: dict[str, Any] | None = None  # ScopeContext（effective_appids/snapshot_id/revision）；Tool Gateway 校验用
    tool_annotations: dict[str, dict[str, Any]] | None = None  # 平台工具标注快照（by tool_name）；Gateway/ASK 判定用
    plan_notified: set[str] = field(default_factory=set)  # 已发过 runtime_plan.updated 的工具（每 task 每工具一次）
    tool_blocked: bool = False  # 本 task 出现过工具拦截（B6-RT-001：阻断后不得采纳模型「已恢复」结论）
    # 模板 default_tools（B7·二：RuntimePlan 只装配模板内工具）。
    # None=未经 start_task 装配（无模板信息）；set()（含空集）=模板集合上界——空模板即零平台工具（B7-SEC-001）。
    template_tools: set[str] | None = None
    # 用户自定义 MCP server（本实例已挂载、未被解绑）：[{mcp_id, display_name, endpoint}]，start_task 解析。
    # **仅 main 装配**：子 Agent 不继承（_child_state 不复制 → 恒 None），用户 MCP 只豁免 main 的模板白名单，
    # 子角色工具面仍按画像 mcp_tools 裁剪（B7 per-agent 隔离）。None/[]=无（旧快照/单测手搓）→ 按空处理。
    mcp_servers: list[dict[str, Any]] | None = None
    sandbox_cfg: dict[str, Any] | None = None  # 沙箱运行配置（容量/deny 前缀）；容器内 Bash 工具用（B8·补2）
    # Agent 可调 Skill：{skill_key: {version_no, checksum}}（平台 active + 用户绑定；C1 run_skill 作 agent 工具）
    # main task 上=已过 main.skills 模板白名单的集合（main 自身可执行面）
    available_skills: dict[str, dict[str, Any]] | None = None
    # 未过滤全集（平台 active ∪ 实例绑定）：子 Agent 画像切片源——main.skills 收窄只管 main 自己，
    # 不得掐子角色的技能池（老 D6：主从技能面各自独立）
    skills_pool: dict[str, dict[str, Any]] | None = None
    # 本轮用户显式指定优先执行的 Skill（StartTaskRequest.skill_hint，已按 available_skills 校验命中；
    # 未命中/未传=None）。run_task 据此在 system_prompt 追加确定性执行指令（发现链路：/<skill> 显式触发）。
    skill_hint: str | None = None
    # 主 Agent 人设（run_task 装配进 system_prompt）：main_role=模板 content_json.main.role；
    # main_role_append=实例 overlay_json.main_role_append。对齐子 Agent 的 sub['role']——修「主 Agent 人设被丢弃」。
    main_role: str | None = None
    main_role_append: str | None = None
    # ASK 决策握手（decide/cancel 置位；orchestrator 等待）
    approval_ev: asyncio.Event = field(default_factory=asyncio.Event)
    approval_result: str | None = None  # approved/rejected/timeout/cancelled
    approval_id: str | None = None
    # D 块：sub_agents 编排
    agent_key: str = "main"  # 本 TaskState 归属 agent（子 task=角色 key；事件 payload 携带供前端分组）
    agent_label: str = "主 Agent"  # 活动栏展示名；子 task 取模板 sub_agents[].label
    leader_task_id: str | None = None  # 子 task 所属主任务；main 为 None
    delegation_id: str | None = None  # 子 task 对应派发账本主键
    dispatch_batch_id: str | None = None  # 同一次 dispatch_subagents 调用共享
    dispatch_batch_no: int | None = None  # 主任务内递增批次号
    activity_tool_labels: dict[str, str] | None = None  # 模板 activity_labels.tools（只含展示文案）
    sub_agents: list[dict[str, Any]] | None = None  # 模板 content_json.sub_agents（仅 main 装配；子恒 None=禁二层派发）
    dispatch_cfg: dict[str, Any] | None = None  # {"max_children", "delegation_max_spawns"}（仅 main）


_by_run: dict[str, TaskState] = {}  # run_id → 当前 task（V1 每 Run 同时最多 1 个 running task 上下文）


_subtasks: dict[str, TaskState] = {}  # E1：子 Agent task（审批桥按 task_id 精确路由到子 approval_ev）


def put(state: TaskState) -> None:
    _by_run[state.run_id] = state


def register_subtask(state: TaskState) -> None:
    _subtasks[state.task_id] = state


def unregister_subtask(task_id: str) -> None:
    _subtasks.pop(task_id, None)


def get_by_run(run_id: str) -> TaskState | None:
    return _by_run.get(run_id)


def get_by_task(task_id: str) -> TaskState | None:
    for st in _by_run.values():
        if st.task_id == task_id:
            return st
    return _subtasks.get(task_id)  # E1：主表 miss 再查子表（decide_approval/cancel 路由）


def running_count(user_id: str, origin: str | None = None) -> int:
    """origin=None 数全部（sandbox_admin 展示口径不变）；origin='user' 供并发闸只数交互池。"""
    return sum(1 for st in _by_run.values()
               if st.user_id == user_id and st.status == "running"
               and (origin is None or getattr(st, "origin", "user") == origin))


def running_total(origin: str) -> int:
    """全局某池在跑主任务数（v3 分池闸：user=交互池 / alert=告警池）。"""
    return sum(1 for st in _by_run.values()
               if st.status == "running" and getattr(st, "origin", "user") == origin)


def running_by_user(user_id: str) -> list[TaskState]:
    """该用户在跑的主任务（管理员强制销毁容器时须连带取消，否则容器被自愈重建）。

    只列主任务：子 Agent 活在主 orchestrator 内，主任务 cancel 会级联（同 reset 口径）。
    """
    return [st for st in _by_run.values() if st.user_id == user_id and st.status == "running"]


def running_by_sandbox(sandbox_uid: str) -> list[TaskState]:
    """按容器寻址键反查在跑主任务（管理员销毁共享告警沙箱的连带取消用）：
    告警诊断任务 user_id=实例 owner ≠ 容器键，按 user 反查会漏——须按本维度补查。"""
    return [st for st in _by_run.values()
            if st.status == "running" and getattr(st, "sandbox_uid", "") == sandbox_uid]


def running_subtask_count(user_id: str) -> int:
    """该用户在飞的子 Agent 数（fan-out 观测；子 Agent 属任务内部，不计入 per_user_running_task_limit）。"""
    return sum(1 for st in _subtasks.values() if st.user_id == user_id and st.status == "running")


def instance_has_running(instance_id: str) -> bool:
    return any(st.instance_id == instance_id and st.status == "running" for st in _by_run.values())


def reset() -> None:  # 测试用
    _subtasks.clear()
    for st in _by_run.values():
        if st.orchestrator and not st.orchestrator.done():
            st.orchestrator.cancel()
    _by_run.clear()
