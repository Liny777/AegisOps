from __future__ import annotations

import time

from conftest import USER_HEADERS, create_instance, create_run, unwrap, wait_until


def start_task(client, run_id: str, text: str = "巡检 APP-A"):
    return unwrap(
        client.post(
            f"/api/openops/v1/agent-runs/{run_id}/tasks",
            headers=USER_HEADERS,
            json={"client_request_id": f"task_{time.time_ns()}", "input_text": text},
        )
    )


def test_run_001_003_task_start_resolves_scope_and_waits_for_ask(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    task = start_task(client, run["agent_run_id"])
    assert task["status"] == "running"

    approval = wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/approvals", headers=USER_HEADERS)),
    )
    assert approval[0]["tool_call_name"] == "recover_execute"

    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state", headers=USER_HEADERS))
    assert state["active_task"]["status"] == "running"
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    event_types = {e["event_type"] for e in events}
    assert "task.started" in event_types
    assert "scope.resolved" in event_types
    assert "openops.approval.required" in event_types


def test_run_002_cannot_create_run_on_other_owner_instance(client):
    instance = create_instance(client)
    forbidden = client.post(
        "/api/openops/v1/agent-runs",
        headers={"X-OpenOps-Mock-User": "admin", "X-OpenOps-Mock-Name": "Admin"},
        json={"client_request_id": "other_run", "agent_team_instance_id": instance["instance_id"]},
    )
    assert forbidden.status_code == 403


def test_run_005_closed_run_rejects_new_task(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    closed = unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}:close", headers=USER_HEADERS, json={}))
    assert closed["run_status"] == "closed"

    response = client.post(
        f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks",
        headers=USER_HEADERS,
        json={"client_request_id": "closed_task", "input_text": "还想继续"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RUN_ALREADY_CLOSED"


def test_cancel_001_task_cancel_keeps_run_active(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    task = start_task(client, run["agent_run_id"])

    cancelled = unwrap(client.post(f"/api/openops/v1/tasks/{task['task_id']}:cancel", headers=USER_HEADERS, json={}))
    assert cancelled["status"] == "cancelled"
    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state", headers=USER_HEADERS))
    assert state["run"]["run_status"] == "active"

    next_task = start_task(client, run["agent_run_id"], "继续新的巡检")
    assert next_task["status"] == "running"


def test_model_switch_is_run_level_only(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    selected = unwrap(
        client.post(
            f"/api/openops/v1/agent-runs/{run['agent_run_id']}:select-model",
            headers=USER_HEADERS,
            json={"client_request_id": "model_select", "model_source": "qwen3.5-instruct"},
        )
    )
    assert selected["selected_model"] == "qwen3.5-instruct"

    cfgs = unwrap(client.get(f"/api/openops/v1/agent-teams/{instance['instance_id']}/config-versions", headers=USER_HEADERS))
    assert len(cfgs) == 1


def test_resolve_available_skills_unifies_skill_key(monkeypatch):
    """键基统一：平台 skill 按 skill_key；用户绑定 skill 也按 skill_key（旧数据无 key 回退 display_name）。
    三面同源（执行门禁/LLM 工具描述/composer「/」列表）必须用同一个键。"""
    import asyncio

    from app import run_state_service as rss

    async def _skills(uid, include_platform=True):
        return [{"skill_key": "alarm-query", "display_name": "告警查询", "status": "active",
                 "version_no": 2, "checksum_sha256": "abc", "source_type": "platform"}]

    async def _bindings(cv):
        return [
            {"asset_type": "skill", "skill_status": "enabled", "skill_key": "my-tool",
             "skill_display_name": "我的工具", "skill_version_no": 1},
            {"asset_type": "skill", "skill_status": "enabled", "skill_key": None,
             "skill_display_name": "旧数据无key", "skill_version_no": 1},  # 回退 display_name
            {"asset_type": "mcp", "skill_status": None, "skill_key": None, "skill_display_name": None},
        ]

    from infra.repositories import assets as assets_repo

    monkeypatch.setattr(assets_repo, "list_skills", _skills)
    from infra.repositories import agent_teams as at_repo

    monkeypatch.setattr(at_repo, "list_binding_details", _bindings)

    out = asyncio.run(rss.resolve_available_skills("u1", "cv1"))
    assert set(out) == {"alarm-query", "my-tool", "旧数据无key"}
    assert out["alarm-query"]["display_name"] == "告警查询"
    assert out["my-tool"]["source_type"] == "user" and out["my-tool"]["display_name"] == "我的工具"


def test_resolve_available_skills_surfaces_description(monkeypatch):
    """发现链路：manifest_json.description 经 resolve 流入 available_skills；binding 覆盖 user skill 后
    仍从 desc_by_key 回填 description（否则被绑定的个人 skill 会丢用途、模型仍零感知）。"""
    import asyncio

    from app import run_state_service as rss

    async def _skills(uid, include_platform=True):
        return [
            {"skill_key": "alarm-query", "display_name": "告警查询", "status": "active", "version_no": 2,
             "checksum_sha256": "abc", "source_type": "platform",
             "manifest_json": {"description": "按 APPID 查询最近告警"}},
            {"skill_key": "my-tool", "display_name": "我的工具", "status": "active", "version_no": 1,
             "checksum_sha256": "d", "source_type": "user", "manifest_json": {"description": "我的自定义排查"}},
        ]

    async def _bindings(cv):  # my-tool 也走 binding 明细（会用新 dict 覆盖第一循环项）
        return [{"asset_type": "skill", "skill_status": "enabled", "skill_key": "my-tool",
                 "skill_display_name": "我的工具", "skill_version_no": 1}]

    from infra.repositories import agent_teams as at_repo
    from infra.repositories import assets as assets_repo

    monkeypatch.setattr(assets_repo, "list_skills", _skills)
    monkeypatch.setattr(at_repo, "list_binding_details", _bindings)

    out = asyncio.run(rss.resolve_available_skills("u1", "cv1"))
    assert out["alarm-query"]["description"] == "按 APPID 查询最近告警"
    assert out["my-tool"]["description"] == "我的自定义排查"  # 覆盖后未丢


def test_render_skill_catalog_injects_purpose():
    """发现链路终点：_render_skill_catalog 把 description 拼进 run_platform_skill 工具描述（模型据此主动调）；
    无 description 的 skill 退回仅名字、不拼空冒号；空集给空态文案。"""
    import pytest as _pytest

    rt = _pytest.importorskip("runtime.agentscope_runtime")  # 需 agentscope 依赖，未装则跳过

    desc = rt._render_skill_catalog({
        "inspection": {"display_name": "巡检 inspection", "description": "巡检 Skill——检查 redis 连接池、p99"},
        "bare": {"display_name": "裸技能"},  # 无 description
    })
    assert "`inspection`（巡检 inspection）：巡检 Skill——检查 redis 连接池、p99" in desc
    assert "- `bare`（裸技能）" in desc and "`bare`（裸技能）：" not in desc  # 无 description 不拼冒号
    assert "（当前实例未装配任何 Skill）" in rt._render_skill_catalog({})


def test_build_system_prompt_uses_role_and_platform_rules():
    """主 Agent 人设装配：用户 main.role/append 进 prompt（不再被丢弃、旧硬编码自诊断默认移除）；
    平台层固定「优先用匹配 Skill、别自行发挥」+ 安全规则；skill_hint 命中时追加确定性执行指令。"""
    import pytest as _pytest

    rt = _pytest.importorskip("runtime.agentscope_runtime")  # 需 agentscope 依赖
    from runtime.task_registry import TaskState

    st = TaskState(task_id="t", run_id="r", user_id="u", instance_id="i", input_text="x")
    st.main_role = "遇到告警诊断请调用 fault-diagnosis 技能并走五步法"
    p = rt._build_system_prompt(st)
    assert "fault-diagnosis 技能并走五步法" in p                 # 用户人设进了 prompt
    assert "必须优先调用该 Skill" in p and "不要自行发挥" in p    # 平台层 skill 引导
    assert "人工批准" in p and "臆造数据" in p                    # 安全规则保留
    assert "先巡检定界" not in p                                  # 旧硬编码自诊断默认已移除

    st.main_role_append = "补充：优先看 redis 指标"
    assert "补充：优先看 redis 指标" in rt._build_system_prompt(st)   # 实例级追加生效

    st.skill_hint = "fault-diagnosis"
    assert "第一步调用 run_platform_skill(skill_name='fault-diagnosis')" in rt._build_system_prompt(st)

    st_bare = TaskState(task_id="t", run_id="r", user_id="u", instance_id="i", input_text="x")
    assert rt._DEFAULT_MAIN_ROLE in rt._build_system_prompt(st_bare)  # role 缺失 → 兜底默认


def test_start_task_populates_main_role_not_dropped(client):
    """回归根因：start_task 把模板 content_json.main.role 流入 st.main_role（此前被丢弃）。
    自然语言（无 / 前缀）→ skill_hint 不强制。"""
    from runtime import task_registry

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    start_task(client, run["agent_run_id"], "帮我诊断告警 X")
    st = task_registry.get_by_run(run["agent_run_id"])
    assert st is not None
    assert st.main_role and "调度" in st.main_role   # seed 模板 main.role 已流入（不再被丢弃）
    assert st.skill_hint is None                       # 无 / 前缀 → 不强制某个 skill


def test_start_task_slash_prefix_sets_skill_hint(client):
    """/skill 确定性触发：input_text 前导 /<skill> 且命中 available_skills → st.skill_hint 被填充
    （前端只把 skill 名塞文本、不发结构化 hint，服务端兜底解析）。"""
    from runtime import task_registry

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    start_task(client, run["agent_run_id"], "/inspection 帮我巡检 APP-A")
    st = task_registry.get_by_run(run["agent_run_id"])
    assert st is not None and st.skill_hint == "inspection"  # seed 平台 skill，main.skills=[] 不收窄


def test_available_skills_endpoint_shape_and_ownership(client):
    """GET /agent-teams/{id}/available-skills：与执行门禁同源；他人实例 403。"""
    from conftest import OTHER_HEADERS, USER_HEADERS, create_instance, unwrap

    inst = create_instance(client)
    rows = unwrap(client.get(f"/api/openops/v1/agent-teams/{inst['instance_id']}/available-skills",
                             headers=USER_HEADERS))
    assert isinstance(rows, list) and rows  # seed 平台 skill（inspection）至少 1 条
    assert {"skill_key", "display_name", "source_type"} <= set(rows[0])
    assert any(r["skill_key"] == "inspection" for r in rows)

    r = client.get(f"/api/openops/v1/agent-teams/{inst['instance_id']}/available-skills", headers=OTHER_HEADERS)
    assert r.status_code == 403


def test_run_title_autoname_and_rename(client):
    """会话名：首个任务输入自动起名（前 30 字）；:rename 覆盖并落审计；他人 403。"""
    from conftest import OTHER_HEADERS

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    rid = run["agent_run_id"]
    assert run.get("run_title") in (None, "")  # 新会话未起名

    long_text = "支付下单接口刚才延迟突然变高，帮我看下是什么问题，谢谢啦，多余的字要被裁掉"
    start_task(client, rid, long_text)
    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/state", headers=USER_HEADERS))
    title = state["run"]["run_title"]
    assert title and len(title) <= 31 and title.startswith(long_text[:10])
    assert title.endswith("…")  # 超 30 字截断标记

    # 列表面（侧栏历史会话数据源）带 run_title
    runs_list = unwrap(client.get("/api/openops/v1/agent-runs", headers=USER_HEADERS))
    assert any(r["agent_run_id"] == rid and r["run_title"] == title for r in runs_list)

    # 重命名：trim + 生效 + 审计
    renamed = unwrap(client.post(f"/api/openops/v1/agent-runs/{rid}:rename", headers=USER_HEADERS,
                                 json={"client_request_id": "rn1", "title": "  Redis 连接池  排查  "}))
    assert renamed["run_title"] == "Redis 连接池 排查"
    state2 = unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/state", headers=USER_HEADERS))
    assert state2["run"]["run_title"] == "Redis 连接池 排查"
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{rid}", headers=USER_HEADERS))
    assert "run.renamed" in {e["event_type"] for e in events}

    # 再起任务不覆盖既有名称（只有首个任务起名）
    start_task(client, rid, "第二个任务的输入不该改标题")
    state3 = unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/state", headers=USER_HEADERS))
    assert state3["run"]["run_title"] == "Redis 连接池 排查"

    # 他人无权改名；空白标题 400
    r = client.post(f"/api/openops/v1/agent-runs/{rid}:rename", headers=OTHER_HEADERS,
                    json={"client_request_id": "rn2", "title": "hack"})
    assert r.status_code == 403
    r2 = client.post(f"/api/openops/v1/agent-runs/{rid}:rename", headers=USER_HEADERS,
                     json={"client_request_id": "rn3", "title": "   "})
    assert r2.status_code in (400, 422)  # service trim 后空 → VALIDATION_FAILED（422=pydantic 拦）


def test_run_delete_soft_and_list_excludes(client):
    """会话删除（软删）：列表不再含该 run、他人 403、审计 run.deleted、删后 state 404。"""
    from conftest import OTHER_HEADERS

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    rid = run["agent_run_id"]

    # 他人无权删
    r = client.post(f"/api/openops/v1/agent-runs/{rid}:delete", headers=OTHER_HEADERS, json={})
    assert r.status_code == 403

    out = unwrap(client.post(f"/api/openops/v1/agent-runs/{rid}:delete", headers=USER_HEADERS, json={}))
    assert out["deleted"] is True

    runs_list = unwrap(client.get("/api/openops/v1/agent-runs", headers=USER_HEADERS))
    assert all(r2["agent_run_id"] != rid for r2 in runs_list)  # 列表不再含
    r2 = client.get(f"/api/openops/v1/agent-runs/{rid}/state", headers=USER_HEADERS)
    assert r2.status_code == 404  # get_run 过滤 deleted_at


def test_idem_survives_process_restart(client):
    """P1（INIT-006 跨进程版）：幂等键落 PG——清空内存 L1（模拟重启）后同
    client_request_id 重放仍命中，返回首个 run 而非重复创建。"""
    from infra import idempotency

    instance = create_instance(client)
    crid = f"idem_{time.time_ns()}"
    body = {"client_request_id": crid, "agent_team_instance_id": instance["instance_id"]}
    r1 = unwrap(client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS, json=body))

    idempotency.clear()  # 模拟进程重启：内存 L1 清空，PG 行仍在
    r2 = unwrap(client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS, json=body))
    assert r2["run"]["agent_run_id"] == r1["run"]["agent_run_id"]

    runs_list = unwrap(client.get("/api/openops/v1/agent-runs", headers=USER_HEADERS))
    assert sum(1 for r in runs_list if r["agent_run_id"] == r1["run"]["agent_run_id"]) == 1


def test_p2_restart_orphan_convergence(client):
    """P2：任务 running 中"重启"（清内存注册表）→ converge 标 interrupted；
    /state 回退快照展示 interrupted；cancel 按快照收敛不再 404。"""
    import asyncio as _asyncio

    from app import run_state_service as rss
    from runtime import task_registry

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    task = start_task(client, run["agent_run_id"])  # mock 剧本停在 ASK，保持 running
    tid = task["task_id"]

    task_registry.reset()  # 模拟进程重启：内存态清空，PG 快照仍在
    n = _asyncio.run(rss.converge_orphan_tasks())
    assert n >= 1

    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state", headers=USER_HEADERS))
    assert state["active_task"] is not None
    assert state["active_task"]["task_id"] == tid
    assert state["active_task"]["status"] == "interrupted"

    out = unwrap(client.post(f"/api/openops/v1/tasks/{tid}:cancel", headers=USER_HEADERS, json={}))
    assert out["status"] == "cancelled"


def test_p3_session_state_continuity(client, runtime_backend):
    """P3：同 run 两个 task——AgentState 落 PG、第二轮 context 累积（同会话不再失忆）。"""
    import asyncio as _asyncio

    import pytest as _pytest

    if runtime_backend != "agentscope":
        _pytest.skip("AgentState 持久化仅 agentscope runtime 生效")
    from test_agui import _annotate_recover_no_ask

    from infra.repositories import agent_session_states

    _annotate_recover_no_ask(client)  # 免审批直通完成
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    rid, fsid = run["agent_run_id"], run["framework_session_id"]

    def _status():
        s = unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/state", headers=USER_HEADERS))
        at = s.get("active_task")
        return at if at and at["status"] in ("completed", "failed") else None

    start_task(client, rid, "第一句：请记住数字 42")
    assert wait_until(_status)["status"] == "completed"
    saved1 = _asyncio.run(agent_session_states.get_state_json(fsid, "main"))
    assert saved1 is not None and len(saved1.get("context") or []) > 0  # 第一轮已落 PG

    start_task(client, rid, "第二句：我刚让你记的数字是多少")
    assert wait_until(_status)["status"] == "completed"
    saved2 = _asyncio.run(agent_session_states.get_state_json(fsid, "main"))
    # 第二轮恢复了第一轮 context 再累积（不再失忆）
    assert len(saved2.get("context") or []) > len(saved1.get("context") or [])


def _dispatch_env(client):
    """建 instance/run/task 后取内存 st 与 run 行，供直调 dispatch。"""
    import asyncio as _asyncio

    from infra.repositories import runs as runs_repo
    from runtime import task_registry

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    start_task(client, run["agent_run_id"], "环境预热")
    st = task_registry.get_by_run(run["agent_run_id"])
    assert st is not None and st.sub_agents, "seed 模板应装配 sub_agents 画像"
    run_row = _asyncio.run(runs_repo.get_run(run["agent_run_id"]))
    return st, run_row


def test_d2_dispatch_budget_and_unknown_role(client):
    """D2：预算两层与角色校验在 spawn 前拒绝（不依赖 agentscope，双解释器可跑）。"""
    import asyncio as _asyncio

    from runtime import subagent_dispatch

    st, run_row = _dispatch_env(client)
    # 未知角色
    out = _asyncio.run(subagent_dispatch.dispatch(st, run_row, [{"role": "nope", "task": "x"}]))
    assert "未知角色" in out
    # max_children 拒发（活跃 0 + 本批 2 > 1）
    st.dispatch_cfg = {"max_children": 1, "delegation_max_spawns": 10}
    out = _asyncio.run(subagent_dispatch.dispatch(
        st, run_row, [{"role": "inspect", "task": "a"}, {"role": "diagnose", "task": "b"}]))
    assert "预算拒绝" in out
    # 累计兜底拒发（total 0 + 本批 1 > 0 不可能——用 max_spawns=0 不合法域，直接造已有行）
    st.dispatch_cfg = {"max_children": 3, "delegation_max_spawns": 1}
    from infra.repositories import delegations

    _asyncio.run(delegations.create(st.run_id, st.task_id, "inspect", "老账", 60, st.user_id))
    out = _asyncio.run(subagent_dispatch.dispatch(st, run_row, [{"role": "inspect", "task": "c"}]))
    assert "累计兜底拒绝" in out


def test_d3_dispatch_end_to_end(client, runtime_backend):
    """D3：真派一个 inspect 子 Agent（stub 模型完整 ReAct 循环）→ 汇报合并返回 + 账本 completed +
    审计含 dispatched/reported（payload 带 agent_key）。"""
    import asyncio as _asyncio

    import pytest as _pytest

    if runtime_backend != "agentscope":
        _pytest.skip("子 Agent 循环仅 agentscope runtime")
    from infra.repositories import delegations
    from runtime import subagent_dispatch

    st, run_row = _dispatch_env(client)
    out = _asyncio.run(subagent_dispatch.dispatch(
        st, run_row, [{"role": "inspect", "task": "查看 APP-A 健康状态"}]))
    assert "【巡检" in out  # 合并汇报带角色标签
    rows = _asyncio.run(delegations.list_by_task(st.task_id))
    assert rows and rows[-1]["delegation_status"] == "completed"
    assert rows[-1]["report_text"]
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{st.run_id}", headers=USER_HEADERS))
    types = {e["event_type"] for e in events}
    assert "openops.subagent.dispatched" in types and "openops.subagent.reported" in types
    reported = next(e for e in reversed(events) if e["event_type"] == "openops.subagent.reported")
    assert reported["payload_redacted_json"]["report_summary"]
    assert reported["payload_redacted_json"]["dispatch_batch_id"] == str(rows[-1]["dispatch_batch_id"])


def test_subagent_dispatch_batches_persist_and_increment(client, monkeypatch):
    """一次 dispatch 共享批次、跨 dispatch 递增；恢复投影只给脱敏摘要，不泄漏账本文本。"""
    import asyncio as _asyncio

    from infra.repositories import delegations
    from runtime import subagent_dispatch

    st, run_row = _dispatch_env(client)
    st.dispatch_cfg = {"max_children": 3, "delegation_max_spawns": 10}

    async def _fake_run(_st, _run, _sub, role, text, _did, _batch_id=None, _batch_no=None):
        return f"{role} report {text}"

    monkeypatch.setattr(subagent_dispatch, "_run_with_budget", _fake_run)
    secret = "Bearer abcdefghijkl"
    _asyncio.run(subagent_dispatch.dispatch(st, run_row, [
        {"role": "inspect", "task": f"巡检 {secret}"},
        {"role": "diagnose", "task": "诊断"},
    ]))
    _asyncio.run(subagent_dispatch.dispatch(st, run_row, [
        {"role": "inspect", "task": "复核"},
    ]))

    rows = _asyncio.run(delegations.list_by_task(st.task_id))
    assert len(rows) == 3
    assert rows[0]["dispatch_batch_id"] == rows[1]["dispatch_batch_id"]
    assert rows[0]["dispatch_batch_no"] == rows[1]["dispatch_batch_no"] == 1
    assert rows[2]["dispatch_batch_id"] != rows[0]["dispatch_batch_id"]
    assert rows[2]["dispatch_batch_no"] == 2

    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{st.run_id}/state", headers=USER_HEADERS))
    projected = state["delegations"][-3:]
    assert all("task_text" not in row and "report_text" not in row for row in projected)
    assert projected[0]["task_summary"] == "巡检 [REDACTED]"
    assert secret not in str(projected)
    assert projected[0]["child_task_id"].startswith(st.task_id + ".inspect-")

    dispatched = [e for e in state["recent_events"] if e["event_type"] == "openops.subagent.dispatched"]
    assert len(dispatched) >= 3
    first_payload = dispatched[-3]["payload_redacted_json"]
    assert {"agent_key", "agent_label", "leader_task_id", "child_task_id", "delegation_id",
            "dispatch_batch_id", "dispatch_batch_no", "summary"} <= set(first_payload)
    assert secret not in str(first_payload)


def test_subagent_cancelled_is_authoritative_event(client, monkeypatch):
    import asyncio as _asyncio

    import pytest as _pytest

    from infra.repositories import delegations
    from runtime import subagent_dispatch

    st, run_row = _dispatch_env(client)

    async def _cancel(*_args, **_kwargs):
        raise _asyncio.CancelledError()

    monkeypatch.setattr(subagent_dispatch, "_run_with_budget", _cancel)
    with _pytest.raises(_asyncio.CancelledError):
        _asyncio.run(subagent_dispatch.dispatch(
            st, run_row, [{"role": "inspect", "task": "取消测试"}]))
    rows = _asyncio.run(delegations.list_by_task(st.task_id))
    assert rows[-1]["delegation_status"] == "cancelled"
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{st.run_id}", headers=USER_HEADERS))
    cancelled = next(e for e in reversed(events) if e["event_type"] == "openops.subagent.cancelled")
    assert cancelled["payload_redacted_json"]["delegation_id"] == str(rows[-1]["delegation_id"])


def test_e1_subagent_approval_bridge(client, runtime_backend):
    """E1：恢复工具绑恢复 Agent——子循环触发 ASK → 审批行带子 task_id → decide 按 task_id
    路由回子 approval_ev（不误触主 st）→ approve 后子完成汇报；再走一遍 rejected 路径。"""
    import asyncio as _asyncio
    from types import SimpleNamespace

    import pytest as _pytest

    if runtime_backend != "agentscope":
        _pytest.skip("子 Agent 循环仅 agentscope runtime")
    from app import run_state_service as rss
    from infra.repositories import delegations
    from infra.repositories import runs as runs_repo
    from runtime import subagent_dispatch

    st, run_row = _dispatch_env(client)

    async def _wait_child_approval():
        for _ in range(400):
            rows = await runs_repo.pending_approvals(st.run_id)
            # 只认子 task 的审批（主任务「环境预热」的 ASK 也可能 pending）
            rows = [r for r in rows if str(r["task_id"]).startswith(st.task_id + ".")]
            if rows:
                return rows[0]
            await _asyncio.sleep(0.05)
        raise AssertionError("子 Agent 审批未出现")

    async def _scenario():
        user = {"user_id": st.user_id}
        # ① approve 路径
        job = _asyncio.ensure_future(subagent_dispatch.dispatch(
            st, run_row, [{"role": "recover", "task": "重启 APP-A"}]))
        appr = await _wait_child_approval()
        assert str(appr["task_id"]).startswith(f"{st.task_id}.recover")
        assert appr["tool_call_name"] == "recover_execute"
        out = await rss.decide_approval(user, str(appr["approval_request_id"]),
                                        SimpleNamespace(decision="approved", reason=""))
        assert out["decision"] == "approved"
        assert st.approval_result is None  # E1 路由：子审批不误置主 st 的握手信号
        report_ok = await job
        # ② rejected 路径：子照常恢复循环并收尾（工具不执行，不挂死）
        job2 = _asyncio.ensure_future(subagent_dispatch.dispatch(
            st, run_row, [{"role": "recover", "task": "再试一次重启"}]))
        appr2 = await _wait_child_approval()
        out2 = await rss.decide_approval(user, str(appr2["approval_request_id"]),
                                         SimpleNamespace(decision="rejected", reason="先不动"))
        assert out2["decision"] == "rejected"
        report_rej = await job2
        return report_ok, report_rej

    report_ok, report_rej = _asyncio.run(_scenario())
    assert "【恢复·completed】" in report_ok
    assert "【恢复·completed】" in report_rej  # 拒绝≠子失败：跳过工具后继续收尾汇报
    rows = _asyncio.run(delegations.list_by_task(st.task_id))
    assert [r["delegation_status"] for r in rows[-2:]] == ["completed", "completed"]
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{st.run_id}", headers=USER_HEADERS))
    types = {e["event_type"] for e in events}
    assert "approval.approved" in types and "approval.rejected" in types


def test_e4_child_governance_config(client, runtime_backend, monkeypatch):
    """E4：子 Agent 按画像接 ReActConfig(max_iters)/ContextConfig(tool_result_limit)。"""
    import asyncio as _asyncio

    import pytest as _pytest

    if runtime_backend != "agentscope":
        _pytest.skip("子 Agent 循环仅 agentscope runtime")
    import agentscope.agent as ag_mod

    from runtime import subagent_dispatch

    captured: dict[str, tuple] = {}  # name → (react_config, context_config)；按名取避免主 Agent 构造竞态
    real_agent = ag_mod.Agent

    class SpyAgent(real_agent):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):  # noqa: ANN002, ANN003
            captured[str(kw.get("name"))] = (kw.get("react_config"), kw.get("context_config"))
            super().__init__(*a, **kw)

    monkeypatch.setattr(ag_mod, "Agent", SpyAgent)
    st, run_row = _dispatch_env(client)
    prof = next(s for s in st.sub_agents if s["key"] == "inspect")
    prof["max_iters"], prof["tool_result_limit"] = 7, 12345  # 非默认值才证明真接上了
    out = _asyncio.run(subagent_dispatch.dispatch(
        st, run_row, [{"role": "inspect", "task": "查看 APP-A 健康状态"}]))
    assert "【巡检" in out
    react_cfg, ctx_cfg = captured["sre-inspect"]
    assert react_cfg is not None and react_cfg.max_iters == 7
    assert ctx_cfg is not None and ctx_cfg.tool_result_limit == 12345


def test_e1_approval_wait_excluded_from_timeout_budget(client, runtime_backend, monkeypatch):
    """E1：审批等待期不计执行超时预算——预算 2s、等人 3s 后 approve，子仍完成而非 timeout。"""
    import asyncio as _asyncio
    from types import SimpleNamespace

    import pytest as _pytest

    if runtime_backend != "agentscope":
        _pytest.skip("子 Agent 循环仅 agentscope runtime")
    from app import run_state_service as rss
    from infra.repositories import runs as runs_repo
    from runtime import subagent_dispatch

    monkeypatch.setattr(subagent_dispatch, "SUB_TIMEOUT_S", 2.0)
    st, run_row = _dispatch_env(client)

    async def _scenario():
        job = _asyncio.ensure_future(subagent_dispatch.dispatch(
            st, run_row, [{"role": "recover", "task": "重启 APP-A"}]))
        appr = None
        for _ in range(400):
            rows = await runs_repo.pending_approvals(st.run_id)
            rows = [r for r in rows if str(r["task_id"]).startswith(st.task_id + ".")]
            if rows:
                appr = rows[0]
                break
            await _asyncio.sleep(0.05)
        assert appr is not None
        await _asyncio.sleep(3.0)  # 挂着不批 3s > 预算 2s：wait_for 硬包会在这里判死
        await rss.decide_approval({"user_id": st.user_id}, str(appr["approval_request_id"]),
                                  SimpleNamespace(decision="approved", reason=""))
        return await job

    report = _asyncio.run(_scenario())
    assert "【恢复·completed】" in report
    assert "超时" not in report


def test_child_skills_slice_from_unfiltered_pool():
    """编排对称化连带：main.skills 白名单只收窄 main 自己——子 Agent 画像切片必须来自
    skills_pool 未过滤全集，否则 main 一收窄就掐死子角色技能（主从技能面独立，老 D6 口径）。"""
    from runtime.subagent_dispatch import _child_state
    from runtime.task_registry import TaskState

    st = TaskState(task_id="t", run_id="r", user_id="u", instance_id="i", input_text="x")
    st.skills_pool = {"inspection": {"version_no": 1}, "log-query": {"version_no": 1}}
    st.available_skills = {"inspection": {"version_no": 1}}  # main.skills=["inspection"] 收窄后
    child = _child_state(st, {"key": "log", "skills": ["log-query"], "mcp_tools": []}, "log", "查日志", "dddddddd-0002")
    assert set(child.available_skills or {}) == {"log-query"}  # 不受 main 收窄影响
    # 兼容路径：无 skills_pool（旧快照恢复）回退 available_skills
    st.skills_pool = None
    child2 = _child_state(st, {"key": "ins", "skills": ["inspection"], "mcp_tools": []}, "ins", "巡检", "dddddddd-0003")
    assert set(child2.available_skills or {}) == {"inspection"}


def test_e1_child_dynamic_tools_respect_whitelist(client, runtime_backend, monkeypatch):
    """per-agent 隔离（编排对称化）：动态 MCP 工具对 main/sub 统一按白名单裁剪——
    子按画像 mcp_tools，main 按模板 default_tools；模板没勾的动态工具对 main 也不注入。"""
    import asyncio as _asyncio

    import pytest as _pytest

    if runtime_backend != "agentscope":
        _pytest.skip("toolkit 构建仅 agentscope runtime")
    from runtime import agentscope_runtime as rt
    from runtime import subagent_dispatch

    st, run_row = _dispatch_env(client)

    def _spec(name):
        return {"name": name, "description": "内网动态工具", "server_url": "http://mcp.internal",
                "readonly": True, "scope_mode": "none", "appid_arg_path": None,
                "input_schema": {"type": "object", "properties": {}}}

    async def _fake_specs():
        return [_spec("dyn_alarm_query"), _spec("dyn_log_query")]

    monkeypatch.setattr(rt, "_dynamic_mcp_specs", _fake_specs)
    # 子：白名单只放 dyn_alarm_query → dyn_log_query 不得注入
    sub = {"key": "probe", "label": "探针", "role": "只查告警", "skills": [], "mcp_tools": ["dyn_alarm_query"]}
    child = subagent_dispatch._child_state(st, sub, "probe", "查告警", "dddddddd-0001")
    _asyncio.run(rt._build_toolkit(child, run_row))
    assert "dyn_alarm_query" in (child.tool_annotations or {})
    assert "dyn_log_query" not in (child.tool_annotations or {})
    # main：同样按模板白名单剪（豁免已去除）——种子模板 default_tools 没勾动态工具 → 不注入
    _asyncio.run(rt._build_toolkit(st, run_row))
    assert "dyn_alarm_query" not in st.tool_annotations and "dyn_log_query" not in st.tool_annotations
    # 模板勾选后（default_tools 含该名）→ main 注入该工具、未勾的仍剪
    st.template_tools = set(st.template_tools or set()) | {"dyn_alarm_query"}
    _asyncio.run(rt._build_toolkit(st, run_row))
    assert "dyn_alarm_query" in st.tool_annotations and "dyn_log_query" not in st.tool_annotations


# ---- 缺陷批回归（regression-0712 报告 DEF-1/2，替代未入库的探针文件） ----

def test_def1_late_decide_does_not_pollute_main_handshake(client):
    """DEF-1：子任务终态后遗留审批的迟到 decide——不得命中主任务握手信号（旧 get_by_run fallback 即旁路）。"""
    import asyncio as _asyncio
    from types import SimpleNamespace

    from app import run_state_service as rss
    from infra.repositories import runs as runs_repo
    from runtime import task_registry

    st, run_row = _dispatch_env(client)
    ghost_task = f"{st.task_id}.recover-deadbeef"  # 已注销的子 task（registry 无此项）

    async def _scenario():
        appr = await runs_repo.create_approval(
            st.user_id, st.instance_id, st.run_id, ghost_task, "recover_execute",
            {"appid": "APP-A"}, str(run_row["audit_trace_id"]), str(run_row["framework_session_id"]))
        assert task_registry.get_by_task(ghost_task) is None
        before = st.approval_result
        out = await rss.decide_approval({"user_id": st.user_id}, str(appr["approval_request_id"]),
                                        SimpleNamespace(decision="approved", reason=""))
        assert out["decision"] == "approved"          # 决策照常落库
        assert st.approval_result == before           # 主 st 握手信号未被污染（修复点）
        return True

    assert _asyncio.run(_scenario())
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{st.run_id}", headers=USER_HEADERS))
    appr_evs = [e for e in events if e["event_type"] == "approval.approved"]
    assert appr_evs and appr_evs[-1]["payload_redacted_json"]["agent_key"] == "recover"  # DEF-6


def test_def1_cascade_cancels_pending_approvals(client):
    """DEF-1 级联收口：cancel_pending_approvals 把该 task 的 pending 审批全部置 cancelled。"""
    import asyncio as _asyncio

    from infra.repositories import runs as runs_repo

    st, run_row = _dispatch_env(client)
    tid = f"{st.task_id}.inspect-cafe0001"

    async def _scenario():
        appr = await runs_repo.create_approval(
            st.user_id, st.instance_id, st.run_id, tid, "recover_execute",
            {}, str(run_row["audit_trace_id"]), str(run_row["framework_session_id"]))
        n = await runs_repo.cancel_pending_approvals(tid, st.user_id)
        fresh = await runs_repo.get_approval(str(appr["approval_request_id"]))
        return n, fresh["decision"]

    n, decision = _asyncio.run(_scenario())
    assert n == 1 and decision == "cancelled"


def test_def2_cross_batch_task_ids_unique(client):
    """DEF-2：同角色跨批 task_id 全局唯一（did 后缀）；第一批收尾不摘第二批 registry 项。"""
    from runtime import subagent_dispatch, task_registry

    st, _run_row = _dispatch_env(client)
    sub = {"key": "inspect", "label": "巡检", "role": "r", "skills": [], "mcp_tools": []}
    c1 = subagent_dispatch._child_state(st, sub, "inspect", "t1", "aaaaaaaa-1111")
    c2 = subagent_dispatch._child_state(st, sub, "inspect", "t2", "bbbbbbbb-2222")
    assert c1.task_id != c2.task_id
    assert c1.task_id.startswith(st.task_id + ".inspect-")  # 兼容 leader 前缀过滤断言
    task_registry.register_subtask(c1)
    task_registry.register_subtask(c2)
    assert task_registry.get_by_task(c1.task_id) is c1 and task_registry.get_by_task(c2.task_id) is c2
    task_registry.unregister_subtask(c1.task_id)  # 第一批收尾
    assert task_registry.get_by_task(c2.task_id) is c2  # 第二批幸存者不被摘（修复点）
    task_registry.unregister_subtask(c2.task_id)


def test_def4_orphan_cancel_terminal_snapshot_truthful(client):
    """DEF-4：终态快照 cancel 不谎报（already_terminal+真实状态+DB 不变）；interrupted → cancelled+审计。"""
    import asyncio as _asyncio
    import uuid as _uuid

    from app import run_state_service as rss
    from infra.repositories import task_states
    from runtime.task_registry import TaskState

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    rid, trace = run["agent_run_id"], str(run["audit_trace_id"])

    def _snap(status):
        tid = "tsk_" + _uuid.uuid4().hex[:10]
        st = TaskState(task_id=tid, run_id=rid, user_id="0026demo01",
                       instance_id=instance["instance_id"], input_text="x")
        _asyncio.run(task_states.upsert_snapshot(st, status, trace))
        return tid

    # 终态快照（completed）→ 返回真实状态，DB 不变
    tid = _snap("completed")
    out = _asyncio.run(rss.cancel_task({"user_id": "0026demo01"}, tid))
    assert out["status"] == "completed" and out.get("already_terminal") is True
    row = _asyncio.run(task_states.get_by_task(tid))
    assert row["task_status"] == "completed"

    # interrupted → cancelled + task.cancelled 审计
    tid2 = _snap("interrupted")
    out2 = _asyncio.run(rss.cancel_task({"user_id": "0026demo01"}, tid2))
    assert out2["status"] == "cancelled"
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{rid}", headers=USER_HEADERS))
    assert any(e["event_type"] == "task.cancelled" and e["task_id"] == tid2 for e in events)


def test_def5_soft_deleted_run_audit_visible(client):
    """DEF-5：软删会话后 owner 仍可查 /audit/runs 且含 run.deleted；他人仍 403。"""
    from conftest import OTHER_HEADERS

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    rid = run["agent_run_id"]
    unwrap(client.post(f"/api/openops/v1/agent-runs/{rid}:delete", headers=USER_HEADERS))
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{rid}", headers=USER_HEADERS))
    assert any(e["event_type"] == "run.deleted" for e in events)  # 删除有痕（修复点）
    assert client.get(f"/api/openops/v1/audit/runs/{rid}", headers=OTHER_HEADERS).status_code == 403


def test_def8_dispatch_overflow_rejected(client):
    """DEF-8：单批 >5 直接拒绝并报真实数量（旧行为静默截断第 6 个）。"""
    import asyncio as _asyncio

    from runtime import subagent_dispatch

    st, run_row = _dispatch_env(client)
    tasks = [{"role": "inspect", "task": f"t{i}"} for i in range(6)]
    out = _asyncio.run(subagent_dispatch.dispatch(st, run_row, tasks))
    assert "6" in out and "5" in out and "拆" in out
