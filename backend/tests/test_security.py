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
        # real 但无 BASE_URL → fail loud（缺 BASE_URL 的失败面在 proxy 路由；direct 路由不需要
        # BASE_URL——server_url 即端点，垃圾 URL 由 httpx 自身 fail loud）
        monkeypatch.setenv("OPENOPS_MCP_ROUTE", "proxy")
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


def test_sec_s3_dns_pin_blocks_disjoint_drift(client, monkeypatch):
    """S3（C2-OBS-002）：同 host 解析集与钉扎完全不相交 → 拦（疑似 rebinding）；有交集/关开关放行。"""
    import socket as _socket

    import pytest as _pytest

    from domain.errors import ApiError
    from infra import egress

    egress.reset_pins()
    resolved = {"ips": [("1.2.3.4",)]}

    def _fake_gai(host, port, proto=0):  # noqa: ANN001
        return [(2, 1, 6, "", (ip[0], 443)) for ip in resolved["ips"]]

    monkeypatch.setattr(egress.socket, "getaddrinfo", _fake_gai)
    egress.check_llm_egress("https://llm.corp.example/v1")  # 首见 → 钉扎 1.2.3.4
    resolved["ips"] = [("1.2.3.4",), ("5.6.7.8",)]
    egress.check_llm_egress("https://llm.corp.example/v1")  # 有交集 → 放行
    resolved["ips"] = [("9.9.9.9",)]
    with _pytest.raises(ApiError) as ei:
        egress.check_llm_egress("https://llm.corp.example/v1")  # 完全漂移 → 拦
    assert "解析漂移" in ei.value.message
    monkeypatch.setenv("OPENOPS_LLM_EGRESS_PIN", "0")
    egress.check_llm_egress("https://llm.corp.example/v1")  # 开关关 → 放行
    egress.reset_pins()


def test_sec_s3_user_llm_no_silent_fallback(client, monkeypatch):
    """S3（C2-OBS-003）：选中的自定义 LLM 失效不再静默回退平台默认——显式 MODEL_NOT_AUTHORIZED。"""
    import asyncio as _asyncio
    import uuid as _uuid

    import pytest as _pytest

    from app import model_gateway
    from domain.errors import ApiError
    from infra.repositories import secrets as secrets_repo

    async def _gone(_id):  # 配置已删除/禁用
        return None

    monkeypatch.setattr(secrets_repo, "get_llm_config", _gone)
    with _pytest.raises(ApiError) as ei:
        _asyncio.run(model_gateway.resolve_runtime_model(str(_uuid.uuid4()), "0026demo01"))
    assert ei.value.code == "MODEL_NOT_AUTHORIZED"


def test_def_d_redact_args_and_gateway_event(client):
    """连带 D（16/30.4）：工具入参 key 级脱敏——敏感 key 打码、字符串值抹 sk-/Bearer 样式。"""
    from infra.redact import redact_args
    from runtime.tool_gateway import _args_for_event

    args = {"password": "p@ssw0rd", "api_key": "sk-abcdef123456", "appid": "APP-A",
            "note": "auth Bearer abcdef.123456 tail", "nested": {"Cookie": "sid=1", "ok": "v"},
            "arr": [{"token": "t0k3n"}, "sk-zzzzzzzzzz"]}
    out = redact_args(args)
    assert out["password"] == "***" and out["api_key"] == "***"
    assert out["nested"]["Cookie"] == "***" and out["nested"]["ok"] == "v"
    assert out["arr"][0]["token"] == "***"
    assert "sk-zzzzzzzzzz" not in str(out) and "Bearer abcdef" not in str(out)
    assert out["appid"] == "APP-A"  # 业务字段不受影响
    ev = _args_for_event(args)  # gateway 事件面（进审计/工具卡）同样干净
    flat = str(ev)
    assert "p@ssw0rd" not in flat and "sk-abcdef123456" not in flat
