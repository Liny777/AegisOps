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
         "model_asset_id": "11111111-1111-1111-1111-111111111111",
         "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
         "has_secret": True},  # 2026-08-17：可用性判据是「密文列有 Key」，不再是「填了环境变量名」
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
    """注册：DTO 白名单字段（access_scope / secret_env_var 进不来）；model_id 去重；列废弃靠 DEFAULT 兜底。"""
    unwrap(client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                       json={"client_request_id": "m1", "display_name": "内部网关模型", "model_id": "gw-llm-1",
                             "secret_env_var": "OPENOPS_GW_KEY",   # 2026-08-17 废弃：多传被忽略
                             "access_scope": "restricted"}))  # 38.1：多传被 Pydantic 忽略，不再有语义
    rows = unwrap(client.get("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS))
    gw = next(r for r in rows if r["model_id"] == "gw-llm-1")
    assert gw["secret_env_var"] is None  # 新注册不写废弃列，Key 一律走密文列
    assert gw["access_scope"] == "all"  # 废弃列恒 DEFAULT 'all'（多传 restricted 无效）
    dup = client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                      json={"client_request_id": "m2", "display_name": "重复", "model_id": "gw-llm-1"})
    assert dup.status_code == 400


# ---- API Key 密文入库（2026-08-17：替代原「Key 只进环境变量」口径）----

def test_model_asset_api_key_never_leaves_db_in_plaintext(client):
    """SEC-001 核心护栏：Key 落库即密文，管理台/用户侧接口都取不到明文，也取不到密文本身。

    仓储的 `_PUBLIC_COLS` 是这条的实现点——一旦有人把它改回 `select *`，本用例即红。"""
    key = "sk-must-never-be-readable-1234"
    unwrap(client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                       json={"client_request_id": "keyreg1", "display_name": "带 Key 的模型",
                             "model_id": "keyed-llm", "base_url": "http://gw/v1", "api_key": key}))

    admin_rows = client.get("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS).text
    user_rows = client.get("/api/openops/v1/models/platform", headers=USER_HEADERS).text
    for body in (admin_rows, user_rows):
        assert key not in body                 # 明文不回显
        assert "secret_ciphertext" not in body  # 密文本身也不出库

    row = _asset(client, "keyed-llm")
    assert row["has_secret"] is True
    assert row["secret_fingerprint"].startswith("fp_")  # 唯一可回显的密钥信息
    assert key not in row["secret_fingerprint"]


def test_model_asset_api_key_roundtrip_and_audit_redaction(client):
    """加密往返：写进去的明文能在调用边界解回来；同时审计事件里没有任何明文痕迹。"""
    import asyncio

    from infra import crypto
    from infra.repositories import model_assets

    key = "sk-roundtrip-abcdef"
    unwrap(client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                       json={"client_request_id": "keyreg2", "display_name": "往返", "model_id": "rt-llm",
                             "api_key": key}))
    aid = _asset(client, "rt-llm")["model_asset_id"]

    async def _decrypt() -> str:
        mat = await model_assets.get_secret_material(aid)
        assert mat is not None
        return crypto.decrypt(mat["secret_ciphertext"])

    assert asyncio.run(_decrypt()) == key

    _update(client, ADMIN_HEADERS, aid, {"api_key": "sk-rotated-999"})
    recent = client.get("/api/openops/v1/admin/audit/recent", headers=ADMIN_HEADERS).text
    assert "sk-rotated-999" not in recent and key not in recent
    assert asyncio.run(_decrypt()) == "sk-rotated-999"  # 轮换生效


def test_model_asset_api_key_patch_tristate(client):
    """PATCH 三态：不传=不动（改地址不必重录 Key）、传空串=清除、传值=覆盖。"""
    unwrap(client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                       json={"client_request_id": "keyreg3", "display_name": "三态", "model_id": "tri-llm",
                             "api_key": "sk-original"}))
    row = _asset(client, "tri-llm")
    aid, fp0 = row["model_asset_id"], row["secret_fingerprint"]

    # 不传 api_key：只改 base_url，密钥原样不动
    assert _update(client, ADMIN_HEADERS, aid, {"base_url": "http://new.gw/v1"}).status_code == 200
    after = _asset(client, "tri-llm")
    assert after["has_secret"] is True and after["secret_fingerprint"] == fp0

    # 传值：覆盖（指纹随之变化）
    assert _update(client, ADMIN_HEADERS, aid, {"api_key": "sk-replaced"}).status_code == 200
    assert _asset(client, "tri-llm")["secret_fingerprint"] != fp0

    # 传空串：清除
    assert _update(client, ADMIN_HEADERS, aid, {"api_key": ""}).status_code == 200
    cleared = _asset(client, "tri-llm")
    assert cleared["has_secret"] is False and cleared["secret_fingerprint"] is None


def test_model_asset_key_backfilled_from_env_on_seed(client, monkeypatch):
    """一次性 backfill：把 secret_env_var 指向的环境变量加密进密文列。
    这是存量库迁移当天不跌 stub 的唯一依靠——env 只在这一次出现，运行时不再读它。

    conftest 刻意不注入平台 Key（否则整片 agentscope 用例会去打真网），所以这里临时注入再手动跑
    一次 backfill；顺带验证「只补空的、不覆盖已录入的」。"""
    import asyncio

    from infra import seed

    glm = _asset(client, "glm-5.1")
    assert glm["has_secret"] is False  # 基线：测试环境无 Key（= 回退 stub）

    monkeypatch.setenv("OPENOPS_PLATFORM_GLM_API_KEY", "sk-from-env-once")
    asyncio.run(seed.ensure_platform_key_backfill())
    after = _asset(client, "glm-5.1")
    assert after["has_secret"] is True and after["secret_fingerprint"].startswith("fp_")

    # 不覆盖已有：换个环境变量值再跑，指纹不变（管理台录入的 Key 不会被 env 悄悄顶掉）
    fp = after["secret_fingerprint"]
    monkeypatch.setenv("OPENOPS_PLATFORM_GLM_API_KEY", "sk-different-value")
    asyncio.run(seed.ensure_platform_key_backfill())
    assert _asset(client, "glm-5.1")["secret_fingerprint"] == fp


def test_model_acl_default_flag_points_to_runnable_default(client):
    """is_default = 运行时真实默认（OPENOPS_RUNTIME_MODEL=glm-5.1，且密文列真有 Key 才能跑）。

    2026-08-17 判据收紧：从「填了环境变量名」变成「库里真有 Key」——一个 Key 都没配时**没有默认**
    （诚实：此时任何 run 都会跌 stub）。给 glm 配上 Key 后它才成为默认，而非列表首位的 Qwen3.5。"""
    none_yet = unwrap(client.get("/api/openops/v1/models/platform", headers=USER_HEADERS))
    assert all(r["is_default"] is False for r in none_yet)  # 无 Key → 无默认

    aid = _asset(client, "glm-5.1")["model_asset_id"]
    assert _update(client, ADMIN_HEADERS, aid, {"api_key": "sk-glm"}).status_code == 200

    rows = unwrap(client.get("/api/openops/v1/models/platform", headers=USER_HEADERS))
    by_id = {r["model_id"]: r for r in rows}
    assert by_id["glm-5.1"]["is_default"] is True            # 真正能跑的默认
    assert by_id["qwen3.5-instruct"]["is_default"] is False  # 无 Key，不会被当默认
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
    for k in ("display_name", "secret_fingerprint", "has_secret", "context_window_tokens",
              "model_id", "status"):
        assert after[k] == before[k], f"{k} 不该被这次更新改动"


def test_model_asset_update_explicit_null_clears_base_url(client):
    """显式传 null 与「没传」必须区分：前者清空（走平台网关的模型不填 base_url）。"""
    aid = _asset(client, "glm-5.1")["model_asset_id"]
    assert _update(client, ADMIN_HEADERS, aid, {"base_url": None}).status_code == 200
    assert _asset(client, "glm-5.1")["base_url"] is None


def test_model_asset_update_ignores_deprecated_secret_env_var(client):
    """secret_env_var 已从 DTO 移除（2026-08-17）：旧客户端仍传它 → 被 Pydantic 忽略，
    既不写坏该列、也不会被误当成 Key。"""
    aid = _asset(client, "glm-5.1")["model_asset_id"]
    before = _asset(client, "glm-5.1")
    r = _update(client, ADMIN_HEADERS, aid, {"secret_env_var": "sk-1234abcd", "display_name": "GLM-5.1"})
    assert r.status_code == 200
    after = _asset(client, "glm-5.1")
    assert after["secret_env_var"] == before["secret_env_var"]  # 未被写坏
    assert after["secret_fingerprint"] == before["secret_fingerprint"]  # 也没被当成 Key 写进密文列


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


def test_model_asset_delete_blocked_when_referenced(client):
    """删资产（38.2）fail-closed：被模板槽位引用 → 400 提示先调整模板（seed 模板引用 glm-5.1）。"""
    aid = _asset(client, "glm-5.1")["model_asset_id"]
    r = client.delete(f"/api/openops/v1/admin/model-assets/{aid}", headers=ADMIN_HEADERS)
    assert r.status_code == 400 and "模型模板引用" in r.json()["error"]["message"]
    assert _asset(client, "glm-5.1")["status"] == "active"  # 未被误删


def test_model_asset_delete_and_reregister(client):
    """删未被引用的资产：列表消失 → 同 model_id 可重注册（唯一索引带 WHERE deleted_at IS NULL）；
    重复删 404、非管理员 403、写审计 model_asset.deleted。"""
    unwrap(client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                       json={"client_request_id": "d1", "display_name": "临时模型", "model_id": "tmp-llm-1",
                             "api_key": "sk-tmp"}))
    aid = _asset(client, "tmp-llm-1")["model_asset_id"]
    assert client.delete(f"/api/openops/v1/admin/model-assets/{aid}",
                         headers=USER_HEADERS).status_code == 403  # 非管理员
    out = unwrap(client.request("DELETE", f"/api/openops/v1/admin/model-assets/{aid}",
                                headers=ADMIN_HEADERS))
    assert out["deleted"] is True
    rows = unwrap(client.get("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS))
    assert all(r["model_id"] != "tmp-llm-1" for r in rows)
    assert "tmp-llm-1" not in _platform_models(client, USER_HEADERS)  # 用户侧同步消失
    assert client.delete(f"/api/openops/v1/admin/model-assets/{aid}",
                         headers=ADMIN_HEADERS).status_code == 404  # 重复删

    # 同 model_id 重注册 OK
    assert client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                       json={"client_request_id": "d2", "display_name": "临时模型二代",
                             "model_id": "tmp-llm-1"}).status_code == 200
    recent = unwrap(client.get("/api/openops/v1/admin/audit/recent", headers=ADMIN_HEADERS))
    assert any(e["event_type"] == "model_asset.deleted" for e in recent)


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


# ---- 平台模型资产的自定义出站 Header（与用户自带模型对齐）----

def test_model_asset_register_and_update_extra_headers(client):
    """注册时带 header → 落库；PUT 改 header → 生效；传 {} → 清空。
    jsonb 列走 update_fields 白名单，dict 需经 jsonb() 适配，直接绑参会被 psycopg 拒。"""
    mid = f"hdr-test-{time.time_ns()}"
    unwrap(client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                       json={"client_request_id": f"reg_{time.time_ns()}", "display_name": "HdrAsset",
                             "model_id": mid, "protocol": "openai_compatible",
                             "base_url": "https://1.1.1.1/v1", "api_key": "sk-hdr",
                             "extra_headers": {"X-Tenant-Id": "t-plat"}}))
    row = _asset(client, mid)
    assert (row["extra_params_json"] or {}).get("extra_headers") == {"X-Tenant-Id": "t-plat"}

    aid = row["model_asset_id"]
    assert _update(client, ADMIN_HEADERS, aid, {"extra_headers": {"X-Route-Env": "prod"}}).status_code == 200
    assert (_asset(client, mid)["extra_params_json"] or {}).get("extra_headers") == {"X-Route-Env": "prod"}

    assert _update(client, ADMIN_HEADERS, aid, {"extra_headers": {}}).status_code == 200
    assert (_asset(client, mid)["extra_params_json"] or {}).get("extra_headers", {}) == {}

    # 其余列不被这次 header 改动殃及（PATCH 语义）——密钥尤其不能被连带清掉
    assert _asset(client, mid)["has_secret"] is True


def test_model_asset_rejects_reserved_headers(client):
    """保留头在平台侧同样被拒（与自带模型共用 validate_extra_headers）：
    Key 走 api_key 字段进加密链，不许经 header 明文旁路（SEC-001）。"""
    aid = _asset(client, "glm-5.1")["model_asset_id"]
    for bad in ({"Authorization": "Bearer x"}, {"Host": "evil.internal"}):
        assert _update(client, ADMIN_HEADERS, aid, {"extra_headers": bad}).status_code in (400, 422)
    r = client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                    json={"client_request_id": f"reg_{time.time_ns()}", "display_name": "Bad",
                          "model_id": f"bad-{time.time_ns()}", "protocol": "openai_compatible",
                          "extra_headers": {"X-Evil": "a\r\nX-Inject: b"}})
    assert r.status_code in (400, 422)


async def test_model_asset_headers_reach_spec_and_probe(client, monkeypatch):
    """平台 header 全链：落库 → _spec 带出（runtime 据此注入 default_headers）；
    「测试连接」与真实调用同源携带，否则测通了也可能跑不通。"""
    import httpx

    from app import model_asset_service, model_gateway
    from infra.external import llm_provider_client
    from infra.repositories import model_assets

    mid = f"spec-hdr-{time.time_ns()}"
    unwrap(client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                       json={"client_request_id": f"reg_{time.time_ns()}", "display_name": "SpecHdr",
                             "model_id": mid, "protocol": "openai_compatible",
                             "base_url": "https://1.1.1.1/v1", "api_key": "sk-platform",
                             "extra_headers": {"X-Tenant-Id": "t-spec"}}))
    row = await model_assets.get_by_model_id(mid)
    spec = model_gateway._spec(row)
    assert spec["extra_headers"] == {"X-Tenant-Id": "t-spec"}
    # spec 只带密钥**引用**，绝不带 Key（runtime 在构建边界才解密）
    assert spec["model_asset_id"] == str(row["model_asset_id"]) and spec["has_secret"] is True
    assert "sk-platform" not in str(spec)

    # 「测试连接」把 header 真的发出去（探测与真跑同源）
    monkeypatch.setenv("OPENOPS_LLM_PROBE", "real")
    seen: dict[str, str] = {}

    class _SpyClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            seen.update(headers or {})

            class _R:
                status_code = 200
                text = ""

            return _R()

    monkeypatch.setattr(httpx, "AsyncClient", _SpyClient)
    assert llm_provider_client  # 探测走的就是这个模块（上面 monkeypatch 的是它用的 httpx）
    # 编辑态口径：不重填 Key，只给 model_asset_id —— 服务端解密库里那把去探测
    req = type("R", (), {"base_url": "https://1.1.1.1/v1", "model_id": mid,
                         "api_key": "", "model_asset_id": str(row["model_asset_id"]),
                         "extra_headers": {"X-Tenant-Id": "t-spec"}})()
    res = await model_asset_service.test_connection(req)
    assert res["ok"] is True
    assert seen["X-Tenant-Id"] == "t-spec"
    assert seen["Authorization"] == "Bearer sk-platform"
