from __future__ import annotations

import asyncio
import json
import time

import pytest
from conftest import USER_HEADERS, create_instance, create_run, unwrap, wait_until

SENSITIVE = ("sk-test-secret", "Authorization", "Bearer", "Cookie", "API Key")


def test_sec_008_secret_fernet_real_encryption(client):
    """C2/SEC-001：Secret 用 Fernet 真加密（非可逆混淆）——密文是有效 Fernet token 且可解密回原文。"""
    from infra import crypto

    plain = "sk-real-fernet-秘钥-123"
    cipher = crypto.encrypt(plain)
    assert cipher.startswith("gAAAAA")  # Fernet token 前缀（版本 0x80 + 时间戳的 base64）
    assert plain not in cipher and "sk-real" not in cipher
    assert crypto.decrypt(cipher) == plain
    # 篡改密文 → 解密抛错（HMAC 校验，非 XOR 静默产垃圾）
    with pytest.raises(ValueError):
        crypto.decrypt(cipher[:-4] + "AAAA")


@pytest.mark.parametrize("base_url,ok", [
    ("http://127.0.0.1:8080/v1", False),        # 环回
    ("http://localhost/v1", False),             # localhost 主机名
    ("http://169.254.169.254/latest", False),   # 云 metadata（链路本地）
    ("http://172.17.0.1/v1", False),            # Docker bridge（默认 deny）
    ("https://1.1.1.1/v1", True),               # 公网 IP 字面量（放行，无 DNS 依赖）
])
def test_sec_009_llm_egress_ssrf_blocks(client, base_url, ok):
    """C2/28.4：LLM egress SSRF——拦 localhost/metadata/docker bridge，放行公网。"""
    from domain.errors import ApiError
    from infra import egress

    if ok:
        egress.check_llm_egress(base_url)  # 不抛
    else:
        with pytest.raises(ApiError):
            egress.check_llm_egress(base_url)


def test_sec_010_user_llm_config_requires_secret_and_egress(client):
    """C2：创建用户 LLM——无 secret → SECRET_REQUIRED（修 NOT NULL 不一致）；SSRF base_url → 拒绝。"""
    # 无 secret_ref_id
    r1 = client.post("/api/openops/v1/llm-configs", headers=USER_HEADERS,
                     json={"client_request_id": "l1", "display_name": "x", "provider": "openai_compatible",
                           "base_url": "https://api.openai.com/v1", "model_name": "gpt-4o"})
    assert r1.status_code == 400 and r1.json()["error"]["code"] == "SECRET_REQUIRED"
    # 造一个 secret，再用 SSRF base_url
    sec = unwrap(client.post("/api/openops/v1/secrets", headers=USER_HEADERS,
                             json={"client_request_id": "s1", "secret_name": "k", "secret_type": "api_key",
                                   "provider": "openai_compatible", "secret_value": "sk-x"}))
    r2 = client.post("/api/openops/v1/llm-configs", headers=USER_HEADERS,
                     json={"client_request_id": "l2", "display_name": "x", "provider": "openai_compatible",
                           "base_url": "http://169.254.169.254/v1", "model_name": "gpt-4o",
                           "secret_ref_id": sec["secret_ref_id"]})
    assert r2.status_code == 400 and r2.json()["error"]["code"] == "MODEL_PROBE_FAILED"


def test_ext_007_external_real_switches_fail_loud_without_endpoint(client, monkeypatch):
    """C3：外部依赖 real 开关未配端点 → raise（不静默降级）；mock 默认路径不受影响。"""
    from infra.external import http_mcp_client, mcp_registry_client, skill_hub_client

    async def scenario():
        # mock 默认：正常返回
        assert (await http_mcp_client.call_tool("query_resource", {"appid": "A"}))["status"] == "ok"
        assert (await skill_hub_client.download_skill_package("inspection", 2))["entrypoint"]
        # real 但无 BASE_URL → fail loud
        for env, fn in [
            ("OPENOPS_MCP", lambda: http_mcp_client.call_tool("query_resource", {"appid": "A"})),
            ("OPENOPS_MCPREGISTRY", lambda: mcp_registry_client.discover_tools("m")),
            ("OPENOPS_SKILLHUB", lambda: skill_hub_client.list_skills("u")),
        ]:
            monkeypatch.setenv(env, "real")
            try:
                await fn()
                assert False, f"{env}=real 无端点应 raise"
            except RuntimeError as e:
                assert "未联" in str(e) or "BASE_URL" in str(e)
            monkeypatch.delenv(env)

    asyncio.run(scenario())


def test_sec_011_user_llm_select_no_silent_fallback(client):
    """C2：选中不存在/非本人的用户 LLM → SECRET_REQUIRED（不再静默回退平台模型）。"""
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    r = client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}:select-model", headers=USER_HEADERS,
                    json={"client_request_id": "sm1", "llm_config_id": "00000000-0000-0000-0000-000000000000"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "SECRET_REQUIRED"


def test_sec_001_secret_plaintext_never_returns_from_api(client):
    created = unwrap(
        client.post(
            "/api/openops/v1/secrets",
            headers=USER_HEADERS,
            json={
                "client_request_id": "secret_create",
                "secret_name": "我的 OpenAI Key",
                "secret_type": "api_key",
                "provider": "openai_compatible",
                "secret_value": "sk-test-secret",
            },
        )
    )
    assert "secret_value" not in created
    assert "sk-test-secret" not in json.dumps(created)

    listed = unwrap(client.get("/api/openops/v1/secrets", headers=USER_HEADERS))
    body = json.dumps(listed, ensure_ascii=False)
    assert "fingerprint" in body
    for token in SENSITIVE:
        assert token not in body


def test_sec_002_events_and_audit_are_redacted(client):
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    unwrap(
        client.post(
            f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks",
            headers=USER_HEADERS,
            json={"client_request_id": f"task_{time.time_ns()}", "input_text": "排查 APP-A 支付延迟"},
        )
    )
    wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/approvals", headers=USER_HEADERS)),
    )
    state = unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state", headers=USER_HEADERS))
    audit = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    body = json.dumps({"state": state, "audit": audit}, ensure_ascii=False)
    for token in SENSITIVE:
        assert token not in body
