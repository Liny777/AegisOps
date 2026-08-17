"""模型模板（38 号：主/子 Agent 槽位模型编排；38.1：授权在模板维度）全链闭合。

- 管理面 CRUD（重名/未知资产/403/审计）+ 默认原子切换 + `:status` 路由顺序护栏 + grants 端点。
- 模板 ACL fail-closed（38.1）：restricted 模板仅白名单用户可见/可绑；disable 全员隐身。
- 绑定端到端：overlay.model_template_id → 起任务 → selected_model=主槽 / selected_sub_model=子槽。
- 越权绑定 403、三键互斥 400、编辑换绑涨版本、legacy 回归（selected_sub_model=None）。
- resolve_template_models 纯逻辑（TEMPLATE_UNAVAILABLE / TEMPLATE_NOT_AUTHORIZED / MODEL_UNAVAILABLE）
  + _child_state 继承 + 降级留痕（redact 白名单护栏）。
"""
from __future__ import annotations

import time

from conftest import ADMIN_HEADERS, OTHER_HEADERS, USER_HEADERS, create_run, unwrap, wait_until

from app import model_gateway


def _assets_by_model_id(client) -> dict[str, dict]:
    rows = unwrap(client.get("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS))
    return {r["model_id"]: r for r in rows}


def _give_platform_default_a_key(client) -> None:
    """给 glm-5.1 配一把 Key，使「平台默认」这个回退目标真实存在。

    2026-08-17 起可用性判据是「密文列真有 Key」（原先只看填没填环境变量名），而 conftest 刻意
    不注入平台 Key（注入了 runtime 会去打真网）。所有断言「降级到平台默认 = glm-5.1」的用例都要
    先调它，否则平台默认解析为 None（无 Key 时本就该跌 stub，是正确行为而非回归）。
    """
    aid = _assets_by_model_id(client)["glm-5.1"]["model_asset_id"]
    unwrap(client.put(f"/api/openops/v1/admin/model-assets/{aid}", headers=ADMIN_HEADERS,
                      json={"client_request_id": f"key_{time.time_ns()}", "api_key": "sk-platform-default"}))


def _admin_templates(client) -> list[dict]:
    return unwrap(client.get("/api/openops/v1/admin/model-templates", headers=ADMIN_HEADERS))


def _user_templates(client, headers=USER_HEADERS) -> list[dict]:
    return unwrap(client.get("/api/openops/v1/models/templates", headers=headers))


def _create_template(client, name: str, main_asset: str, sub_asset: str, **extra):
    return client.post("/api/openops/v1/admin/model-templates", headers=ADMIN_HEADERS,
                       json={"client_request_id": f"mt_{time.time_ns()}", "display_name": name,
                             "main_model_asset_id": main_asset, "sub_model_asset_id": sub_asset, **extra})


def _grants(client, template_id: str, scope: str, user_ids: list[str]):
    """模板授权保存（38.1：PUT /admin/model-templates/{id}/grants）。"""
    return unwrap(client.put(f"/api/openops/v1/admin/model-templates/{template_id}/grants",
                             headers=ADMIN_HEADERS,
                             json={"client_request_id": f"g_{time.time_ns()}",
                                   "access_scope": scope, "user_ids": user_ids}))


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
    """seed 双调用时序：全新库（pytest 库）末尾第二次调用生效——种出「均衡（推荐）默认 + 经济 +
    交易专用（受限演示）」；38.1：restricted 种子模板带 scope + grant_count。"""
    rows = _admin_templates(client)
    by_name = {r["display_name"]: r for r in rows}
    assert "均衡（推荐）" in by_name and "经济" in by_name
    assert by_name["均衡（推荐）"]["is_default"] is True
    assert by_name["经济"]["main_model"]["model_id"] == "glm-5.1"
    assert by_name["经济"]["sub_model"]["model_id"] == "qwen3.5-instruct"
    assert sum(1 for r in rows if r["is_default"]) == 1
    tx = by_name["交易专用（受限演示）"]
    assert tx["access_scope"] == "restricted" and tx["grant_count"] == 1  # 授权了 0026demo01
    assert by_name["均衡（推荐）"]["access_scope"] == "all"


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
    assert created["access_scope"] == "all"  # 38.1：缺省全员开放

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
    """模板级 ACL（38.1）：restricted 模板仅白名单用户可见；disable 后全员隐身。
    seed 的「交易专用（受限演示）」即天然夹具（授权 0026demo01）。"""
    _whitelist_other(client)
    demo_names = {t["display_name"] for t in _user_templates(client)}
    other_names = {t["display_name"] for t in _user_templates(client, OTHER_HEADERS)}
    assert "交易专用（受限演示）" in demo_names       # 0026demo01：seed 已授权
    assert "交易专用（受限演示）" not in other_names  # 0099other：无 grant → 隐身（fail-closed）
    assert "均衡（推荐）" in other_names              # scope=all 照常可见

    # 授权 other → 立即可见；撤销 → 再隐身（软删+插新）
    assets = _assets_by_model_id(client)
    mine = unwrap(_create_template(client, "临时受限组合", assets["glm-5.1"]["model_asset_id"],
                                   assets["qwen3.5-instruct"]["model_asset_id"],
                                   access_scope="restricted"))
    tid = mine["model_template_id"]
    assert "临时受限组合" not in {t["display_name"] for t in _user_templates(client, OTHER_HEADERS)}
    _grants(client, tid, "restricted", ["0099other"])
    assert "临时受限组合" in {t["display_name"] for t in _user_templates(client, OTHER_HEADERS)}
    _grants(client, tid, "restricted", [])
    assert "临时受限组合" not in {t["display_name"] for t in _user_templates(client, OTHER_HEADERS)}

    # disable → 对所有人（含已授权者）隐身
    unwrap(client.put(f"/api/openops/v1/admin/model-templates/{tid}:status",
                      headers=ADMIN_HEADERS, json={"client_request_id": "st_x", "status": "disabled"}))
    _grants(client, tid, "restricted", ["0026demo01"])
    assert "临时受限组合" not in {t["display_name"] for t in _user_templates(client)}


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
    """越权绑定（restricted 模板无 grant）→ MODEL_NOT_AUTHORIZED；三键互斥 → 400；
    绑不存在模板 → 404；停用模板 → TEMPLATE_DISABLED。"""
    _whitelist_other(client)
    assets = _assets_by_model_id(client)
    tx = unwrap(_create_template(client, "受限组合", assets["glm-5.1"]["model_asset_id"],
                                 assets["tx-llm-v2"]["model_asset_id"], access_scope="restricted"))
    _grants(client, tx["model_template_id"], "restricted", ["0026demo01"])  # 只授权 demo

    r = _create_team(client, OTHER_HEADERS, "other 越权", {"model_template_id": tx["model_template_id"]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "MODEL_NOT_AUTHORIZED"
    ok = _create_team(client, USER_HEADERS, "demo 已授权可绑", {"model_template_id": tx["model_template_id"]})
    assert ok.status_code == 200  # 白名单内正常绑定

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


def test_mt_011_degraded_template_grant_revoked(client):
    """降级留痕（38.1）：绑定 restricted 模板后撤销 grant → 起任务照常 + TEMPLATE_NOT_AUTHORIZED
    降级（主回平台默认、sub=None 跟随主）；payload 经 redact 白名单后仍含
    slot / model_template_id / reason_summary（护住脱敏白名单）。"""
    _give_platform_default_a_key(client)  # 降级目标（平台默认）须真有 Key 才解析得出
    assets = _assets_by_model_id(client)
    tpl = unwrap(_create_template(client, "会降级的组合", assets["glm-5.1"]["model_asset_id"],
                                  assets["qwen3.5-instruct"]["model_asset_id"], access_scope="restricted"))
    _grants(client, tpl["model_template_id"], "restricted", ["0026demo01"])
    inst = unwrap(_create_team(client, USER_HEADERS, "降级实例",
                               {"model_template_id": tpl["model_template_id"]}))["instance"]
    # 绑定后撤销授权（改绑空集合）→ 第三闸门在下次 start_task 拦截并降级
    _grants(client, tpl["model_template_id"], "restricted", [])
    run = create_run(client, inst["instance_id"])
    unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks", headers=USER_HEADERS,
                       json={"client_request_id": f"t_{time.time_ns()}", "input_text": "排查"}))
    state = wait_until(lambda: _state_with_model(client, run["agent_run_id"]))
    assert state["active_task"]["selected_model"] == "glm-5.1"      # 主回平台默认
    assert state["active_task"]["selected_sub_model"] is None        # sub 跟随主

    events = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/events", headers=USER_HEADERS))
    items = events["items"] if isinstance(events, dict) else events
    deg = next(e for e in items if e["event_type"] == "model.template_degraded")
    payload = deg["payload_redacted_json"]
    assert payload["slot"] == "template"
    assert payload["model_template_id"] == tpl["model_template_id"]
    assert "TEMPLATE_NOT_AUTHORIZED" in str(payload.get("reason_summary"))  # reason 经 redact 改名


def test_mt_011b_degraded_slot_asset_disabled(client):
    """槽位资产失效降级（38.1：MODEL_UNAVAILABLE，已非授权问题）：all 模板绑定后禁用 sub 槽资产 →
    起任务 sub 槽回退平台默认 + 留痕。"""
    _give_platform_default_a_key(client)  # 降级目标（平台默认）须真有 Key 才解析得出
    assets = _assets_by_model_id(client)
    tpl = unwrap(_create_template(client, "槽位会失效的组合", assets["glm-5.1"]["model_asset_id"],
                                  assets["tx-llm-v2"]["model_asset_id"]))
    inst = unwrap(_create_team(client, USER_HEADERS, "槽位失效实例",
                               {"model_template_id": tpl["model_template_id"]}))["instance"]
    unwrap(client.put(f"/api/openops/v1/admin/model-assets/{assets['tx-llm-v2']['model_asset_id']}:status",
                      headers=ADMIN_HEADERS,
                      json={"client_request_id": f"st_{time.time_ns()}", "status": "disabled"}))
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
    assert "MODEL_UNAVAILABLE" in str(payload.get("reason_summary"))


# ---- 纯逻辑：resolve_template_models / _child_state ----

_ROWS = [
    # has_secret=True 才是「能跑」的平台默认候选（2026-08-17：Key 已迁密文列，判据不再是 secret_env_var）
    {"model_asset_id": "a-glm", "model_id": "glm-5.1", "display_name": "GLM-5.1",
     "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions", "has_secret": True},
    {"model_asset_id": "a-qwen", "model_id": "qwen3.5-instruct", "display_name": "Qwen3.5",
     "base_url": None, "has_secret": False},
]


def _patch_gateway(monkeypatch, tpl_row, has_grant: bool = True):
    async def fake_active():  # 38.1：list_active 无参（旧 fake 带 _uid 会 TypeError）
        return _ROWS

    async def fake_get(_tid: str):
        return tpl_row

    async def fake_has_grant(_tid: str, _uid: str):
        return has_grant

    monkeypatch.setattr(model_gateway.model_assets, "list_active", fake_active)
    monkeypatch.setattr(model_gateway.model_templates, "get", fake_get)
    monkeypatch.setattr(model_gateway.model_templates, "has_grant", fake_has_grant)


async def test_mt_012_resolve_template_models_happy(monkeypatch):
    _patch_gateway(monkeypatch, {"model_template_id": "t1", "status": "active", "access_scope": "all",
                                 "main_model_asset_id": "a-glm", "sub_model_asset_id": "a-qwen"})
    res = await model_gateway.resolve_template_models("t1", "u1")
    assert res["main_spec"]["model_id"] == "glm-5.1" and res["main_selected"] == "glm-5.1"
    assert res["sub_spec"]["model_id"] == "qwen3.5-instruct" and res["sub_selected"] == "qwen3.5-instruct"
    assert res["degraded"] == []


async def test_mt_013_resolve_template_models_slot_unavailable(monkeypatch):
    """sub 槽资产不在 active 池（禁用/删除）→ 该槽回退平台默认（glm）+ degraded 记 MODEL_UNAVAILABLE
    （38.1：已非授权问题，语义如实）。"""
    _patch_gateway(monkeypatch, {"model_template_id": "t1", "status": "active", "access_scope": "all",
                                 "main_model_asset_id": "a-glm", "sub_model_asset_id": "a-gone"})
    res = await model_gateway.resolve_template_models("t1", "u1")
    assert res["main_selected"] == "glm-5.1"
    assert res["sub_selected"] == "glm-5.1"  # 回退默认
    assert [d["slot"] for d in res["degraded"]] == ["sub"]
    assert res["degraded"][0]["reason"] == "MODEL_UNAVAILABLE"


async def test_mt_014_resolve_template_models_template_gone(monkeypatch):
    """模板软删/disabled → 主槽回默认、sub=None（子回落跟随主）、degraded 记 TEMPLATE_UNAVAILABLE。"""
    _patch_gateway(monkeypatch, None)
    res = await model_gateway.resolve_template_models("t-gone", "u1")
    assert res["main_selected"] == "glm-5.1" and res["sub_spec"] is None and res["sub_selected"] is None
    assert res["degraded"][0]["reason"] == "TEMPLATE_UNAVAILABLE"

    _patch_gateway(monkeypatch, {"model_template_id": "t1", "status": "disabled", "access_scope": "all",
                                 "main_model_asset_id": "a-glm", "sub_model_asset_id": "a-qwen"})
    res2 = await model_gateway.resolve_template_models("t1", "u1")
    assert res2["degraded"][0]["reason"] == "TEMPLATE_UNAVAILABLE"


async def test_mt_014b_resolve_template_models_not_authorized(monkeypatch):
    """restricted 模板 + 无 grant → TEMPLATE_NOT_AUTHORIZED（第三闸门）：主回默认、sub=None；
    有 grant → 正常解析（38.1 模板级授权二次校验）。access_scope 缺失视同 restricted（fail-closed）。"""
    tpl = {"model_template_id": "t1", "status": "active", "access_scope": "restricted",
           "main_model_asset_id": "a-glm", "sub_model_asset_id": "a-qwen"}
    _patch_gateway(monkeypatch, tpl, has_grant=False)
    res = await model_gateway.resolve_template_models("t1", "u1")
    assert res["main_selected"] == "glm-5.1" and res["sub_spec"] is None
    assert res["degraded"][0]["reason"] == "TEMPLATE_NOT_AUTHORIZED"

    _patch_gateway(monkeypatch, tpl, has_grant=True)
    res2 = await model_gateway.resolve_template_models("t1", "u1")
    assert res2["degraded"] == [] and res2["sub_selected"] == "qwen3.5-instruct"

    # access_scope 键缺失（未迁移旧行）→ 视同 restricted，无 grant 即降级
    _patch_gateway(monkeypatch, {"model_template_id": "t1", "status": "active",
                                 "main_model_asset_id": "a-glm", "sub_model_asset_id": "a-qwen"},
                   has_grant=False)
    res3 = await model_gateway.resolve_template_models("t1", "u1")
    assert res3["degraded"][0]["reason"] == "TEMPLATE_NOT_AUTHORIZED"


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
    """迁移纪律（镜像 test_ddl_007）：幂等、无 DROP、索引名 ≤30；含 38.1 追加段。"""
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[1] / "sql" / "migrate-2026-07-29-model-template.sql").read_text(
        encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS sre_model_template" in sql
    assert "ADD COLUMN IF NOT EXISTS selected_sub_model text" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_model_template_name" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_model_template_default" in sql
    # 38.1：模板授权范围增量（旧库重跑本文件即补齐）
    assert "ADD COLUMN IF NOT EXISTS access_scope text" in sql
    assert "CREATE TABLE IF NOT EXISTS sre_model_template_grant" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_model_template_grant_user" in sql
    assert "CREATE INDEX IF NOT EXISTS ix_model_template_grant_user" in sql
    assert max(len(n) for n in ("ux_model_template_name", "ux_model_template_default",
                                "ux_model_template_grant_user", "ix_model_template_grant_user")) <= 30
    assert "DROP " not in sql.upper()


def test_mt_018_delete_template(client):
    """软删模板（38.2）：admin/用户列表消失、grants 404、重复删 404、同名可重建；
    允许删默认模板（is_default 只影响选单预选）；写审计 model_template.deleted；非管理员 403。"""
    tid = next(t["model_template_id"] for t in _admin_templates(client) if t["display_name"] == "经济")
    assert client.delete(f"/api/openops/v1/admin/model-templates/{tid}",
                         headers=USER_HEADERS).status_code == 403  # 非管理员
    out = unwrap(client.request("DELETE", f"/api/openops/v1/admin/model-templates/{tid}",
                                headers=ADMIN_HEADERS))
    assert out["deleted"] is True
    assert "经济" not in {t["display_name"] for t in _admin_templates(client)}
    assert "经济" not in {t["display_name"] for t in _user_templates(client)}
    assert client.get(f"/api/openops/v1/admin/model-templates/{tid}/grants",
                      headers=ADMIN_HEADERS).status_code == 404
    assert client.delete(f"/api/openops/v1/admin/model-templates/{tid}",
                         headers=ADMIN_HEADERS).status_code == 404  # 重复删

    # display_name 唯一索引带 WHERE deleted_at IS NULL：同名可重建
    assets = _assets_by_model_id(client)
    assert _create_template(client, "经济", assets["glm-5.1"]["model_asset_id"],
                            assets["qwen3.5-instruct"]["model_asset_id"]).status_code == 200

    # 允许删默认模板：删后无默认（用户选单回退首个 active，前端已兜底）
    default_tid = next(t["model_template_id"] for t in _admin_templates(client) if t["is_default"])
    unwrap(client.request("DELETE", f"/api/openops/v1/admin/model-templates/{default_tid}",
                          headers=ADMIN_HEADERS))
    assert sum(1 for t in _admin_templates(client) if t["is_default"]) == 0

    recent = unwrap(client.get("/api/openops/v1/admin/audit/recent", headers=ADMIN_HEADERS))
    assert any(e["event_type"] == "model_template.deleted" for e in recent)


def test_mt_019_delete_bound_template_degrades(client):
    """删除已绑实例的模板 → 下次起任务走 TEMPLATE_UNAVAILABLE 降级回平台默认（既有 fail-safe 链）。"""
    _give_platform_default_a_key(client)  # 降级目标（平台默认）须真有 Key 才解析得出
    assets = _assets_by_model_id(client)
    tpl = unwrap(_create_template(client, "将被删除的组合", assets["glm-5.1"]["model_asset_id"],
                                  assets["qwen3.5-instruct"]["model_asset_id"]))
    inst = unwrap(_create_team(client, USER_HEADERS, "绑将删模板",
                               {"model_template_id": tpl["model_template_id"]}))["instance"]
    unwrap(client.request("DELETE", f"/api/openops/v1/admin/model-templates/{tpl['model_template_id']}",
                          headers=ADMIN_HEADERS))
    run = create_run(client, inst["instance_id"])
    unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks", headers=USER_HEADERS,
                       json={"client_request_id": f"t_{time.time_ns()}", "input_text": "排查"}))
    state = wait_until(lambda: _state_with_model(client, run["agent_run_id"]))
    assert state["active_task"]["selected_model"] == "glm-5.1"  # 主回平台默认
    assert state["active_task"]["selected_sub_model"] is None    # sub 跟随主

    events = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/events", headers=USER_HEADERS))
    items = events["items"] if isinstance(events, dict) else events
    deg = next(e for e in items if e["event_type"] == "model.template_degraded")
    assert deg["payload_redacted_json"]["slot"] == "template"
    assert "TEMPLATE_UNAVAILABLE" in str(deg["payload_redacted_json"].get("reason_summary"))


def test_mt_017_grants_crud_audit_and_404(client):
    """模板授权端点（38.1，镜像原资产侧 acl_005）：PUT/GET 往返、审计 model_template.grants_updated、
    all 清空白名单、未知模板 404。"""
    tid = next(t["model_template_id"] for t in _admin_templates(client) if t["display_name"] == "经济")
    out = _grants(client, tid, "restricted", ["admin", "admin", "0026demo01"])  # 重复项去重保序
    assert out["access_scope"] == "restricted" and out["user_ids"] == ["admin", "0026demo01"]
    got = unwrap(client.get(f"/api/openops/v1/admin/model-templates/{tid}/grants", headers=ADMIN_HEADERS))
    assert got["user_ids"] == ["admin", "0026demo01"]
    recent = unwrap(client.get("/api/openops/v1/admin/audit/recent", headers=ADMIN_HEADERS))
    assert any(e["event_type"] == "model_template.grants_updated" for e in recent)

    back = _grants(client, tid, "all", ["ignored"])  # all 时忽略 user_ids 清空白名单
    assert back["access_scope"] == "all" and back["user_ids"] == []

    assert client.get("/api/openops/v1/admin/model-templates/00000000-0000-0000-0000-000000000000/grants",
                      headers=ADMIN_HEADERS).status_code == 404
    # 非管理员 403
    assert client.get(f"/api/openops/v1/admin/model-templates/{tid}/grants",
                      headers=USER_HEADERS).status_code == 403
