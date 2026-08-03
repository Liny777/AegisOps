"""模型模板（38 号：主/子 Agent 槽位模型编排）全链闭合。

- 管理面 CRUD（重名/未知资产/403/审计）+ 默认原子切换 + `:status` 路由顺序护栏。
- 用户列表 ACL fail-closed：主、子槽位都授权才可见；disable 全员隐身。
- 绑定端到端：overlay.model_template_id → 起任务 → selected_model=主槽 / selected_sub_model=子槽。
- 越权绑定 403、三键互斥 400、编辑换绑涨版本、legacy 回归（selected_sub_model=None）。
- resolve_template_models 纯逻辑（降级三态）+ _child_state 继承 + 降级留痕（redact 白名单护栏）。
"""
from __future__ import annotations

import time

from conftest import ADMIN_HEADERS, OTHER_HEADERS, USER_HEADERS, create_run, unwrap, wait_until

from app import model_gateway


def _assets_by_model_id(client) -> dict[str, dict]:
    rows = unwrap(client.get("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS))
    return {r["model_id"]: r for r in rows}


def _admin_templates(client) -> list[dict]:
    return unwrap(client.get("/api/openops/v1/admin/model-templates", headers=ADMIN_HEADERS))


def _user_templates(client, headers=USER_HEADERS) -> list[dict]:
    return unwrap(client.get("/api/openops/v1/models/templates", headers=headers))


def _create_template(client, name: str, main_asset: str, sub_asset: str, **extra):
    return client.post("/api/openops/v1/admin/model-templates", headers=ADMIN_HEADERS,
                       json={"client_request_id": f"mt_{time.time_ns()}", "display_name": name,
                             "main_model_asset_id": main_asset, "sub_model_asset_id": sub_asset, **extra})


def _create_team(client, headers, name: str, overlay: dict | None):
    templates = unwrap(client.get("/api/openops/v1/templates/available", headers=headers))
    return client.post("/api/openops/v1/agent-teams", headers=headers,
                       json={"client_request_id": f"i_{time.time_ns()}",
                             "template_version_id": templates[0]["template_version_id"],
                             "name": name, "workspace_id": "ws_pay_abc",
                             **({"initial_overlay_json": overlay} if overlay is not None else {})})


def _whitelist_other(client):
    unwrap(client.post("/api/openops/v1/admin/users/whitelist", headers=ADMIN_HEADERS,
                       json={"client_request_id": f"wl_{time.time_ns()}", "user_id": "0099other",
                             "display_name": "Other", "role": "user"}))


# ---- seed 与管理面 CRUD ----

def test_mt_001_seed_templates_present(client):
    """seed 双调用时序：全新库（pytest 库）末尾第二次调用生效——种出「均衡（推荐）默认 + 经济」。"""
    rows = _admin_templates(client)
    by_name = {r["display_name"]: r for r in rows}
    assert "均衡（推荐）" in by_name and "经济" in by_name
    assert by_name["均衡（推荐）"]["is_default"] is True
    assert by_name["经济"]["main_model"]["model_id"] == "glm-5.1"
    assert by_name["经济"]["sub_model"]["model_id"] == "qwen3.5-instruct"
    assert sum(1 for r in rows if r["is_default"]) == 1


def test_mt_002_admin_crud_and_audit(client):
    """创建（嵌套槽位信息）→ PATCH 只改给出键 → 审计事件齐全；重名/未知资产/空 body 400。"""
    assets = _assets_by_model_id(client)
    glm, qwen = assets["glm-5.1"], assets["qwen3.5-instruct"]

    created = unwrap(_create_template(client, "测试组合", glm["model_asset_id"], qwen["model_asset_id"],
                                      description="glm 主 + qwen 子"))
    tid = created["model_template_id"]
    assert created["main_model"]["model_id"] == "glm-5.1"
    assert created["sub_model"]["display_name"] == "Qwen3.5"
    assert created["is_default"] is False

    # 重名 400；未知资产 400
    assert _create_template(client, "测试组合", glm["model_asset_id"], qwen["model_asset_id"]).status_code == 400
    bad = _create_template(client, "坏组合", "00000000-0000-0000-0000-000000000000", qwen["model_asset_id"])
    assert bad.status_code == 400

    # PATCH：只改 sub 槽位，名称/主槽不动
    r = client.put(f"/api/openops/v1/admin/model-templates/{tid}", headers=ADMIN_HEADERS,
                   json={"client_request_id": f"u_{time.time_ns()}", "sub_model_asset_id": glm["model_asset_id"]})
    assert r.status_code == 200
    after = next(t for t in _admin_templates(client) if t["model_template_id"] == tid)
    assert after["sub_model"]["model_id"] == "glm-5.1" and after["display_name"] == "测试组合"

    # 空 body 400；不存在 404
    assert client.put(f"/api/openops/v1/admin/model-templates/{tid}", headers=ADMIN_HEADERS,
                      json={"client_request_id": "u_empty"}).status_code == 400
    assert client.put("/api/openops/v1/admin/model-templates/00000000-0000-0000-0000-000000000000",
                      headers=ADMIN_HEADERS,
                      json={"client_request_id": "u_404", "display_name": "x"}).status_code == 404

    recent = unwrap(client.get("/api/openops/v1/admin/audit/recent", headers=ADMIN_HEADERS))
    types = {e["event_type"] for e in recent}
    assert {"model_template.created", "model_template.updated"} <= types


def test_mt_003_admin_endpoints_forbidden_for_user(client):
    assert client.get("/api/openops/v1/admin/model-templates", headers=USER_HEADERS).status_code == 403
    assets = _assets_by_model_id(client)
    r = client.post("/api/openops/v1/admin/model-templates", headers=USER_HEADERS,
                    json={"client_request_id": "x", "display_name": "越权",
                          "main_model_asset_id": assets["glm-5.1"]["model_asset_id"],
                          "sub_model_asset_id": assets["glm-5.1"]["model_asset_id"]})
    assert r.status_code == 403


def test_mt_004_set_default_atomic(client):
    """默认切换后恰一个 is_default 且指向新目标（两步切换 + 部分唯一索引兜底）。"""
    rows = _admin_templates(client)
    economy = next(t for t in rows if t["display_name"] == "经济")
    out = unwrap(client.post(
        f"/api/openops/v1/admin/model-templates/{economy['model_template_id']}:set-default",
        headers=ADMIN_HEADERS, json={"client_request_id": f"d_{time.time_ns()}"}))
    assert out["is_default"] is True
    after = _admin_templates(client)
    defaults = [t for t in after if t["is_default"]]
    assert len(defaults) == 1 and defaults[0]["display_name"] == "经济"
    recent = unwrap(client.get("/api/openops/v1/admin/audit/recent", headers=ADMIN_HEADERS))
    assert any(e["event_type"] == "model_template.default_changed" for e in recent)


def test_mt_005_status_route_not_swallowed(client):
    """路由顺序护栏：路径参数匹配 `:`，PUT /{id} 若抢注会把 `:status` 吃掉（镜像 model-assets 用例）。"""
    tid = _admin_templates(client)[0]["model_template_id"]
    r = client.put(f"/api/openops/v1/admin/model-templates/{tid}:status", headers=ADMIN_HEADERS,
                   json={"client_request_id": f"st_{time.time_ns()}", "status": "disabled"})
    assert r.status_code == 200
    assert next(t for t in _admin_templates(client)
                if t["model_template_id"] == tid)["status"] == "disabled"
    # 已停用不能设默认
    r2 = client.post(f"/api/openops/v1/admin/model-templates/{tid}:set-default", headers=ADMIN_HEADERS,
                     json={"client_request_id": "d_disabled"})
    assert r2.status_code == 400


# ---- 用户列表 ACL 与绑定 ----

def test_mt_006_user_list_acl_fail_closed(client):
    """主、子槽位都授权才可见：restricted 槽位对未授权用户整套隐身；disable 后全员隐身。"""
    _whitelist_other(client)
    assets = _assets_by_model_id(client)
    tx = unwrap(_create_template(client, "含受限子模型", assets["glm-5.1"]["model_asset_id"],
                                 assets["tx-llm-v2"]["model_asset_id"]))

    demo_names = {t["display_name"] for t in _user_templates(client)}
    other_names = {t["display_name"] for t in _user_templates(client, OTHER_HEADERS)}
    assert "含受限子模型" in demo_names          # 0026demo01：seed 已授 tx-llm-v2
    assert "含受限子模型" not in other_names     # 0099other：sub 槽未授权 → 整套隐身
    assert "均衡（推荐）" in other_names          # 全 all 槽位照常可见

    # disable → 对所有人隐身
    unwrap(client.put(f"/api/openops/v1/admin/model-templates/{tx['model_template_id']}:status",
                      headers=ADMIN_HEADERS, json={"client_request_id": "st_x", "status": "disabled"}))
    assert "含受限子模型" not in {t["display_name"] for t in _user_templates(client)}


def test_mt_007_bind_and_runtime_resolution(client):
    """绑定端到端：overlay.model_template_id（经济）→ 起任务 → 主槽 glm-5.1 / 子槽 qwen3.5-instruct。"""
    economy = next(t for t in _user_templates(client) if t["display_name"] == "经济")
    inst = unwrap(_create_team(client, USER_HEADERS, "绑模板的 Agent",
                               {"model_template_id": economy["model_template_id"]}))["instance"]
    run = create_run(client, inst["instance_id"])
    unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks", headers=USER_HEADERS,
                       json={"client_request_id": f"t_{time.time_ns()}", "input_text": "排查支付延迟"}))
    state = wait_until(lambda: _state_with_model(client, run["agent_run_id"]))
    assert state is not None
    assert state["active_task"]["selected_model"] == "glm-5.1"
    assert state["active_task"]["selected_sub_model"] == "qwen3.5-instruct"


def _state_with_model(client, run_id: str):
    s = unwrap(client.get(f"/api/openops/v1/agent-runs/{run_id}/state", headers=USER_HEADERS))
    return s if (s.get("active_task") or {}).get("selected_model") else None


def test_mt_008_bind_unauthorized_403_and_exclusive_400(client):
    """越权绑定（sub 槽 restricted 未授权）→ MODEL_NOT_AUTHORIZED；三键互斥 → 400；
    绑不存在模板 → 404；停用模板 → TEMPLATE_DISABLED。"""
    _whitelist_other(client)
    assets = _assets_by_model_id(client)
    tx = unwrap(_create_template(client, "受限组合", assets["glm-5.1"]["model_asset_id"],
                                 assets["tx-llm-v2"]["model_asset_id"]))

    r = _create_team(client, OTHER_HEADERS, "other 越权", {"model_template_id": tx["model_template_id"]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "MODEL_NOT_AUTHORIZED"

    r2 = _create_team(client, USER_HEADERS, "双键", {"model_template_id": tx["model_template_id"],
                                                    "user_llm_config_id": "11111111-1111-1111-1111-111111111111"})
    assert r2.status_code == 400

    r3 = _create_team(client, USER_HEADERS, "绑不存在", {"model_template_id": "00000000-0000-0000-0000-000000000000"})
    assert r3.status_code == 404

    unwrap(client.put(f"/api/openops/v1/admin/model-templates/{tx['model_template_id']}:status",
                      headers=ADMIN_HEADERS, json={"client_request_id": "st_d", "status": "disabled"}))
    r4 = _create_team(client, USER_HEADERS, "绑停用", {"model_template_id": tx["model_template_id"]})
    assert r4.status_code == 400 and r4.json()["error"]["code"] == "TEMPLATE_DISABLED"


def test_mt_009_edit_rebind_derives_config_version(client):
    """编辑换绑：update 带 model_template_id → 配置版本 +1、新 overlay 只含模板键（旧模型键被清）。"""
    inst = unwrap(_create_team(client, USER_HEADERS, "先平台后模板",
                               {"platform_model_id": "glm-5.1"}))["instance"]
    before = unwrap(client.get(f"/api/openops/v1/agent-teams/{inst['instance_id']}/config-versions",
                               headers=USER_HEADERS))
    economy = next(t for t in _user_templates(client) if t["display_name"] == "经济")
    unwrap(client.post(f"/api/openops/v1/agent-teams/{inst['instance_id']}:update", headers=USER_HEADERS,
                       json={"client_request_id": f"u_{time.time_ns()}", "name": "先平台后模板",
                             "workspace_id": "ws_pay_abc", "user_llm_config_id": None,
                             "platform_model_id": None,
                             "model_template_id": economy["model_template_id"]}))
    after = unwrap(client.get(f"/api/openops/v1/agent-teams/{inst['instance_id']}/config-versions",
                              headers=USER_HEADERS))
    assert len(after) == len(before) + 1
    newest = after[0] if after[0]["config_version_id"] != before[0]["config_version_id"] else after[-1]
    overlay = newest["overlay_json"]
    assert overlay.get("model_template_id") == economy["model_template_id"]
    assert "platform_model_id" not in overlay and "user_llm_config_id" not in overlay


def test_mt_010_legacy_platform_overlay_keeps_sub_none(client):
    """legacy 回归：platform_model_id overlay → selected_sub_model=None（主=子旧语义不受影响）。"""
    inst = unwrap(_create_team(client, USER_HEADERS, "legacy 平台模型",
                               {"platform_model_id": "glm-5.1"}))["instance"]
    run = create_run(client, inst["instance_id"])
    unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks", headers=USER_HEADERS,
                       json={"client_request_id": f"t_{time.time_ns()}", "input_text": "看看告警"}))
    state = wait_until(lambda: _state_with_model(client, run["agent_run_id"]))
    assert state["active_task"]["selected_model"] == "glm-5.1"
    assert state["active_task"]["selected_sub_model"] is None


def test_mt_011_degraded_slot_falls_back_and_audits(client):
    """降级留痕：绑定后撤销 sub 槽授权 → 起任务照常 running + model.template_degraded 审计，
    payload 经 redact 白名单后仍含 slot / model_template_id / reason_summary（护住脱敏白名单）。"""
    assets = _assets_by_model_id(client)
    tpl = unwrap(_create_template(client, "会降级的组合", assets["glm-5.1"]["model_asset_id"],
                                  assets["tx-llm-v2"]["model_asset_id"]))
    inst = unwrap(_create_team(client, USER_HEADERS, "降级实例",
                               {"model_template_id": tpl["model_template_id"]}))["instance"]
    # 撤销 demo 的 tx-llm-v2 授权（改绑空集合）→ sub 槽在下次 start_task 解析时失效
    unwrap(client.put(f"/api/openops/v1/admin/model-assets/{assets['tx-llm-v2']['model_asset_id']}/grants",
                      headers=ADMIN_HEADERS,
                      json={"client_request_id": "g_revoke", "access_scope": "restricted", "user_ids": []}))
    run = create_run(client, inst["instance_id"])
    unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks", headers=USER_HEADERS,
                       json={"client_request_id": f"t_{time.time_ns()}", "input_text": "排查"}))
    state = wait_until(lambda: _state_with_model(client, run["agent_run_id"]))
    assert state["active_task"]["selected_model"] == "glm-5.1"
    assert state["active_task"]["selected_sub_model"] == "glm-5.1"  # sub 槽回退平台默认

    events = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/events", headers=USER_HEADERS))
    items = events["items"] if isinstance(events, dict) else events
    deg = next(e for e in items if e["event_type"] == "model.template_degraded")
    payload = deg["payload_redacted_json"]
    assert payload["slot"] == "sub"
    assert payload["model_template_id"] == tpl["model_template_id"]
    assert payload.get("reason_summary")  # reason 经 redact 改名 reason_summary


# ---- 纯逻辑：resolve_template_models / _child_state ----

_ROWS = [
    {"model_asset_id": "a-glm", "model_id": "glm-5.1", "display_name": "GLM-5.1",
     "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
     "secret_env_var": "OPENOPS_PLATFORM_GLM_API_KEY"},
    {"model_asset_id": "a-qwen", "model_id": "qwen3.5-instruct", "display_name": "Qwen3.5",
     "base_url": None, "secret_env_var": None},
]


def _patch_gateway(monkeypatch, tpl_row):
    async def fake_available(_uid: str):
        return _ROWS

    async def fake_get(_tid: str):
        return tpl_row

    monkeypatch.setattr(model_gateway.model_assets, "list_available_for_user", fake_available)
    monkeypatch.setattr(model_gateway.model_templates, "get", fake_get)


async def test_mt_012_resolve_template_models_happy(monkeypatch):
    _patch_gateway(monkeypatch, {"model_template_id": "t1", "status": "active",
                                 "main_model_asset_id": "a-glm", "sub_model_asset_id": "a-qwen"})
    res = await model_gateway.resolve_template_models("t1", "u1")
    assert res["main_spec"]["model_id"] == "glm-5.1" and res["main_selected"] == "glm-5.1"
    assert res["sub_spec"]["model_id"] == "qwen3.5-instruct" and res["sub_selected"] == "qwen3.5-instruct"
    assert res["degraded"] == []


async def test_mt_013_resolve_template_models_sub_unauthorized(monkeypatch):
    """sub 槽资产不在授权集合 → 该槽回退平台默认（glm）+ degraded 记 MODEL_NOT_AUTHORIZED。"""
    _patch_gateway(monkeypatch, {"model_template_id": "t1", "status": "active",
                                 "main_model_asset_id": "a-glm", "sub_model_asset_id": "a-gone"})
    res = await model_gateway.resolve_template_models("t1", "u1")
    assert res["main_selected"] == "glm-5.1"
    assert res["sub_selected"] == "glm-5.1"  # 回退默认
    assert [d["slot"] for d in res["degraded"]] == ["sub"]
    assert res["degraded"][0]["reason"] == "MODEL_NOT_AUTHORIZED"


async def test_mt_014_resolve_template_models_template_gone(monkeypatch):
    """模板软删/disabled → 主槽回默认、sub=None（子回落跟随主）、degraded 记 TEMPLATE_UNAVAILABLE。"""
    _patch_gateway(monkeypatch, None)
    res = await model_gateway.resolve_template_models("t-gone", "u1")
    assert res["main_selected"] == "glm-5.1" and res["sub_spec"] is None and res["sub_selected"] is None
    assert res["degraded"][0]["reason"] == "TEMPLATE_UNAVAILABLE"

    _patch_gateway(monkeypatch, {"model_template_id": "t1", "status": "disabled",
                                 "main_model_asset_id": "a-glm", "sub_model_asset_id": "a-qwen"})
    res2 = await model_gateway.resolve_template_models("t1", "u1")
    assert res2["degraded"][0]["reason"] == "TEMPLATE_UNAVAILABLE"


def test_mt_015_child_state_inherits_sub_slot():
    """_child_state：sub_model_spec 设了 → 子取子槽（spec 与标量同源）；未设 → 回落主（主=子）。"""
    from runtime.subagent_dispatch import _child_state
    from runtime.task_registry import TaskState

    st = TaskState(task_id="tsk_1", run_id="r1", user_id="u1", instance_id="i1", input_text="x")
    st.model_spec = {"model_id": "glm-5.1"}
    st.selected_model = "glm-5.1"
    sub = {"label": "巡检", "skills": [], "mcp_tools": []}

    child = _child_state(st, sub, "inspect", "查一下", "d1")
    assert child.model_spec == {"model_id": "glm-5.1"} and child.selected_model == "glm-5.1"

    st.sub_model_spec = {"model_id": "qwen3.5-instruct"}
    st.selected_sub_model = "qwen3.5-instruct"
    child2 = _child_state(st, sub, "inspect", "查一下", "d2")
    assert child2.model_spec == {"model_id": "qwen3.5-instruct"}
    assert child2.selected_model == "qwen3.5-instruct"
    # 子恒禁二层派发：自身 sub 槽不携带
    assert child2.sub_model_spec is None and child2.selected_sub_model is None


def test_mt_016_migration_file_idempotent_and_names_fit():
    """迁移纪律（镜像 test_ddl_007）：幂等、无 DROP、索引名 ≤30。"""
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[1] / "sql" / "migrate-2026-07-29-model-template.sql").read_text(
        encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS sre_model_template" in sql
    assert "ADD COLUMN IF NOT EXISTS selected_sub_model text" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_model_template_name" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_model_template_default" in sql
    assert max(len(n) for n in ("ux_model_template_name", "ux_model_template_default")) <= 30
    assert "DROP " not in sql.upper()
