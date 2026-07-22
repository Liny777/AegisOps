from __future__ import annotations

import asyncio
import json

from conftest import OTHER_HEADERS, USER_HEADERS, create_instance, create_run, unwrap


def test_emit_reuses_audit_id_and_applies_template_tool_label(client):
    from infra.repositories import audit, runs as runs_repo
    from runtime import events
    from runtime.emit import emit
    from runtime.task_registry import TaskState

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    run_row = asyncio.run(runs_repo.get_run(run["agent_run_id"]))
    assert run_row is not None
    st = TaskState(task_id="tsk_event_id", run_id=run["agent_run_id"], user_id="0026demo01",
                   instance_id=instance["instance_id"], input_text="x")
    st.activity_tool_labels = {"query_alarm": "查询告警"}

    asyncio.run(emit(
        st, run_row, "openops.tool.call.started", action="mcp__alarm__query_alarm",
        message="开始查询", payload={"tool": "mcp__alarm__query_alarm"},
    ))
    live = events.snapshot(st.run_id)[-1]
    stored = asyncio.run(audit.list_by_run(st.run_id, limit=1))[-1]
    assert live["event_id"] == str(stored["audit_event_id"])
    assert stored["payload_redacted_json"]["display_label"] == "查询告警"
    assert stored["payload_redacted_json"]["summary"] == "开始查询"


def test_events_cursor_returns_latest_pages_and_enforces_owner(client):
    from infra.repositories import audit

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    rid = run["agent_run_id"]

    async def _seed() -> None:
        for i in range(205):
            await audit.insert_event(
                audit_trace_id=str(run["audit_trace_id"]), event_type="test.activity",
                user_id="0026demo01", run_id=rid, instance_id=instance["instance_id"],
                action=f"marker-{i:03d}", payload_redacted={"summary": f"marker-{i:03d}"},
            )

    asyncio.run(_seed())

    first = unwrap(client.get(
        f"/api/openops/v1/agent-runs/{rid}/events?limit=50", headers=USER_HEADERS))
    assert len(first["items"]) == 50 and first["has_more"] is True
    assert first["next_cursor"] == first["items"][0]["audit_event_id"]
    assert all(e["event_id"] == e["audit_event_id"] for e in first["items"])
    assert first["items"][-1]["action"] == "marker-204"
    assert [e["occurred_at"] for e in first["items"]] == sorted(
        e["occurred_at"] for e in first["items"])

    second = unwrap(client.get(
        f"/api/openops/v1/agent-runs/{rid}/events",
        headers=USER_HEADERS,
        params={"limit": 50, "before": first["next_cursor"]},
    ))
    assert len(second["items"]) == 50
    assert not ({e["audit_event_id"] for e in first["items"]}
                & {e["audit_event_id"] for e in second["items"]})
    assert second["items"][-1]["action"] == "marker-154"

    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{rid}/state", headers=USER_HEADERS))
    assert len(state["recent_events"]) == 100
    assert state["recent_events"][-1]["action"] == "marker-204"
    assert state["events_has_more"] is True and state["events_next_cursor"]

    # 兼容审计 API 也应返回最新 N，而不是长会话最早 N。
    legacy = unwrap(client.get(f"/api/openops/v1/audit/runs/{rid}", headers=USER_HEADERS))
    assert legacy[-1]["action"] == "marker-204"
    assert all(e["event_id"] == e["audit_event_id"] for e in legacy)

    assert client.get(f"/api/openops/v1/agent-runs/{rid}/events", headers=OTHER_HEADERS).status_code == 403
    assert client.get(
        f"/api/openops/v1/agent-runs/{rid}/events?before=not-a-uuid", headers=USER_HEADERS).status_code == 400
    assert client.get(
        f"/api/openops/v1/agent-runs/{rid}/events?limit=201", headers=USER_HEADERS).status_code == 422


def test_activity_payload_is_allowlisted_and_credentials_are_redacted(client):
    from infra.redact import redact_text
    from infra.repositories import audit, runs as runs_repo
    from runtime import events
    from runtime.emit import emit
    from runtime.task_registry import TaskState

    cookie_header = "".join(("Cook", "ie", ": "))
    samples = [
        "password=hunter2-value",
        cookie_header + "session=cookie-value",
        "Authorization: Basic YWxhZGRpbjpvcGVuc2VzYW1l",
        "authorization: bearer lower-case-token",
        '{"password": "quoted-json-password"}',
        "https://user:url-password@example.test/path",
    ]
    flattened = "\n".join(redact_text(item, max_length=500) for item in samples)
    for secret in ("hunter2-value", "cookie-value", "YWxhZGRpbj", "lower-case-token",
                   "quoted-json-password", "url-password"):
        assert secret not in flattened

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    run_row = asyncio.run(runs_repo.get_run(run["agent_run_id"]))
    assert run_row is not None
    st = TaskState(task_id="tsk_activity_security", run_id=run["agent_run_id"], user_id="0026demo01",
                   instance_id=instance["instance_id"], input_text="x")

    asyncio.run(emit(
        st, run_row, "openops.tool.call.started", action="query_secret",
        message="调用工具", payload={
            "tool": "query_secret",
            "arguments": {
                "password": "payload-password-value",
                "note": "Authorization: Basic cGF5bG9hZC1iYXNpYw==",
                "appid": "APP-A",
            },
        },
    ))
    asyncio.run(emit(
        st, run_row, "openops.skill.call.succeeded", action="inspection",
        message="Skill 完成", payload={
            "skill": "inspection",
            "result": {"status": "success", "token": "result-token-value"},
            "stdout": cookie_header + "sid=stdout-cookie-value\nfull output must not survive",
        },
    ))

    stored = asyncio.run(audit.list_by_run(st.run_id, limit=10))
    live = events.snapshot(st.run_id)
    for collection in (stored, live):
        encoded = json.dumps(collection, ensure_ascii=False, default=str)
        for secret in ("payload-password-value", "cGF5bG9hZC", "result-token-value", "stdout-cookie-value"):
            assert secret not in encoded
        assert '"stdout"' not in encoded
        tool = next(item for item in collection
                    if str(item.get("event_type")) == "openops.tool.call.started")
        assert set(tool["payload_redacted_json"]["arguments"]) == {"summary"}
        assert len(tool["payload_redacted_json"]["argument_summary"]) <= 300

    # 模拟迁移前已经存在的旧 raw 审计行：公开活动接口仍须做二次安全投影。
    asyncio.run(audit.insert_event(
        audit_trace_id=str(run["audit_trace_id"]), event_type="openops.sandbox.command.executed",
        user_id="0026demo01", run_id=st.run_id, instance_id=instance["instance_id"],
        action="bash", payload_redacted={
            "command": "echo token=legacy-token-value",
            "stdout": "Authorization: Basic bGVnYWN5LWJhc2ljLXZhbHVl",
            "stderr": cookie_header + "sid=legacy-cookie-value",
        },
    ))
    state = unwrap(client.get(
        f"/api/openops/v1/agent-runs/{st.run_id}/state", headers=USER_HEADERS))
    page = unwrap(client.get(
        f"/api/openops/v1/agent-runs/{st.run_id}/events", headers=USER_HEADERS))
    exposed = json.dumps({"state": state["recent_events"], "page": page["items"]}, ensure_ascii=False)
    for secret in ("legacy-token-value", "bGVnYWN5", "legacy-cookie-value"):
        assert secret not in exposed
    assert '"stdout"' not in exposed and '"stderr"' not in exposed


def test_rca_updated_payload_keeps_status_and_steps_strips_unknown_and_redacts():
    """rca.updated 白名单：status（完成态判定的后端权威信号）与 steps 保留；未知键剥除；凭证打码。"""
    from infra.redact import sanitize_activity_payload

    payload = {
        "revision": 4,
        "title": "支付下单 P99 突增",
        "status": "concluded",
        "phaseLabel": "已定界",
        "board_task_id": "tsk_leader",
        "steps": [{"num": 5, "label": "结论", "state": "done", "evil": "<script>alert(1)</script>"}],
        "conclusion": "根因 H1；token=super-secret-value",
        "facts": [{"text": "P99 1.4s", "password": "hunter2-value"}],
        "internal_note": "must-not-leak",
        "rca_source": "model",
    }
    out = sanitize_activity_payload("openops.rca.updated", payload)

    assert out["status"] == "concluded" and out["revision"] == 4 and out["phaseLabel"] == "已定界"
    assert out["board_task_id"] == "tsk_leader"  # 前端 revision 分段键（owner 身份）必须透传
    # 列表项按字段白名单裁剪：未知键（含可执行内容）不落地
    assert out["steps"] == [{"num": 5, "label": "结论", "state": "done"}]
    assert out["facts"] == [{"text": "P99 1.4s"}]
    encoded = json.dumps(out, ensure_ascii=False)
    for leaked in ("internal_note", "must-not-leak", "rca_source", "hunter2-value",
                   "super-secret-value", "<script>"):
        assert leaked not in encoded
    assert "token=[REDACTED]" in out["conclusion"]  # 凭证样式文本打码后保留语义

    # status 仅是白名单标量，非法类型（对象注入）不透传
    weird = sanitize_activity_payload("openops.rca.updated", {"status": {"nested": "x"}, "revision": 1})
    assert "status" not in weird and weird["revision"] == 1


def test_restart_can_restore_delegation_context_for_approval_events(client):
    from infra.repositories import delegations
    from runtime.emit import activity_context_of_task

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    did = asyncio.run(delegations.create(
        run["agent_run_id"], "tsk_leader", "recover", "恢复服务", 300, "0026demo01",
        dispatch_batch_id="5f02c74f-a694-4e6a-bcea-e447da4cdd66", dispatch_batch_no=7,
    ))
    ctx = asyncio.run(activity_context_of_task(f"tsk_leader.recover-{did[:8]}"))
    assert ctx == {
        "agent_key": "recover",
        "agent_label": "recover",
        "leader_task_id": "tsk_leader",
        "child_task_id": f"tsk_leader.recover-{did[:8]}",
        "delegation_id": did,
        "dispatch_batch_id": "5f02c74f-a694-4e6a-bcea-e447da4cdd66",
        "dispatch_batch_no": 7,
    }
