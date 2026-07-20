from __future__ import annotations

import time

from conftest import ADMIN_HEADERS, OTHER_HEADERS, USER_HEADERS, create_instance, create_run, unwrap

from app import model_gateway


def _platform_models(client, headers) -> list[str]:
    return [m["model_id"] for m in unwrap(client.get("/api/openops/v1/models/platform", headers=headers))]


def _tx_asset_id(client) -> str:
    rows = unwrap(client.get("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS))
    return next(r["model_asset_id"] for r in rows if r["model_id"] == "tx-llm-v2")


def _select(client, headers, run_id: str, model: str):
    return client.post(
        f"/api/openops/v1/agent-runs/{run_id}:select-model", headers=headers,
        json={"client_request_id": f"sel_{time.time_ns()}", "model_source": model},
    )


def test_model_acl_001_visibility_filtered_by_grant(client):
    """scope=all 全员可见；restricted 仅被授权用户可见（MODEL-ACL-001/002 列表面）。"""
    demo = _platform_models(client, USER_HEADERS)  # 0026demo01：seed 已授 tx-llm-v2
    assert "qwen3.5-instruct" in demo and "tx-llm-v2" in demo
    assert "claude-3-5-sonnet" not in demo  # disabled 不出现

    # 另一用户先加白（platform 准入）再查：restricted 模型不可见
    unwrap(client.post("/api/openops/v1/admin/users/whitelist", headers=ADMIN_HEADERS,
                       json={"client_request_id": "wl_other", "user_id": "0099other",
                             "display_name": "Other", "role": "user"}))
    other = _platform_models(client, OTHER_HEADERS)
    assert "qwen3.5-instruct" in other and "tx-llm-v2" not in other


def test_model_acl_002_restricted_select_403(client):
    """白名单外用户选 restricted / 未知模型 → MODEL_NOT_AUTHORIZED（MODEL-ACL-002）。"""
    unwrap(client.post("/api/openops/v1/admin/users/whitelist", headers=ADMIN_HEADERS,
                       json={"client_request_id": "wl_other2", "user_id": "0099other",
                             "display_name": "Other", "role": "user"}))
    templates = unwrap(client.get("/api/openops/v1/templates/available", headers=OTHER_HEADERS))
    inst = unwrap(client.post("/api/openops/v1/agent-teams", headers=OTHER_HEADERS,
                              json={"client_request_id": f"c_{time.time_ns()}",
                                    "template_version_id": templates[0]["template_version_id"],
                                    "name": "other 的 Agent", "workspace_id": "ws_pay_abc"}))["instance"]
    run = unwrap(client.post("/api/openops/v1/agent-runs", headers=OTHER_HEADERS,
                             json={"client_request_id": f"r_{time.time_ns()}",
                                   "agent_team_instance_id": inst["instance_id"]}))["run"]
    r = _select(client, OTHER_HEADERS, run["agent_run_id"], "tx-llm-v2")
    assert r.status_code == 403 and r.json()["error"]["code"] == "MODEL_NOT_AUTHORIZED"
    r2 = _select(client, OTHER_HEADERS, run["agent_run_id"], "no-such-model")
    assert r2.status_code == 403  # 未知模型同样 fail-closed


def test_model_acl_004_grant_then_select_ok(client):
    """授权后可见可选；撤销后 fail-closed（MODEL-ACL-003/004 API 面）。"""
    unwrap(client.post("/api/openops/v1/admin/users/whitelist", headers=ADMIN_HEADERS,
                       json={"client_request_id": "wl_other3", "user_id": "0099other",
                             "display_name": "Other", "role": "user"}))
    aid = _tx_asset_id(client)
    unwrap(client.put(f"/api/openops/v1/admin/model-assets/{aid}/grants", headers=ADMIN_HEADERS,
                      json={"client_request_id": "g1", "access_scope": "restricted",
                            "user_ids": ["0026demo01", "0099other"]}))
    assert "tx-llm-v2" in _platform_models(client, OTHER_HEADERS)

    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    ok = _select(client, USER_HEADERS, run["agent_run_id"], "tx-llm-v2")
    assert ok.status_code == 200

    # 撤销 other（保留 demo）→ other 再不可见
    unwrap(client.put(f"/api/openops/v1/admin/model-assets/{aid}/grants", headers=ADMIN_HEADERS,
                      json={"client_request_id": "g2", "access_scope": "restricted", "user_ids": ["0026demo01"]}))
    assert "tx-llm-v2" not in _platform_models(client, OTHER_HEADERS)


async def test_model_acl_003_gateway_second_check_fallback(monkeypatch):
    """Model Gateway 二次校验（MODEL-ACL-003 运行时面）：选中模型不在授权集合 → 回退默认（纯逻辑，不打 DB）。"""
    rows = [
        {"model_id": "glm-5.1", "display_name": "GLM-5.1",
         "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
         "secret_env_var": "OPENOPS_PLATFORM_GLM_API_KEY"},
    ]

    async def fake_available(_uid: str):
        return rows

    monkeypatch.setattr(model_gateway.model_assets, "list_available_for_user", fake_available)
    spec = await model_gateway.resolve_runtime_model("tx-llm-v2", "u1")  # 选中已撤销授权的模型
    assert spec is not None and spec["model_id"] == "glm-5.1"  # 忽略选中值回退默认
    assert spec["base_url"] == "https://open.bigmodel.cn/api/paas/v4"  # completions 后缀剥离

    monkeypatch.setattr(model_gateway.model_assets, "list_available_for_user", lambda _u: _empty())
    spec2 = await model_gateway.resolve_runtime_model("tx-llm-v2", "u1")
    assert spec2 is None  # 全无可用 → stub


async def _empty():
    return []


def test_model_acl_005_grants_audit_and_churn(client):
    """保存授权写审计 model_asset.grants_updated；软删+插新（MODEL-ACL-005）。"""
    aid = _tx_asset_id(client)
    unwrap(client.put(f"/api/openops/v1/admin/model-assets/{aid}/grants", headers=ADMIN_HEADERS,
                      json={"client_request_id": "g3", "access_scope": "restricted", "user_ids": ["admin"]}))
    grants = unwrap(client.get(f"/api/openops/v1/admin/model-assets/{aid}/grants", headers=ADMIN_HEADERS))
    assert grants["user_ids"] == ["admin"]
    recent = unwrap(client.get("/api/openops/v1/admin/audit/recent", headers=ADMIN_HEADERS))
    assert any(e["event_type"] == "model_asset.grants_updated" for e in recent)


def test_model_acl_006_admin_endpoints_forbidden_for_user(client):
    r = client.get("/api/openops/v1/admin/model-assets", headers=USER_HEADERS)
    assert r.status_code == 403


def test_model_acl_007_register_ignores_sensitive_and_dedups(client):
    """注册：DTO 白名单字段（api_key 进不来）；model_id 去重（MODEL-ACL-007）。"""
    unwrap(client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                       json={"client_request_id": "m1", "display_name": "内部网关模型", "model_id": "gw-llm-1",
                             "secret_env_var": "OPENOPS_GW_KEY", "access_scope": "all",
                             "api_key": "sk-should-never-persist"}))
    rows = unwrap(client.get("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS))
    gw = next(r for r in rows if r["model_id"] == "gw-llm-1")
    assert "api_key" not in gw and "sk-should-never-persist" not in str(gw)
    dup = client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                      json={"client_request_id": "m2", "display_name": "重复", "model_id": "gw-llm-1"})
    assert dup.status_code == 400


def test_register_rejects_key_value_in_secret_env_var(client):
    """SEC 护栏：secret_env_var 填成 Key 本身（如 sk-...）→ 入口拒绝，明文 Key 不落库。"""
    r = client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                    json={"client_request_id": "reg_k1", "display_name": "误填", "model_id": "oops-llm",
                          "base_url": "http://gw/v1", "secret_env_var": "sk-1234abcd", "access_scope": "all"})
    assert r.status_code == 400
    assert "环境变量名" in r.json()["error"]["message"]

    r2 = client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                     json={"client_request_id": "reg_k2", "display_name": "正确", "model_id": "good-llm",
                           "base_url": "http://gw/v1", "secret_env_var": "OPENOPS_TX_LLM_KEY", "access_scope": "all"})
    assert r2.status_code == 200


def test_model_acl_default_flag_points_to_runnable_default(client):
    """is_default = 运行时真实默认（OPENOPS_RUNTIME_MODEL=glm-5.1，带 secret_env_var 才能跑），
    不是列表首位的 Qwen3.5（seed 无 Key，跑不起来）——初始化向导据此展示真实平台默认名。"""
    rows = unwrap(client.get("/api/openops/v1/models/platform", headers=USER_HEADERS))
    by_id = {r["model_id"]: r for r in rows}
    assert by_id["glm-5.1"]["is_default"] is True            # 真正能跑的默认
    assert by_id["qwen3.5-instruct"]["is_default"] is False  # 无 Key，不再被当默认
    assert sum(1 for r in rows if r.get("is_default")) == 1  # 恰一个默认


# ---- 更新模型资产连接配置（PUT /admin/model-assets/{id}，PATCH 语义）----

def _asset(client, model_id: str) -> dict:
    rows = unwrap(client.get("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS))
    return next(r for r in rows if r["model_id"] == model_id)


def _update(client, headers, asset_id: str, body: dict):
    return client.put(f"/api/openops/v1/admin/model-assets/{asset_id}", headers=headers,
                      json={"client_request_id": f"upd_{time.time_ns()}", **body})


def test_model_asset_update_patches_only_given_keys(client):
    """只改 base_url，其余列原样不动——PATCH 语义（生产改错环境地址的主场景）。"""
    before = _asset(client, "glm-5.1")
    r = _update(client, ADMIN_HEADERS, before["model_asset_id"], {"base_url": "https://prod.gw/v1/chat/completions"})
    assert r.status_code == 200

    after = _asset(client, "glm-5.1")
    assert after["base_url"] == "https://prod.gw/v1/chat/completions"
    for k in ("display_name", "secret_env_var", "context_window_tokens", "model_id", "status", "access_scope"):
        assert after[k] == before[k], f"{k} 不该被这次更新改动"


def test_model_asset_update_explicit_null_clears_base_url(client):
    """显式传 null 与「没传」必须区分：前者清空（走平台网关的模型不填 base_url）。"""
    aid = _asset(client, "glm-5.1")["model_asset_id"]
    assert _update(client, ADMIN_HEADERS, aid, {"base_url": None}).status_code == 200
    assert _asset(client, "glm-5.1")["base_url"] is None


def test_model_asset_update_rejects_key_value_in_secret_env_var(client):
    """SEC-001：更新路径与注册路径同一道闸，不能成为「真实 Key 落库」的后门。"""
    aid = _asset(client, "glm-5.1")["model_asset_id"]
    r = _update(client, ADMIN_HEADERS, aid, {"secret_env_var": "sk-1234abcd"})
    assert r.status_code == 400 and "环境变量名" in r.json()["error"]["message"]
    assert _asset(client, "glm-5.1")["secret_env_var"] == "OPENOPS_PLATFORM_GLM_API_KEY"  # 未被写坏


def test_model_asset_update_rejects_empty_and_not_null_columns(client):
    """空 body → 400；NOT NULL 列显式置 null → 400（入口给可读原因，而非 DB 500）。"""
    aid = _asset(client, "glm-5.1")["model_asset_id"]
    assert _update(client, ADMIN_HEADERS, aid, {}).status_code == 400
    assert _update(client, ADMIN_HEADERS, aid, {"display_name": None}).status_code == 400
    assert _update(client, ADMIN_HEADERS, aid, {"context_window_tokens": None}).status_code == 400


def test_model_asset_update_404_and_403(client):
    """不存在 → 404；非管理员 → 403。"""
    aid = _asset(client, "glm-5.1")["model_asset_id"]
    assert _update(client, ADMIN_HEADERS, "00000000-0000-0000-0000-000000000000",
                   {"base_url": "http://x/v1"}).status_code == 404
    assert _update(client, USER_HEADERS, aid, {"base_url": "http://x/v1"}).status_code == 403


def test_model_asset_update_route_does_not_swallow_status(client):
    """路由顺序护栏：路径参数匹配 `:`，新的 PUT /{id} 若抢在 `:status` 前会把它吃掉。"""
    aid = _asset(client, "qwen3.5-instruct")["model_asset_id"]
    r = client.put(f"/api/openops/v1/admin/model-assets/{aid}:status", headers=ADMIN_HEADERS,
                   json={"client_request_id": f"st_{time.time_ns()}", "status": "disabled"})
    assert r.status_code == 200
    assert _asset(client, "qwen3.5-instruct")["status"] == "disabled"  # 真的走了 set_status


async def test_model_asset_update_takes_effect_in_gateway(client):
    """改完新 run 立即生效：网关每次重查库，无缓存需失效。"""
    aid = _asset(client, "glm-5.1")["model_asset_id"]
    assert _update(client, ADMIN_HEADERS, aid, {"base_url": "https://prod.gw/v1/chat/completions"}).status_code == 200
    spec = await model_gateway.resolve_runtime_model("glm-5.1", "0026demo01")
    assert spec is not None and spec["base_url"] == "https://prod.gw/v1"  # completions 后缀照常剥离


def test_model_asset_update_writes_audit(client):
    aid = _asset(client, "glm-5.1")["model_asset_id"]
    _update(client, ADMIN_HEADERS, aid, {"base_url": "https://prod.gw/v1"})
    recent = unwrap(client.get("/api/openops/v1/admin/audit/recent", headers=ADMIN_HEADERS))
    assert any(e["event_type"] == "model_asset.updated" for e in recent)
