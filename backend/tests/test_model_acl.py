"""模型资产路径回归（38.1：授权已迁模型模板维度，见 test_model_templates 的模板 ACL 用例）。

- 资产级授权放开：全部 active 模型对所有白名单用户可见/可选（disabled 仍隐身，fail-closed 保留）。
- 旧资产 grants 端点已移除（404）；注册/更新不再有 access_scope 语义（列废弃靠 DEFAULT 兜底）。
- Model Gateway 在全 active 池内解析（候选池不受授权约束）。
"""
from __future__ import annotations

import time

from conftest import ADMIN_HEADERS, OTHER_HEADERS, USER_HEADERS, create_instance, create_run, unwrap

from app import model_gateway


def _platform_models(client, headers) -> list[str]:
    return [m["model_id"] for m in unwrap(client.get("/api/openops/v1/models/platform", headers=headers))]


def _select(client, headers, run_id: str, model: str):
    return client.post(
        f"/api/openops/v1/agent-runs/{run_id}:select-model", headers=headers,
        json={"client_request_id": f"sel_{time.time_ns()}", "model_source": model},
    )


def _whitelist_other(client, crid: str):
    unwrap(client.post("/api/openops/v1/admin/users/whitelist", headers=ADMIN_HEADERS,
                       json={"client_request_id": crid, "user_id": "0099other",
                             "display_name": "Other", "role": "user"}))


def test_model_acl_001_asset_path_open_for_all(client):
    """38.1 资产路径放开：全部 active 模型（含遗留 restricted 数据 tx-llm-v2）对所有白名单用户可见；
    disabled 仍隐身（fail-closed 的 active 门保留）。"""
    demo = _platform_models(client, USER_HEADERS)
    assert "qwen3.5-instruct" in demo and "tx-llm-v2" in demo
    assert "claude-3-5-sonnet" not in demo  # disabled 不出现

    _whitelist_other(client, "wl_other")
    other = _platform_models(client, OTHER_HEADERS)
    assert "qwen3.5-instruct" in other and "tx-llm-v2" in other  # 38.1：不再按人过滤
    assert "claude-3-5-sonnet" not in other


def test_model_acl_002_select_model_open_but_fail_closed(client):
    """select-model（38.1 退化）：存在且 active 的模型任何人可选 → 200；
    未知模型 / disabled 模型 → 403（fail-closed 保留，错误码沿用 MODEL_NOT_AUTHORIZED）。"""
    _whitelist_other(client, "wl_other2")
    templates = unwrap(client.get("/api/openops/v1/templates/available", headers=OTHER_HEADERS))
    inst = unwrap(client.post("/api/openops/v1/agent-teams", headers=OTHER_HEADERS,
                              json={"client_request_id": f"c_{time.time_ns()}",
                                    "template_version_id": templates[0]["template_version_id"],
                                    "name": "other 的 Agent", "workspace_id": "ws_pay_abc"}))["instance"]
    run = unwrap(client.post("/api/openops/v1/agent-runs", headers=OTHER_HEADERS,
                             json={"client_request_id": f"r_{time.time_ns()}",
                                   "agent_team_instance_id": inst["instance_id"]}))["run"]
    ok = _select(client, OTHER_HEADERS, run["agent_run_id"], "tx-llm-v2")
    assert ok.status_code == 200  # 遗留 restricted 资产也可选（授权在模板维度）
    r = _select(client, OTHER_HEADERS, run["agent_run_id"], "no-such-model")
    assert r.status_code == 403 and r.json()["error"]["code"] == "MODEL_NOT_AUTHORIZED"
    r2 = _select(client, OTHER_HEADERS, run["agent_run_id"], "claude-3-5-sonnet")
    assert r2.status_code == 403  # disabled 同样 fail-closed


async def test_model_acl_003_gateway_resolves_in_active_pool(monkeypatch):
    """Model Gateway（38.1）：在全 active 池内解析——选中不在池内 → 回退默认；空池 → stub（纯逻辑，不打 DB）。"""
    rows = [
        {"model_id": "glm-5.1", "display_name": "GLM-5.1",
         "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
         "secret_env_var": "OPENOPS_PLATFORM_GLM_API_KEY"},
    ]

    async def fake_active():  # 38.1：list_active 无参（旧 fake 带 _uid 会 TypeError）
        return rows

    monkeypatch.setattr(model_gateway.model_assets, "list_active", fake_active)
    spec = await model_gateway.resolve_runtime_model("gone-model", "u1")  # 选中不在 active 池
    assert spec is not None and spec["model_id"] == "glm-5.1"  # 忽略选中值回退默认
    assert spec["base_url"] == "https://open.bigmodel.cn/api/paas/v4"  # completions 后缀剥离

    monkeypatch.setattr(model_gateway.model_assets, "list_active", _empty)
    spec2 = await model_gateway.resolve_runtime_model("gone-model", "u1")
    assert spec2 is None  # 全无可用 → stub


async def _empty():
    return []


def test_model_acl_004_asset_grants_endpoints_removed(client):
    """38.1：资产级 grants 端点已移除——GET/PUT /admin/model-assets/{id}/grants 均 404。"""
    rows = unwrap(client.get("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS))
    aid = next(r["model_asset_id"] for r in rows if r["model_id"] == "glm-5.1")
    assert client.get(f"/api/openops/v1/admin/model-assets/{aid}/grants",
                      headers=ADMIN_HEADERS).status_code == 404
    assert client.put(f"/api/openops/v1/admin/model-assets/{aid}/grants", headers=ADMIN_HEADERS,
                      json={"client_request_id": "g1", "access_scope": "restricted",
                            "user_ids": ["0026demo01"]}).status_code == 404


def test_model_acl_006_admin_endpoints_forbidden_for_user(client):
    r = client.get("/api/openops/v1/admin/model-assets", headers=USER_HEADERS)
    assert r.status_code == 403


def test_model_acl_007_register_ignores_sensitive_and_dedups(client):
    """注册：DTO 白名单字段（api_key/access_scope 进不来）；model_id 去重；列废弃靠 DEFAULT 兜底。"""
    unwrap(client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                       json={"client_request_id": "m1", "display_name": "内部网关模型", "model_id": "gw-llm-1",
                             "secret_env_var": "OPENOPS_GW_KEY",
                             "api_key": "sk-should-never-persist",
                             "access_scope": "restricted"}))  # 38.1：多传被 Pydantic 忽略，不再有语义
    rows = unwrap(client.get("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS))
    gw = next(r for r in rows if r["model_id"] == "gw-llm-1")
    assert "api_key" not in gw and "sk-should-never-persist" not in str(gw)
    assert gw["access_scope"] == "all"  # 废弃列恒 DEFAULT 'all'（多传 restricted 无效）
    dup = client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                      json={"client_request_id": "m2", "display_name": "重复", "model_id": "gw-llm-1"})
    assert dup.status_code == 400


def test_register_rejects_key_value_in_secret_env_var(client):
    """SEC 护栏：secret_env_var 填成 Key 本身（如 sk-...）→ 入口拒绝，明文 Key 不落库。"""
    r = client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                    json={"client_request_id": "reg_k1", "display_name": "误填", "model_id": "oops-llm",
                          "base_url": "http://gw/v1", "secret_env_var": "sk-1234abcd"})
    assert r.status_code == 400
    assert "环境变量名" in r.json()["error"]["message"]

    r2 = client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                     json={"client_request_id": "reg_k2", "display_name": "正确", "model_id": "good-llm",
                           "base_url": "http://gw/v1", "secret_env_var": "OPENOPS_TX_LLM_KEY"})
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
    for k in ("display_name", "secret_env_var", "context_window_tokens", "model_id", "status"):
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


def test_model_acl_005_legacy_binding_open(client):
    """legacy platform_model_id 绑定（38.1 退化）：遗留 restricted 资产也可绑（存在+active 即可）。"""
    inst = create_instance(client, name="legacy 绑 tx")
    run = create_run(client, inst["instance_id"])
    assert run["agent_run_id"]
    r = client.post(f"/api/openops/v1/agent-teams/{inst['instance_id']}:update", headers=USER_HEADERS,
                    json={"client_request_id": f"u_{time.time_ns()}", "name": "legacy 绑 tx",
                          "workspace_id": "ws_pay_abc", "user_llm_config_id": None,
                          "model_template_id": None, "platform_model_id": "tx-llm-v2"})
    assert r.status_code == 200
    r2 = client.post(f"/api/openops/v1/agent-teams/{inst['instance_id']}:update", headers=USER_HEADERS,
                     json={"client_request_id": f"u_{time.time_ns()}", "name": "legacy 绑 tx",
                           "workspace_id": "ws_pay_abc", "user_llm_config_id": None,
                           "model_template_id": None, "platform_model_id": "claude-3-5-sonnet"})
    assert r2.status_code == 403  # disabled 仍 fail-closed
