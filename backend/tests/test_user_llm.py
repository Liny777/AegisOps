"""用户自定义 LLM 全链闭合（探测真化 + 实例默认绑定）。

- MODEL-001：录 Secret → 建 llm-config（mock 探测通过）→ active，出现在列表。
- MODEL-002：model_name 含 "no-tool" → MODEL_PROBE_FAILED，不落库。
- 实例默认绑定：initial_overlay_json.user_llm_config_id → 起任务后 /state.selected_model = 该配置（overlay 被真消费，非死字段）。
- probe real 门控：OPENOPS_LLM_PROBE=real 走真 HTTP 分支，可达=200→ok；连接错→优雅 ok=False（不崩、reason 脱敏）。
"""
from __future__ import annotations

import time

from conftest import USER_HEADERS, create_run, unwrap, wait_until

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
