from __future__ import annotations

import time

from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, create_run, unwrap
from test_assets import _assert_recover_blocked, _bind_skill, _upload_skill


def _template_id(client) -> str:
    rows = unwrap(client.get("/api/openops/v1/admin/templates", headers=ADMIN_HEADERS))
    return str(rows[0]["template_id"])


def _detail(client, template_id: str) -> dict:
    return unwrap(client.get(f"/api/openops/v1/admin/templates/{template_id}", headers=ADMIN_HEADERS))


def _content(default_tools: list[str], role: str = "理解用户任务，调度巡检/诊断/恢复能力。") -> dict:
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


def test_template_draft_scrubs_stale_tools(client):
    """ADMIN-002（自愈翻案）+ 复合键迁移：保存时解析不到 allowed 标注的平台 tool（已删 MCP server /
    失效存量绑定）被自动摘除、不再硬报错；被摘名字回传 dropped_tools；allowed 的裸名**升级为
    「server::tool」复合键**（存量模板保存一次即迁移）。main.default_tools 与 sub.mcp_tools 两处
    同规则。草稿 upsert 不涨版本号。"""
    tid = _template_id(client)
    content = _content(["query_resource", "no_such_tool"])
    content["sub_agents"][0]["mcp_tools"] = ["recover_execute", "ghost_tool"]
    d1 = unwrap(_save_draft(client, tid, content))
    assert d1["status"] == "draft" and d1["version_no"] == 2
    # 幽灵摘除；allowed 裸名升级为 seed 占位 server 的复合键
    assert d1["content_json"]["main"]["default_tools"] == ["oModel 查询与恢复::query_resource"]
    assert d1["content_json"]["sub_agents"][0]["mcp_tools"] == ["oModel 查询与恢复::recover_execute"]
    assert set(d1["dropped_tools"]) == {"no_such_tool", "ghost_tool"}

    d2 = unwrap(_save_draft(client, tid, _content(["query_resource"])))
    assert d2["version_no"] == 2  # upsert 语义：同一草稿反复改
    assert str(d2["template_version_id"]) == str(d1["template_version_id"])
    assert d2["dropped_tools"] == []  # 纯 allowed 无失效工具
    # 复合键条目再保存幂等（不重复展开/不再改写）
    d3 = unwrap(_save_draft(client, tid, _content(["oModel 查询与恢复::query_resource"])))
    assert d3["content_json"]["main"]["default_tools"] == ["oModel 查询与恢复::query_resource"]
    assert d3["dropped_tools"] == []


def test_template_activity_tool_labels_are_optional_string_mapping(client):
    tid = _template_id(client)
    content = _content(["query_resource"])
    content["activity_labels"] = {"tools": {"query_resource": "查询资源", "query_alarm": "查询告警"}}
    saved = unwrap(_save_draft(client, tid, content))
    assert saved["content_json"]["activity_labels"]["tools"]["query_resource"] == "查询资源"

    invalid = _content(["query_resource"])
    invalid["activity_labels"] = {"tools": {"query_resource": 123}}
    response = _save_draft(client, tid, invalid)
    assert response.status_code == 400
    assert "activity_labels.tools" in response.json()["error"]["message"]


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


def test_template_publish_scrubs_now_blocked_tool(client):
    """B7-TEST-001①（自愈翻案）：草稿期 allowed、发布前被 block 的 tool——发布不再 400，而是自动摘除该
    工具后发布干净版本（dropped_tools 含之，发布版 default_tools 不含之）。"""
    tid = _template_id(client)
    draft = unwrap(_save_draft(client, tid, _content(["query_resource", "recover_execute"])))
    catalog = unwrap(client.get("/api/openops/v1/admin/mcp-tools", headers=ADMIN_HEADERS))
    rec = next(t for t in catalog if t["tool_name"] == "recover_execute")
    unwrap(client.put(
        f"/api/openops/v1/admin/mcp-tools/{rec['tool_catalog_id']}/annotation", headers=ADMIN_HEADERS,
        json={"is_approval_required": True, "is_secret_required": False, "scope_mode": "required",
              "appid_arg_path": "$.appid", "status": "blocked", "blocked_reason": "发布前冻结"},
    ))
    pub = unwrap(_publish(client, str(draft["template_version_id"])))
    assert pub["status"] == "active"
    # 草稿保存时裸名已升级为复合键，故 dropped 里是复合键形态
    assert "oModel 查询与恢复::recover_execute" in pub["dropped_tools"]
    assert pub["content_json"]["main"]["default_tools"] == ["oModel 查询与恢复::query_resource"]  # 发布版已摘 blocked 工具


def test_delete_mcp_cascades_scrub_template_draft(client):
    """删平台 MCP server 级联清理：删掉后，引用其工具的模板 draft 绑定被自动摘掉（published 不可变、不动）。"""
    import asyncio

    from app import asset_registry_service
    from infra.repositories import assets, templates

    tid = _template_id(client)
    draft = unwrap(_save_draft(client, tid, _content(["query_resource"])))
    dvid = str(draft["template_version_id"])
    assert draft["content_json"]["main"]["default_tools"] == ["oModel 查询与恢复::query_resource"]  # 保存即迁移

    async def scenario() -> list[str]:
        mcp_id = None
        for m in await assets.list_platform_mcps():  # seed 平台 MCP「oModel 查询与恢复」含 query_resource
            if "query_resource" in await assets.tool_names_for_mcp(str(m["mcp_id"])):
                mcp_id = str(m["mcp_id"])
                break
        assert mcp_id is not None
        await asset_registry_service.delete_mcp({"user_id": "admin"}, mcp_id)
        v = await templates.get_version(dvid)
        return v["content_json"]["main"]["default_tools"]

    assert asyncio.run(scenario()) == []  # 该 server 的复合键绑定随 server 删除被级联摘掉


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

def test_omodel_console_page_env_matrix(client, monkeypatch):
    """设置页 iframe 前缀下发：未配置→空串；固定 BASE_URL 派生；安全 PAGE_URL 覆盖优先。"""
    from conftest import USER_HEADERS, unwrap

    monkeypatch.delenv("OPENOPS_OMODEL_BASE_URL", raising=False)
    monkeypatch.delenv("OPENOPS_OMODEL_PAGE_URL", raising=False)
    out = unwrap(client.get("/api/openops/v1/omodel/console-page", headers=USER_HEADERS))
    assert out["page_base"] == ""

    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "https://console.x.y/omodel")
    out = unwrap(client.get("/api/openops/v1/omodel/console-page", headers=USER_HEADERS))
    assert out["page_base"] == "https://console.x.y/wesee/omodel/index.html?dataSource=api&workspace="

    monkeypatch.setenv("OPENOPS_OMODEL_PAGE_URL", "https://other.z/custom/page?ws=")
    out = unwrap(client.get("/api/openops/v1/omodel/console-page", headers=USER_HEADERS))
    assert out["page_base"] == "https://other.z/custom/page?ws="

    monkeypatch.setenv("OPENOPS_OMODEL_PAGE_URL", "https://user:secret@other.z/custom/page?ws=")
    out = unwrap(client.get("/api/openops/v1/omodel/console-page", headers=USER_HEADERS))
    assert out["page_base"] == ""

    for unsafe_page in (
        "https://{host}/custom/page?ws=",
        "https://%7Bhost%7D/custom/page?ws=",
        "https://other.z/custom/{workspace}?ws=",
        "https://other.z/custom/page?ws=#fragment",
        "https://other.z\\@attacker.example/custom/page?ws=",
    ):
        monkeypatch.setenv("OPENOPS_OMODEL_PAGE_URL", unsafe_page)
        out = unwrap(client.get("/api/openops/v1/omodel/console-page", headers=USER_HEADERS))
        assert out["page_base"] == ""

    monkeypatch.delenv("OPENOPS_OMODEL_PAGE_URL")
    monkeypatch.setenv("OPENOPS_OMODEL_BASE_URL", "https://user:secret@console.x.y/omodel")
    out = unwrap(client.get("/api/openops/v1/omodel/console-page", headers=USER_HEADERS))
    assert out["page_base"] == ""


def test_workspace_statistics_endpoint(client):
    """看护空间统计四数（Agent 初始化「确认能力清单」页）：mock 后端返回样例四键，恒有整数、不缺键。"""
    from conftest import USER_HEADERS, unwrap

    out = unwrap(client.get("/api/openops/v1/workspaces/ws_pay_abc/statistics", headers=USER_HEADERS))
    assert set(out) == {"node_count", "relation_count", "node_type_count", "relation_type_count"}
    assert all(isinstance(out[k], int) for k in out)
    assert out == {"node_count": 3116, "relation_count": 2246, "node_type_count": 37, "relation_type_count": 5}


# ---- 编排对称化：main.skills 白名单（模板编辑器主 Agent 技能面） ----

def test_main_skills_validation(client):
    """main.skills 类型校验：非字符串数组 400；合法数组可保存（skills 无标注门禁）。"""
    tid = _template_id(client)
    bad = _save_draft(client, tid, {**_content(["query_resource"]),
                                    "main": {"role": "编排", "default_tools": ["query_resource"], "skills": "oops"}})
    assert bad.status_code == 400
    assert "main.skills" in bad.json()["error"]["message"]
    ok = _save_draft(client, tid, {**_content(["query_resource"]),
                                   "main": {"role": "编排", "default_tools": ["query_resource"],
                                            "skills": ["inspection"]}})
    assert ok.status_code == 200


def test_filter_main_skills_semantics():
    """filter_main_skills：空/缺省/非法类型=不过滤（存量模板兼容+编辑器往返安全）；非空=交集。"""
    from app.run_state_service import filter_main_skills

    sk = {"a": {"display_name": "A"}, "b": {"display_name": "B"}}
    assert filter_main_skills(sk, {}) == sk                                   # main 缺省
    assert filter_main_skills(sk, {"main": {}}) == sk                         # skills 缺省
    assert filter_main_skills(sk, {"main": {"skills": []}}) == sk             # 空表=不限
    assert filter_main_skills(sk, {"main": {"skills": "oops"}}) == sk         # 非法类型忽略
    assert filter_main_skills(sk, {"main": {"skills": ["a", "ghost"]}}) == {"a": sk["a"]}  # 交集
    # 用户个人 skill 豁免 main.skills 白名单；平台 skill 仍受收窄
    mixed = {"u": {"source_type": "user"}, "p": {"source_type": "platform"}}
    assert filter_main_skills(mixed, {"main": {"skills": ["p"]}}) == mixed              # p 命中、u 豁免 → 都在
    assert filter_main_skills(mixed, {"main": {"skills": ["x"]}}) == {"u": mixed["u"]}  # p 被收窄、u 豁免


def test_main_skills_whitelist_exempts_user_skills(client):
    """模板 main.skills 白名单只收窄平台 skill；用户个人 skill 豁免（绑定/自动挂载即可用，Agent 运行时可见）。"""
    # 基线实例（seed 模板 skills=[]=不限）：绑两个用户 Skill，学到真实 skill_key
    inst_a = create_instance(client, name="技能面基线")
    alpha, beta = _upload_skill(client, "skill-alpha"), _upload_skill(client, "skill-beta")
    _bind_skill(client, inst_a["instance_id"], alpha)
    _bind_skill(client, inst_a["instance_id"], beta)
    base = unwrap(client.get(f"/api/openops/v1/agent-teams/{inst_a['instance_id']}/available-skills",
                             headers=USER_HEADERS))
    ka = next(x["skill_key"] for x in base if x["display_name"] == "skill-alpha")
    kb = next(x["skill_key"] for x in base if x["display_name"] == "skill-beta")
    # 发布 main.skills=[ka]（只列 alpha）的新版本 → 新实例（钉新版）→ 两个用户 skill 都在（豁免白名单）
    tid = _template_id(client)
    ver = unwrap(_save_draft(client, tid, {**_content(["query_resource"]),
                                           "main": {"role": "编排", "default_tools": ["query_resource"],
                                                    "skills": [ka]}}))
    assert _publish(client, str(ver["template_version_id"])).status_code == 200
    inst_b = create_instance(client, name="技能面白名单")
    _bind_skill(client, inst_b["instance_id"], alpha)
    _bind_skill(client, inst_b["instance_id"], beta)
    out = unwrap(client.get(f"/api/openops/v1/agent-teams/{inst_b['instance_id']}/available-skills",
                            headers=USER_HEADERS))
    keys = {x["skill_key"] for x in out}
    assert ka in keys and kb in keys  # 用户 skill 豁免 main.skills 白名单（改动前 kb 会被误收窄）


# ---- 管理员代查通道：手输 APPID 创建系统范围（必填原因 + 审计） ----

def _admin_audit_events(client, event_type: str) -> list[dict]:
    rows = unwrap(client.get("/api/openops/v1/admin/audit/recent", headers=ADMIN_HEADERS))
    return [e for e in rows if e["event_type"] == event_type]


def test_workspace_manual_appid_creates_and_audits(client):
    """手输 APPID（未经 apptree 权限过滤）创建成功；写 workspace.admin_created 审计，
    管理台 recent 投影可见 manual_app_ids/app_ids/name/workspace_id 与 reason_summary。"""
    out = unwrap(client.post("/api/openops/v1/workspaces", headers=ADMIN_HEADERS, json={
        "client_request_id": "manual-1", "name": "代查-支付故障",
        "app_ids": ["APP-NOPERM-01"], "apps": [{"app_id": "APP-NOPERM-01"}],
        "manual_app_ids": ["APP-NOPERM-01"], "reason": "工单 T-20260720-001 复现用户上报",
    }))
    ws_id = out["workspace_id"]
    detail = unwrap(client.get(f"/api/openops/v1/workspaces/{ws_id}", headers=ADMIN_HEADERS))
    assert detail["app_ids"] == ["APP-NOPERM-01"]

    events = _admin_audit_events(client, "workspace.admin_created")
    assert len(events) == 1
    ev = events[0]
    assert ev["user_id"] == "admin" and ev["actor_type"] == "user"
    payload = ev["payload_redacted_json"]
    assert payload["workspace_id"] == ws_id
    assert payload["manual_app_ids"] == ["APP-NOPERM-01"]
    assert payload["app_ids"] == ["APP-NOPERM-01"]
    assert payload["name"] == "代查-支付故障"
    assert "T-20260720-001" in payload["reason_summary"]


def test_workspace_manual_appid_requires_reason(client):
    """manual_app_ids 非空但 reason 空白 → 400 VALIDATION_FAILED，且不落 workspace、不写审计。"""
    before = unwrap(client.get("/api/openops/v1/workspaces", headers=ADMIN_HEADERS))
    r = client.post("/api/openops/v1/workspaces", headers=ADMIN_HEADERS, json={
        "client_request_id": "manual-2", "name": "缺原因",
        "app_ids": ["APP-X"], "manual_app_ids": ["APP-X"], "reason": "   ",
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert len(unwrap(client.get("/api/openops/v1/workspaces", headers=ADMIN_HEADERS))) == len(before)
    assert _admin_audit_events(client, "workspace.admin_created") == []


def test_workspace_normal_create_unchanged_no_admin_audit(client):
    """普通创建（不带新字段）行为零变化：成功且不产生 workspace.admin_created 事件。"""
    out = unwrap(client.post("/api/openops/v1/workspaces", headers=USER_HEADERS, json={
        "client_request_id": "normal-1", "name": "普通范围", "app_ids": ["APP-A"],
    }))
    assert out["workspace_id"]
    assert _admin_audit_events(client, "workspace.admin_created") == []


def test_workspace_manual_appid_no_audit_on_omodel_failure(client, monkeypatch):
    """oModel 创建失败（上游 5xx）→ 报错透传映射，且不写审计（同 set_role 只审计实际变更）。"""
    from infra.external import omodel_client

    async def _fail(*_args, **_kwargs):
        raise omodel_client.OModelError("upstream", "boom", status_code=503)

    monkeypatch.setattr(omodel_client, "create_workspace", _fail)
    r = client.post("/api/openops/v1/workspaces", headers=ADMIN_HEADERS, json={
        "client_request_id": "manual-3", "name": "失败不留痕",
        "app_ids": ["APP-Y"], "manual_app_ids": ["APP-Y"], "reason": "复现工单 T-2",
    })
    assert r.status_code == 502
    assert _admin_audit_events(client, "workspace.admin_created") == []


def test_workspace_manual_appid_forbidden_for_non_admin(client):
    """手输通道服务端角色校验：普通白名单用户带 manual_app_ids 直调 → 403，
    不创建 workspace、不产生「非管理员的 admin_created」审计（留痕语义完整性）。"""
    before = unwrap(client.get("/api/openops/v1/workspaces", headers=USER_HEADERS))
    r = client.post("/api/openops/v1/workspaces", headers=USER_HEADERS, json={
        "client_request_id": "manual-4", "name": "越权手输",
        "app_ids": ["APP-Z"], "manual_app_ids": ["APP-Z"], "reason": "工单 T-3",
    })
    assert r.status_code == 403
    assert len(unwrap(client.get("/api/openops/v1/workspaces", headers=USER_HEADERS))) == len(before)
    assert _admin_audit_events(client, "workspace.admin_created") == []
