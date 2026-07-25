"""/admin/studio/*：管理员回溯复盘端点（403 隔离 / run 列表 stats / 详情分组与交接联合）。"""
from __future__ import annotations

import uuid


from _helpers import _fsid, _insert_delegation, _insert_span
from conftest import (ADMIN_HEADERS, OTHER_HEADERS, USER_HEADERS, create_instance,
                      create_run, unwrap)


def test_studio_endpoints_admin_only(client):
    for headers in (USER_HEADERS, OTHER_HEADERS):
        r = client.get("/api/openops/v1/admin/studio/runs",
                       params={"user_id": "0026demo01"}, headers=headers)
        assert r.status_code == 403, r.text
        r = client.get(f"/api/openops/v1/admin/studio/runs/{uuid.uuid4()}", headers=headers)
        assert r.status_code == 403, r.text
    r = client.get("/api/openops/v1/admin/studio/runs", params={"user_id": "x"})
    assert r.status_code == 401, r.text


def test_run_list_by_user_with_stats(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    rid = run["agent_run_id"]
    _insert_span(user_id="0026demo01", agent_run_id=rid, session_id="s1", agent_role="main",
                 kind="llm", input_tokens=100, output_tokens=20, latency_ms=1000.0)
    _insert_span(user_id="0026demo01", agent_run_id=rid, session_id="s1", agent_role="main",
                 kind="tool", tool_name="t", latency_ms=200.0)

    data = unwrap(client.get("/api/openops/v1/admin/studio/runs",
                             params={"user_id": "0026demo01"}, headers=ADMIN_HEADERS))
    assert data["total"] == 1 and data["page"] == 1
    item = data["items"][0]
    assert item["agent_run_id"] == rid and item["deleted"] is False
    assert item["stats"] == {"agents": 1, "llm_calls": 1, "tool_calls": 1,
                             "total_tokens": 120, "latency_ms": 1200.0}

    other = unwrap(client.get("/api/openops/v1/admin/studio/runs",
                              params={"user_id": "0099other"}, headers=ADMIN_HEADERS))
    assert other["items"] == [] and other["total"] == 0


def test_run_detail_grouping_and_handover(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    rid = run["agent_run_id"]
    fsid = _fsid(client, rid)
    did = str(uuid.uuid4())
    sub_sid = f"{fsid}:inspect-{did[:8]}"

    # main：agent（含 agent 层输入输出）+ 2×llm + 1×tool（dispatch）
    _insert_span(user_id="0026demo01", agent_run_id=rid, session_id=fsid, agent_role="main",
                 kind="agent", agent_name="sre-rca",
                 input_messages="用户问题：支付超时", output_messages="结论：网关抖动")
    _insert_span(user_id="0026demo01", agent_run_id=rid, session_id=fsid, agent_role="main",
                 kind="llm", model="glm-5.1", provider="zhipu", input_tokens=100, output_tokens=40,
                 latency_ms=2000.0, input_messages="[msgs-1]", output_messages="[out-1]",
                 finish_reason="tool_calls", span_status="OK")
    _insert_span(user_id="0026demo01", agent_run_id=rid, session_id=fsid, agent_role="main",
                 kind="tool", tool_name="dispatch_subagents", tool_args='{"agents":["inspect"]}',
                 tool_result="已派发", latency_ms=5000.0, span_status="OK")
    _insert_span(user_id="0026demo01", agent_run_id=rid, session_id=fsid, agent_role="main",
                 kind="llm", model="glm-5.1", input_tokens=60, output_tokens=20, latency_ms=1500.0)
    # sub：agent + llm（换一个模型，验 rollup.models 去重并集）
    _insert_span(user_id="0026demo01", agent_run_id=rid, session_id=sub_sid, agent_role="inspect",
                 kind="agent", agent_name="sre-inspect")
    _insert_span(user_id="0026demo01", agent_run_id=rid, session_id=sub_sid, agent_role="inspect",
                 kind="llm", model="qwen-max", input_tokens=30, output_tokens=10, latency_ms=800.0)
    _insert_delegation(rid, did, "inspect", "查一下指标", "指标正常")

    data = unwrap(client.get(f"/api/openops/v1/admin/studio/runs/{rid}", headers=ADMIN_HEADERS))
    assert data["run"]["agent_run_id"] == rid and data["run"]["user_id"] == "0026demo01"

    agents = data["agents"]
    assert len(agents) == 2
    main, sub = agents
    assert main["is_main"] and main["role"] == "main" and main["agent_name"] == "sre-rca"
    assert main["agent_io"] == {"input": "用户问题：支付超时", "output": "结论：网关抖动"}
    assert [c["kind"] for c in main["calls"]] == ["llm", "tool", "llm"]  # agent 类不进 calls
    assert main["calls"][0]["input"] == "[msgs-1]" and main["calls"][0]["output"] == "[out-1]"
    assert main["totals"] == {"llm_calls": 2, "tool_calls": 1, "input_tokens": 160,
                              "output_tokens": 60, "total_tokens": 220, "latency_ms": 8500.0}
    assert not sub["is_main"] and sub["role"] == "inspect"
    assert sub["handover"]["task_text"] == "查一下指标"
    assert sub["handover"]["report_text"] == "指标正常"
    assert sub["handover"]["delegation_status"] == "completed"

    rollup = data["rollup"]
    assert rollup["agents"] == 2 and rollup["llm_calls"] == 3 and rollup["tool_calls"] == 1
    assert rollup["total_tokens"] == 260
    assert rollup["models"] == ["glm-5.1", "qwen-max"]
    assert len(data["delegations"]) == 1


def test_run_messages_admin_only(client):
    rid = str(uuid.uuid4())
    for headers in (USER_HEADERS, OTHER_HEADERS):
        r = client.get(f"/api/openops/v1/admin/studio/runs/{rid}/messages", headers=headers)
        assert r.status_code == 403, r.text
    assert client.get(f"/api/openops/v1/admin/studio/runs/{rid}/messages").status_code == 401


def test_run_messages_transcript_and_soft_deleted(client):
    import asyncio

    from infra.repositories import agent_session_states, runs

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    rid = run["agent_run_id"]

    # 无 state（mock runtime 不落对话）→ 200 空数组
    assert unwrap(client.get(f"/api/openops/v1/admin/studio/runs/{rid}/messages",
                             headers=ADMIN_HEADERS)) == []

    long_answer = "根因分析结论：" + "网关连接池配置回退。" * 800  # 长文本不得截断（≫ span 表 8000 cap 的语义差异）

    async def seed() -> None:
        r = await runs.get_run(rid)
        await agent_session_states.upsert_state_json(str(r["framework_session_id"]), {"context": [
            {"id": "m1", "role": "user", "content": [{"type": "text", "text": "支付超时帮我查"}]},
            {"role": "assistant", "content": [
                {"type": "tool_call", "id": "c1", "name": "dispatch_subagents", "input": "{}"},  # 工具噪声滤掉
                {"type": "text", "text": "正在派发子 Agent 排查"}]},
            {"id": "m3", "role": "user", "content": [{"type": "text", "text": "10 点开始的"}]},
            {"id": "m4", "role": "assistant", "content": [{"type": "text", "text": long_answer}]},
        ]}, "main")

    asyncio.run(seed())
    got = unwrap(client.get(f"/api/openops/v1/admin/studio/runs/{rid}/messages", headers=ADMIN_HEADERS))
    assert [m["role"] for m in got] == ["user", "assistant", "user", "assistant"]
    assert got[0] == {"id": "m1", "role": "user", "content": "支付超时帮我查"}
    assert got[1]["content"] == "正在派发子 Agent 排查"  # tool_call block 不进对话
    assert got[3]["content"] == long_answer  # 全文无截断

    # 与用户端 /messages 同投影（管理员看到的 = 用户看到的）
    own = unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/messages", headers=USER_HEADERS))
    assert own == got

    # 软删后：owner 端 404，管理员端仍可查（复盘不受用户删会话影响）
    unwrap(client.post(f"/api/openops/v1/agent-runs/{rid}:delete", headers=USER_HEADERS, json={}))
    assert client.get(f"/api/openops/v1/agent-runs/{rid}/messages",
                      headers=USER_HEADERS).status_code == 404
    assert unwrap(client.get(f"/api/openops/v1/admin/studio/runs/{rid}/messages",
                             headers=ADMIN_HEADERS)) == got

    r = client.get(f"/api/openops/v1/admin/studio/runs/{uuid.uuid4()}/messages", headers=ADMIN_HEADERS)
    assert r.status_code == 404, r.text


def test_deleted_run_still_visible_and_unknown_404(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    rid = run["agent_run_id"]
    _insert_span(user_id="0026demo01", agent_run_id=rid, session_id="s", agent_role="main", kind="llm")
    unwrap(client.post(f"/api/openops/v1/agent-runs/{rid}:delete", headers=USER_HEADERS, json={}))

    data = unwrap(client.get(f"/api/openops/v1/admin/studio/runs/{rid}", headers=ADMIN_HEADERS))
    assert data["run"]["deleted"] is True and len(data["agents"]) == 1
    listed = unwrap(client.get("/api/openops/v1/admin/studio/runs",
                               params={"user_id": "0026demo01"}, headers=ADMIN_HEADERS))
    assert listed["items"][0]["deleted"] is True  # 软删 run 对管理员复盘仍可见

    r = client.get(f"/api/openops/v1/admin/studio/runs/{uuid.uuid4()}", headers=ADMIN_HEADERS)
    assert r.status_code == 404, r.text
