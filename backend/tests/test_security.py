from __future__ import annotations

import json
import time

from conftest import USER_HEADERS, create_instance, create_run, unwrap, wait_until

SENSITIVE = ("sk-test-secret", "Authorization", "Bearer", "Cookie", "API Key")


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
