"""用户自定义 LLM 全链闭合（探测真化 + 实例默认绑定）。

- MODEL-001：录 Secret → 建 llm-config（mock 探测通过）→ active，出现在列表。
- MODEL-002：model_name 含 "no-tool" → MODEL_PROBE_FAILED，不落库。
- 实例默认绑定：initial_overlay_json.user_llm_config_id → 起任务后 /state.selected_model = 该配置（overlay 被真消费，非死字段）。
- probe real 门控：OPENOPS_LLM_PROBE=real 走真 HTTP 分支，可达=200→ok；连接错→优雅 ok=False（不崩、reason 脱敏）。
- probe 默认 real（005）：不设/拼错环境变量都走真探测，只有显式 mock 才假探测且自曝 probe_mode=mock。

注：conftest 用 setdefault 把整套测试钉在 OPENOPS_LLM_PROBE=mock（离线），故本文件里
「mock 探测」的用例无需自己 setenv；要验真分支的用例自行 monkeypatch。
"""
from __future__ import annotations

import time

from conftest import ADMIN_HEADERS, USER_HEADERS, create_run, unwrap, wait_until

# base_url 用公网 IP 字面量：过 egress SSRF 校验且不依赖 DNS（同 test_sec_009）
_BASE = "https://1.1.1.1/v1"


def _mk_secret(client, val: str = "sk-user-key") -> dict:
    return unwrap(client.post("/api/openops/v1/secrets", headers=USER_HEADERS,
                              json={"client_request_id": f"s_{time.time_ns()}", "secret_name": "我的 Key",
                                    "secret_type": "api_key", "provider": "openai_compatible", "secret_value": val}))


def _mk_llm(client, secret_ref: str, model: str = "gpt-4o", name: str = "我的自定义模型"):
    return client.post("/api/openops/v1/llm-configs", headers=USER_HEADERS,
                       json={"client_request_id": f"l_{time.time_ns()}", "display_name": name,
                             "provider": "openai_compatible", "base_url": _BASE, "model_name": model,
                             "secret_ref_id": secret_ref})


def test_user_llm_001_create_probe_ok(client):
    sec = _mk_secret(client)
    cfg = unwrap(_mk_llm(client, sec["secret_ref_id"]))
    assert cfg["llm_config_id"]
    listed = unwrap(client.get("/api/openops/v1/llm-configs", headers=USER_HEADERS))
    row = next(c for c in listed if c["llm_config_id"] == cfg["llm_config_id"])
    assert row["model_name"] == "gpt-4o"
    assert row["supports_tool_calling"] is True and row["status"] == "active"


def test_user_llm_002_probe_rejects_no_tool(client):
    sec = _mk_secret(client)
    r = _mk_llm(client, sec["secret_ref_id"], model="gpt-4o-no-tool")
    assert r.status_code == 400 and r.json()["error"]["code"] == "MODEL_PROBE_FAILED"
    listed = unwrap(client.get("/api/openops/v1/llm-configs", headers=USER_HEADERS))
    assert all("no-tool" not in c["model_name"] for c in listed)  # 探测失败不落库


def test_user_llm_003_instance_default_binds_via_overlay(client):
    """InitWizard custom 分支：实例 overlay 绑用户 LLM → 起任务即作为默认模型（selected_model=该配置）。"""
    sec = _mk_secret(client)
    cfg = unwrap(_mk_llm(client, sec["secret_ref_id"]))
    templates = unwrap(client.get("/api/openops/v1/templates/available", headers=USER_HEADERS))
    inst = unwrap(client.post("/api/openops/v1/agent-teams", headers=USER_HEADERS,
                              json={"client_request_id": f"i_{time.time_ns()}",
                                    "template_version_id": templates[0]["template_version_id"],
                                    "name": "绑定自定义模型的 Agent", "workspace_id": "ws_pay_abc",
                                    "initial_overlay_json": {"user_llm_config_id": cfg["llm_config_id"]}}))["instance"]
    run = create_run(client, inst["instance_id"])
    unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks", headers=USER_HEADERS,
                       json={"client_request_id": f"t_{time.time_ns()}", "input_text": "排查支付延迟"}))
    state = wait_until(lambda: _state_with_model(client, run["agent_run_id"]))
    assert state is not None
    assert state["active_task"]["selected_model"] == cfg["llm_config_id"]


def _state_with_model(client, run_id: str):
    s = unwrap(client.get(f"/api/openops/v1/agent-runs/{run_id}/state", headers=USER_HEADERS))
    return s if (s.get("active_task") or {}).get("selected_model") else None


async def test_user_llm_004_probe_real_gate_graceful(monkeypatch):
    """OPENOPS_LLM_PROBE=real：可达(200)→ok；连接错→优雅 ok=False（reason 脱敏，不含 Key/url）。"""
    import httpx

    from infra.external import llm_provider_client

    monkeypatch.setenv("OPENOPS_LLM_PROBE", "real")

    class _Resp:
        def __init__(self, status: int):
            self.status_code = status
            self.text = ""

    class _OkClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp(200)

    monkeypatch.setattr(httpx, "AsyncClient", _OkClient)
    ok = await llm_provider_client.probe("https://api.example.com/v1", "gpt-4o", "sk-secret")
    assert ok["ok"] and ok["supports_tool_calling"]

    class _BoomClient(_OkClient):
        async def post(self, *a, **k):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", _BoomClient)
    bad = await llm_provider_client.probe("https://api.example.com/v1", "gpt-4o", "sk-secret")
    assert bad["ok"] is False and "不可达" in bad["reason"]
    assert "sk-secret" not in bad["reason"] and "api.example.com" not in bad["reason"]


async def test_user_llm_005_probe_defaults_to_real(monkeypatch):
    """探测默认必须是 real（fail-closed 回归）：不设 OPENOPS_LLM_PROBE 时应真发 HTTP，而非
    静默走 mock 假通过——默认曾是 mock，导致线上「测试连接」对任何 Key 都绿勾。
    连拼错的值（"ral"）也须往 real 倒；只有显式 "mock" 才假探测。"""
    import httpx

    from infra.external import llm_provider_client

    posted: list[str] = []

    class _SpyClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, *a, **k):
            posted.append(url)

            class _R:
                status_code = 200
                text = ""

            return _R()

    monkeypatch.setattr(httpx, "AsyncClient", _SpyClient)

    for env in (None, "ral", "REAL"):  # 未设 / 拼错 / 大小写：一律 real
        monkeypatch.delenv("OPENOPS_LLM_PROBE", raising=False)
        if env is not None:
            monkeypatch.setenv("OPENOPS_LLM_PROBE", env)
        posted.clear()
        r = await llm_provider_client.probe("https://api.example.com/v1", "gpt-4o", "sk-x")
        assert r["ok"] and r["probe_mode"] == "real", f"env={env!r} 应走真探测"
        assert posted == ["https://api.example.com/v1/chat/completions"], f"env={env!r} 应真发 HTTP"

    # 只有显式 mock 才假探测，且自曝 probe_mode=mock（供前端显示「未真实探测」而非绿勾）
    monkeypatch.setenv("OPENOPS_LLM_PROBE", "mock")
    posted.clear()
    m = await llm_provider_client.probe("https://api.example.com/v1", "gpt-4o", "sk-x")
    assert m["ok"] and m["probe_mode"] == "mock"
    assert posted == []  # mock 不打网


def test_test_connection_gate_mock(client):
    """用户自带模型「测试连接」端点（存前探测、不落库）：mock 探测下正常模型 ok=true，
    含 no-tool 的模型 ok=false 且带 reason；SSRF 地址 ok=false（egress 拦截）。"""
    good = unwrap(client.post("/api/openops/v1/llm-configs:test-connection", headers=USER_HEADERS,
                              json={"base_url": _BASE, "model_name": "gpt-4o", "api_key": "sk-x"}))
    assert good["ok"] is True and good["supports_tool_calling"] is True
    assert good["probe_mode"] == "mock"  # 透传到端点：前端据此显示「未真实探测」而非绿勾

    bad = unwrap(client.post("/api/openops/v1/llm-configs:test-connection", headers=USER_HEADERS,
                             json={"base_url": _BASE, "model_name": "foo-no-tool", "api_key": "sk-x"}))
    assert bad["ok"] is False and bad["reason"]

    ssrf = unwrap(client.post("/api/openops/v1/llm-configs:test-connection", headers=USER_HEADERS,
                              json={"base_url": "http://localhost/v1", "model_name": "gpt-4o", "api_key": "sk-x"}))
    assert ssrf["ok"] is False and ssrf["reason"]  # egress SSRF 拦截，返回原因而非 500

    # 测试连接不落库：调用后 llm-configs 列表不新增
    assert unwrap(client.get("/api/openops/v1/llm-configs", headers=USER_HEADERS)) == []


def test_admin_model_asset_test_connection(client, monkeypatch):
    """平台模型「测试连接」：Key 走服务器环境变量（secret_env_var 名）；未配变量 / 缺 base_url →
    ok=false 带清晰 reason；环境变量已注入 + base_url 合法 → mock 探测 ok=true。"""
    from conftest import ADMIN_HEADERS

    # 环境变量未注入 → 明确 reason（不 500）
    no_env = unwrap(client.post("/api/openops/v1/admin/model-assets:test-connection", headers=ADMIN_HEADERS,
                                json={"base_url": _BASE, "model_id": "glm-5.1", "secret_env_var": "OPENOPS_NOT_SET_XYZ"}))
    assert no_env["ok"] is False and "OPENOPS_NOT_SET_XYZ" in no_env["reason"]

    # 缺 base_url → 明确 reason
    no_url = unwrap(client.post("/api/openops/v1/admin/model-assets:test-connection", headers=ADMIN_HEADERS,
                                json={"base_url": "", "model_id": "glm-5.1", "secret_env_var": ""}))
    assert no_url["ok"] is False and "base_url" in no_url["reason"]

    # 环境变量已注入 + base_url 合法 → mock 探测通过
    monkeypatch.setenv("OPENOPS_TESTKEY_ENV", "sk-platform")
    good = unwrap(client.post("/api/openops/v1/admin/model-assets:test-connection", headers=ADMIN_HEADERS,
                              json={"base_url": _BASE, "model_id": "glm-5.1", "secret_env_var": "OPENOPS_TESTKEY_ENV"}))
    assert good["ok"] is True


def test_register_model_asset_persists_context_window(client):
    """管理台注册平台模型带上下文长度（新增列）：注册后可见（走 admin/model-assets 列表）。"""
    from conftest import ADMIN_HEADERS

    unwrap(client.post("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS,
                       json={"client_request_id": f"reg_{time.time_ns()}", "display_name": "CtxTest",
                             "model_id": f"ctx-test-{time.time_ns()}", "protocol": "openai_compatible",
                             "base_url": _BASE, "secret_env_var": "OPENOPS_CTX_KEY",
                             "context_window_tokens": 200000}))
    rows = unwrap(client.get("/api/openops/v1/admin/model-assets", headers=ADMIN_HEADERS))
    row = next(r for r in rows if r["display_name"] == "CtxTest")
    assert row["context_window_tokens"] == 200000


def test_user_llm_006_extra_headers_forbidden_names_rejected(client):
    """高级选项自定义 Header：Authorization/Host 等保留头在 schema 层被拒（400/422），不落库。
    鉴权只走 secret 加密链，禁止经 extra_params_json 明文旁路（SEC-001 口径）。"""
    sec = _mk_secret(client)
    for bad in ({"Authorization": "Bearer sneaky"}, {"Host": "evil.internal"}, {"X-Forwarded-For": "127.0.0.1"}):
        r = client.post("/api/openops/v1/llm-configs", headers=USER_HEADERS,
                        json={"client_request_id": f"l_{time.time_ns()}", "display_name": "带非法Header",
                              "provider": "openai_compatible", "base_url": _BASE, "model_name": "gpt-4o",
                              "secret_ref_id": sec["secret_ref_id"], "extra_headers": bad})
        assert r.status_code in (400, 422), f"{bad} 应被拒，实际 {r.status_code}"
    listed = unwrap(client.get("/api/openops/v1/llm-configs", headers=USER_HEADERS))
    assert all(c["display_name"] != "带非法Header" for c in listed)
    # 值含 CR/LF（header 注入）同样拒
    r = client.post("/api/openops/v1/llm-configs:test-connection", headers=USER_HEADERS,
                    json={"base_url": _BASE, "model_name": "gpt-4o", "api_key": "sk",
                          "extra_headers": {"X-Tenant-Id": "a\r\nX-Evil: b"}})
    assert r.status_code in (400, 422)


async def test_user_llm_007_extra_headers_persist_and_reach_spec(client):
    """自定义 Header 全链（存→取）：创建时落 extra_params_json，_user_llm_spec 原样带出——
    spec 整体透传 runtime/_build_model 与子 Agent，此处即出站生效的事实来源。"""
    from app import model_gateway, secret_model_gateway
    from domain.schemas import CreateLlmConfigRequest, CreateSecretRequest
    from infra.repositories import secrets as secrets_repo

    # async 测试内不走 TestClient（anyio portal 会互卡），直接驱动网关层；client fixture 仅用于重置 DB
    user = {"user_id": "0026demo01"}
    sec = await secret_model_gateway.create_secret(user, CreateSecretRequest(
        client_request_id="s_hdr", secret_name="hdr key", secret_value="sk-user-key"))
    hdrs = {"X-Tenant-Id": "t-001", "X-Route-Env": "prod"}
    cfg = await secret_model_gateway.create_llm_config(user, CreateLlmConfigRequest(
        client_request_id="l_hdr", display_name="带Header模型", base_url=_BASE, model_name="gpt-4o",
        secret_ref_id=sec["secret_ref_id"], extra_headers=hdrs))
    row = await secrets_repo.get_llm_config(cfg["llm_config_id"])
    assert (row["extra_params_json"] or {}).get("extra_headers") == hdrs
    spec = await model_gateway._user_llm_spec(cfg["llm_config_id"], "0026demo01")
    assert spec is not None and spec["extra_headers"] == hdrs


async def test_user_llm_008_probe_real_sends_extra_headers(monkeypatch):
    """real 探测与真实调用同源携带自定义 Header：探测请求带上 extra_headers，
    且 Authorization 仍由 api_key 生成（自定义头被 schema 禁配，不可能覆盖鉴权）。"""
    import httpx

    from infra.external import llm_provider_client

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
    ok = await llm_provider_client.probe("https://api.example.com/v1", "gpt-4o", "sk-secret",
                                         {"X-Tenant-Id": "t-001"})
    assert ok["ok"]
    assert seen["X-Tenant-Id"] == "t-001"
    assert seen["Authorization"] == "Bearer sk-secret"


# ---- 自带模型可看/可改/可删（PATCH / DELETE /llm-configs/{id}）----

def _patch(client, headers, cfg_id: str, body: dict):
    return client.patch(f"/api/openops/v1/llm-configs/{cfg_id}", headers=headers,
                        json={"client_request_id": f"upd_{time.time_ns()}", **body})


def _listed(client, cfg_id: str) -> dict:
    rows = unwrap(client.get("/api/openops/v1/llm-configs", headers=USER_HEADERS))
    return next(c for c in rows if c["llm_config_id"] == cfg_id)


def test_user_llm_009_list_returns_extra_headers(client):
    """列表回显自定义 Header——否则用户看不到自己配了什么，改都无从改起。"""
    sec = _mk_secret(client)
    cfg = unwrap(client.post("/api/openops/v1/llm-configs", headers=USER_HEADERS,
                             json={"client_request_id": f"l_{time.time_ns()}", "display_name": "带头模型",
                                   "provider": "openai_compatible", "base_url": _BASE, "model_name": "gpt-4o",
                                   "secret_ref_id": sec["secret_ref_id"],
                                   "extra_headers": {"X-Tenant-Id": "t-001"}}))
    row = _listed(client, cfg["llm_config_id"])
    assert (row["extra_params_json"] or {}).get("extra_headers") == {"X-Tenant-Id": "t-001"}


def test_user_llm_010_patch_only_given_keys(client):
    """PATCH 语义：只改传入的键，其余原样（含 header 不被顺手清空）。"""
    sec = _mk_secret(client)
    cfg = unwrap(client.post("/api/openops/v1/llm-configs", headers=USER_HEADERS,
                             json={"client_request_id": f"l_{time.time_ns()}", "display_name": "旧名",
                                   "provider": "openai_compatible", "base_url": _BASE, "model_name": "gpt-4o",
                                   "secret_ref_id": sec["secret_ref_id"],
                                   "extra_headers": {"X-Tenant-Id": "t-001"}}))
    cid = cfg["llm_config_id"]
    assert _patch(client, USER_HEADERS, cid, {"display_name": "新名"}).status_code == 200

    row = _listed(client, cid)
    assert row["display_name"] == "新名"
    assert row["model_name"] == "gpt-4o" and row["base_url"] == _BASE
    assert (row["extra_params_json"] or {}).get("extra_headers") == {"X-Tenant-Id": "t-001"}


def test_user_llm_011_patch_headers_replace_and_clear(client):
    """header 传什么就是什么（整体替换）；显式传 {} 即清空。"""
    sec = _mk_secret(client)
    cfg = unwrap(client.post("/api/openops/v1/llm-configs", headers=USER_HEADERS,
                             json={"client_request_id": f"l_{time.time_ns()}", "display_name": "改头模型",
                                   "provider": "openai_compatible", "base_url": _BASE, "model_name": "gpt-4o",
                                   "secret_ref_id": sec["secret_ref_id"],
                                   "extra_headers": {"X-Tenant-Id": "t-001"}}))
    cid = cfg["llm_config_id"]
    assert _patch(client, USER_HEADERS, cid, {"extra_headers": {"X-Route-Env": "prod"}}).status_code == 200
    assert (_listed(client, cid)["extra_params_json"] or {}).get("extra_headers") == {"X-Route-Env": "prod"}

    assert _patch(client, USER_HEADERS, cid, {"extra_headers": {}}).status_code == 200
    assert (_listed(client, cid)["extra_params_json"] or {}).get("extra_headers", {}) == {}

    # 保留头在 PATCH 路径上同样被拒（更新不能成为绕过创建期校验的后门）
    assert _patch(client, USER_HEADERS, cid,
                  {"extra_headers": {"Authorization": "Bearer x"}}).status_code in (400, 422)


def test_user_llm_012_patch_reprobes_and_rejects_bad_model(client):
    """改连接类字段强制重探测：探测不过则整条不落库（与创建同门禁）。"""
    sec = _mk_secret(client)
    cfg = unwrap(_mk_llm(client, sec["secret_ref_id"]))
    cid = cfg["llm_config_id"]
    r = _patch(client, USER_HEADERS, cid, {"model_name": "gpt-4o-no-tool"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "MODEL_PROBE_FAILED"
    assert _listed(client, cid)["model_name"] == "gpt-4o"  # 未被写坏


def test_user_llm_013_patch_rejects_empty_and_cross_user(client):
    """空 body → 400；NOT NULL 列显式 null → 400；改他人配置 → 404。

    越权用 admin 断言：自带模型绑的是**用户私有** Secret，管理员也不该经用户接口改写它；
    且 404 而非 403——不区分「不存在」与「是别人的」，免得接口成了 UUID 存在性探测旁路。
    """
    sec = _mk_secret(client)
    cid = unwrap(_mk_llm(client, sec["secret_ref_id"]))["llm_config_id"]
    assert _patch(client, USER_HEADERS, cid, {}).status_code == 400
    assert _patch(client, USER_HEADERS, cid, {"display_name": None}).status_code in (400, 422)

    assert _patch(client, ADMIN_HEADERS, cid, {"display_name": "越权改"}).status_code == 404
    assert _listed(client, cid)["display_name"] == "我的自定义模型"


def test_user_llm_014_delete_soft_deletes_and_revokes_secret(client):
    """删除：从列表消失，且专属 Secret 连带作废（不留无人认领的孤儿密钥）。"""
    sec = _mk_secret(client)
    cid = unwrap(_mk_llm(client, sec["secret_ref_id"]))["llm_config_id"]
    assert client.delete(f"/api/openops/v1/llm-configs/{cid}", headers=USER_HEADERS).status_code == 200

    rows = unwrap(client.get("/api/openops/v1/llm-configs", headers=USER_HEADERS))
    assert all(c["llm_config_id"] != cid for c in rows)
    secs = unwrap(client.get("/api/openops/v1/secrets", headers=USER_HEADERS))
    assert all(s["secret_ref_id"] != sec["secret_ref_id"] for s in secs)


def test_user_llm_015_delete_blocked_while_bound_to_agent(client):
    """仍被某 Agent 当前配置绑定时拒删——真删了那些 Agent 每次起任务都 403（运行时是硬失败）。"""
    sec = _mk_secret(client)
    cid = unwrap(_mk_llm(client, sec["secret_ref_id"]))["llm_config_id"]
    templates = unwrap(client.get("/api/openops/v1/templates/available", headers=USER_HEADERS))
    unwrap(client.post("/api/openops/v1/agent-teams", headers=USER_HEADERS,
                       json={"client_request_id": f"i_{time.time_ns()}",
                             "template_version_id": templates[0]["template_version_id"],
                             "name": "占用该模型的 Agent", "workspace_id": "ws_pay_abc",
                             "initial_overlay_json": {"user_llm_config_id": cid}}))
    r = client.delete(f"/api/openops/v1/llm-configs/{cid}", headers=USER_HEADERS)
    assert r.status_code == 400
    msg = r.json()["error"]["message"]
    assert "占用该模型的 Agent" in msg  # 报出占用方名字，用户知道去哪换绑
    assert _listed(client, cid)["llm_config_id"] == cid  # 仍在


def test_user_llm_016_delete_cross_user_is_404(client):
    """删他人配置 → 404（同 PATCH 口径），且对方配置完好。"""
    sec = _mk_secret(client)
    cid = unwrap(_mk_llm(client, sec["secret_ref_id"]))["llm_config_id"]
    assert client.delete(f"/api/openops/v1/llm-configs/{cid}", headers=ADMIN_HEADERS).status_code == 404
    assert _listed(client, cid)["llm_config_id"] == cid
