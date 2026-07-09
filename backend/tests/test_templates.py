from __future__ import annotations

import time

from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, create_run, unwrap
from test_assets import _assert_recover_blocked, _bind_skill, _upload_skill


def _template_id(client) -> str:
    rows = unwrap(client.get("/api/openops/v1/admin/templates", headers=ADMIN_HEADERS))
    return str(rows[0]["template_id"])


def _detail(client, template_id: str) -> dict:
    return unwrap(client.get(f"/api/openops/v1/admin/templates/{template_id}", headers=ADMIN_HEADERS))


def _content(default_tools: list[str], role: str = "理解用户任务，调度巡检/定界/恢复能力。") -> dict:
    return {
        "main": {"role": role, "default_tools": default_tools},
        "sub_agents": [{"key": "inspect", "label": "巡检", "role": "巡检"}],
        "default_llm": {"provider": "platform", "model": "qwen3.5-instruct"},
    }


def _save_draft(client, template_id: str, content: dict):
    return client.post(
        f"/api/openops/v1/admin/templates/{template_id}/versions", headers=ADMIN_HEADERS,
        json={"client_request_id": f"tv_{time.time_ns()}", "content_json": content},
    )


def _publish(client, version_id: str):
    return client.post(
        f"/api/openops/v1/admin/template-versions/{version_id}:publish", headers=ADMIN_HEADERS,
        json={"client_request_id": f"pub_{time.time_ns()}"},
    )


def test_template_draft_validates_allowed_tools(client):
    """ADMIN-002：保存校验——未 allowed 标注的平台 tool 不可绑定；草稿 upsert 不涨版本号。"""
    tid = _template_id(client)
    bad = _save_draft(client, tid, _content(["query_resource", "no_such_tool"]))
    assert bad.status_code == 400
    assert "no_such_tool" in bad.json()["error"]["message"]

    d1 = unwrap(_save_draft(client, tid, _content(["query_resource", "recover_execute"])))
    assert d1["status"] == "draft" and d1["version_no"] == 2
    d2 = unwrap(_save_draft(client, tid, _content(["query_resource"])))
    assert d2["version_no"] == 2  # upsert 语义：同一草稿反复改
    assert str(d2["template_version_id"]) == str(d1["template_version_id"])


def test_template_publish_switches_active_and_immutable(client):
    """发布：draft→active、旧 active→archived、模板指针切换；发布后不可再发（不可变）。"""
    tid = _template_id(client)
    draft = unwrap(_save_draft(client, tid, _content(["query_resource", "recover_execute"], role="v2 角色")))
    pub = unwrap(_publish(client, str(draft["template_version_id"])))
    assert pub["status"] == "active" and pub["version_no"] == 2

    d = _detail(client, tid)
    assert str(d["template"]["active_template_version_id"]) == str(draft["template_version_id"])
    assert d["draft_version"] is None  # 草稿已转正
    # 普通用户可实例化的是新版本
    avail = unwrap(client.get("/api/openops/v1/templates/available", headers=USER_HEADERS))
    assert str(avail[0]["template_version_id"]) == str(draft["template_version_id"])
    # 已发布版本不可再次发布（不可原地改）
    again = _publish(client, str(draft["template_version_id"]))
    assert again.status_code == 409


def test_template_disable_active_removes_from_available(client):
    tid = _template_id(client)
    d = _detail(client, tid)
    ver = str(d["template"]["active_template_version_id"])
    unwrap(client.post(f"/api/openops/v1/admin/template-versions/{ver}:disable", headers=ADMIN_HEADERS,
                       json={"client_request_id": "dis1"}))
    avail = unwrap(client.get("/api/openops/v1/templates/available", headers=USER_HEADERS))
    assert all(str(t["template_id"]) != tid or t["template_version_id"] is None for t in avail)


def test_template_upgrade_derives_instance_config(client):
    """28.7 使用时派生（ASSET-003/RUN-006）：模板发布新版本 → 实例下一次任务边界自动派生，保留 overlay 与绑定。"""
    instance = create_instance(client)
    iid = instance["instance_id"]
    # 用户侧改造：绑一个 Skill + main 追加（升级派生必须结转这两样）
    skill = _upload_skill(client, "升级结转 Skill")
    _bind_skill(client, iid, skill)
    unwrap(client.post(
        f"/api/openops/v1/agent-teams/{iid}/config-versions", headers=USER_HEADERS,
        json={"client_request_id": f"cfg_{time.time_ns()}", "overlay_json": {"main_role_append": "多看支付链路"},
              "change_reason": "append"},
    ))
    # 管理员发布模板新版本
    tid = _template_id(client)
    draft = unwrap(_save_draft(client, tid, _content(["query_resource", "recover_execute"], role="升级后的角色")))
    unwrap(_publish(client, str(draft["template_version_id"])))

    # 下一次任务边界触发派生
    run = create_run(client, iid)
    unwrap(client.post(
        f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks", headers=USER_HEADERS,
        json={"client_request_id": f"t{time.time_ns()}", "input_text": "升级后首个任务"},
    ))
    detail = unwrap(client.get(f"/api/openops/v1/agent-teams/{iid}", headers=USER_HEADERS))
    assert str(detail["instance"]["template_version_id"]) == str(draft["template_version_id"])  # 指针已升级
    assert detail["active_config_version"]["change_reason"] == "template upgraded"
    assert detail["active_config_version"]["overlay_json"]["main_role_append"] == "多看支付链路"  # overlay 结转
    bindings = unwrap(client.get(f"/api/openops/v1/agent-teams/{iid}/asset-bindings", headers=USER_HEADERS))
    assert {b["display_name"] for b in bindings} == {"升级结转 Skill"}  # 绑定结转
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    assert any(e["event_type"] == "config.version.derived" for e in events)

    # 幂等：指针已对齐 active 版本，后续边界不会重复派生（_derive_if_template_upgraded 短路）
    versions = unwrap(client.get(f"/api/openops/v1/agent-teams/{iid}/config-versions", headers=USER_HEADERS))
    derived = [v for v in versions if v["change_reason"] == "template upgraded"]
    assert len(derived) == 1


def test_template_tools_enforcement(client, runtime_backend):
    """B7·二：模板未绑定的平台工具即使全局标注 allowed 也 fail-closed（双 runtime）。"""
    tid = _template_id(client)
    draft = unwrap(_save_draft(client, tid, _content(["query_resource"])))  # 模板去掉 recover_execute
    unwrap(_publish(client, str(draft["template_version_id"])))

    instance = create_instance(client)  # 新实例直接落在新模板版本
    run = create_run(client, instance["instance_id"])
    unwrap(client.post(
        f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks", headers=USER_HEADERS,
        json={"client_request_id": f"t{time.time_ns()}", "input_text": "定位"},
    ))
    _assert_recover_blocked(client, run["agent_run_id"], "TOOL_BLOCKED")


def test_template_empty_tools_fail_closed(client, runtime_backend):
    """B7-SEC-001：模板显式 default_tools=[] = 零平台工具——空集不得视为「无限制」（双 runtime）。"""
    tid = _template_id(client)
    draft = unwrap(_save_draft(client, tid, _content([])))
    unwrap(_publish(client, str(draft["template_version_id"])))

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    unwrap(client.post(
        f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks", headers=USER_HEADERS,
        json={"client_request_id": f"t{time.time_ns()}", "input_text": "定位"},
    ))
    _assert_recover_blocked(client, run["agent_run_id"], "TOOL_BLOCKED")
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    # 空模板下任何平台工具都不得成功执行；模板门先于标注热读，也不应产生无意义的 runtime_plan.updated
    assert not any(e["event_type"] == "openops.tool.call.succeeded" for e in events)
    assert not any(e["event_type"] == "openops.runtime_plan.updated" for e in events)


def test_template_publish_revalidates_annotation(client):
    """B7-TEST-001①：草稿期 allowed、发布前被 block 的 tool——发布重校验必须 400 拦下。"""
    tid = _template_id(client)
    draft = unwrap(_save_draft(client, tid, _content(["query_resource", "recover_execute"])))
    catalog = unwrap(client.get("/api/openops/v1/admin/mcp-tools", headers=ADMIN_HEADERS))
    rec = next(t for t in catalog if t["tool_name"] == "recover_execute")
    unwrap(client.put(
        f"/api/openops/v1/admin/mcp-tools/{rec['tool_catalog_id']}/annotation", headers=ADMIN_HEADERS,
        json={"is_approval_required": True, "is_secret_required": False, "scope_mode": "required",
              "appid_arg_path": "$.appid", "status": "blocked", "blocked_reason": "发布前冻结"},
    ))
    resp = _publish(client, str(draft["template_version_id"]))
    assert resp.status_code == 400
    assert "recover_execute" in resp.json()["error"]["message"]


def test_template_write_endpoints_forbidden_for_user(client):
    """B7-TEST-001②：模板写面三端点（存草稿/发布/禁用）普通用户一律 403。"""
    tid = _template_id(client)
    fake_ver = "00000000-0000-0000-0000-000000000000"
    r1 = client.post(f"/api/openops/v1/admin/templates/{tid}/versions", headers=USER_HEADERS,
                     json={"client_request_id": "u1", "content_json": _content(["query_resource"])})
    r2 = client.post(f"/api/openops/v1/admin/template-versions/{fake_ver}:publish", headers=USER_HEADERS,
                     json={"client_request_id": "u2"})
    r3 = client.post(f"/api/openops/v1/admin/template-versions/{fake_ver}:disable", headers=USER_HEADERS,
                     json={"client_request_id": "u3"})
    assert r1.status_code == r2.status_code == r3.status_code == 403