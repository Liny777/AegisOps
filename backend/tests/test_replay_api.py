"""/agent-runs/{id}/replay：用户自查回放（owner-only；LLM 输入不下发，管理员版不受影响）。"""
from __future__ import annotations

import uuid

from conftest import ADMIN_HEADERS, OTHER_HEADERS, USER_HEADERS, create_instance, create_run, unwrap
from test_admin_studio_api import _fsid, _insert_delegation, _insert_span


def _seed_run_with_spans(client) -> tuple[str, str]:
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    rid = run["agent_run_id"]
    fsid = _fsid(client, rid)
    did = str(uuid.uuid4())
    sub_sid = f"{fsid}:inspect-{did[:8]}"
    _insert_span(user_id="0026demo01", agent_run_id=rid, session_id=fsid, agent_role="main",
                 kind="agent", agent_name="sre-rca", input_messages="用户问题", output_messages="最终结论")
    _insert_span(user_id="0026demo01", agent_run_id=rid, session_id=fsid, agent_role="main",
                 kind="llm", model="glm-5.1", provider="zhipu", input_tokens=100, output_tokens=40,
                 latency_ms=2000.0, input_messages='[{"role":"system","content":"平台提示词"}]',
                 output_messages="[out-1]", finish_reason="stop", span_status="OK")
    _insert_span(user_id="0026demo01", agent_run_id=rid, session_id=fsid, agent_role="main",
                 kind="tool", tool_name="query_logs", tool_args='{"q":1}', tool_result="ok",
                 latency_ms=300.0, span_status="OK")
    _insert_span(user_id="0026demo01", agent_run_id=rid, session_id=sub_sid, agent_role="inspect",
                 kind="llm", model="qwen-max", input_tokens=30, output_tokens=10,
                 input_messages="[sub-in]", output_messages="[sub-out]")
    _insert_delegation(rid, did, "inspect", "查一下指标", "指标正常")
    return rid, fsid


def test_replay_owner_sees_all_but_llm_input(client):
    rid, _ = _seed_run_with_spans(client)
    data = unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/replay", headers=USER_HEADERS))

    assert data["run"]["agent_run_id"] == rid
    main, sub = data["agents"]
    assert main["is_main"] and main["agent_name"] == "sre-rca"
    assert main["agent_io"] == {"input": "用户问题", "output": "最终结论"}  # agent 层 IO 是用户自己的内容，保留
    llm = main["calls"][0]
    assert llm["kind"] == "llm" and llm["input"] == ""  # 平台提示词不下发
    assert llm["output"] == "[out-1]" and llm["input_tokens"] == 100  # 输出与指标全量
    tool = main["calls"][1]
    assert tool["tool_args"] == '{"q":1}' and tool["tool_result"] == "ok"  # 工具入参结果全量
    assert sub["calls"][0]["input"] == "" and sub["calls"][0]["output"] == "[sub-out]"
    assert sub["handover"]["task_text"] == "查一下指标"  # 交接全量
    assert data["rollup"]["llm_calls"] == 2 and data["rollup"]["tool_calls"] == 1

    # 同一 run 管理员版不受影响：LLM 输入仍全量
    admin = unwrap(client.get(f"/api/openops/v1/admin/studio/runs/{rid}", headers=ADMIN_HEADERS))
    assert admin["agents"][0]["calls"][0]["input"] == '[{"role":"system","content":"平台提示词"}]'


def test_replay_isolation_and_lifecycle(client):
    rid, _ = _seed_run_with_spans(client)
    # 他人 403（每人只看自己的）；无头 401；未知 404
    assert client.get(f"/api/openops/v1/agent-runs/{rid}/replay", headers=OTHER_HEADERS).status_code == 403
    assert client.get(f"/api/openops/v1/agent-runs/{rid}/replay").status_code == 401
    assert client.get(f"/api/openops/v1/agent-runs/{uuid.uuid4()}/replay",
                      headers=USER_HEADERS).status_code == 404
    # 管理员不是 owner：回放端点同样 403（管理员走 /admin/studio，不共用本口）
    assert client.get(f"/api/openops/v1/agent-runs/{rid}/replay", headers=ADMIN_HEADERS).status_code == 403

    # 软删后 owner 404（与 /messages 口径一致）；管理员端仍可查
    unwrap(client.post(f"/api/openops/v1/agent-runs/{rid}:delete", headers=USER_HEADERS, json={}))
    assert client.get(f"/api/openops/v1/agent-runs/{rid}/replay", headers=USER_HEADERS).status_code == 404
    assert unwrap(client.get(f"/api/openops/v1/admin/studio/runs/{rid}",
                             headers=ADMIN_HEADERS))["run"]["deleted"] is True
