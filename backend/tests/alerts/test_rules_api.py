"""alerts 切片：规则（v2 一弹窗一规则）/批量操作/订阅/模板/配置 API。

覆盖：单规则创建（categories 多选必填/severities 三档校验/strategies/prompt 往返 + 幂等重放 +
同名 400）、部分更新、rules:batch 三动作与越权、模板接口（3 类 9 条 + default_prompt）、
订阅默认未开通、admin 配置整批校验。
"""
from __future__ import annotations

import time

from conftest import ADMIN_HEADERS, OTHER_HEADERS, USER_HEADERS, create_instance, unwrap

BASE = "/api/openops/v1/alerts"


def _crid() -> str:
    return f"crid_{time.time_ns()}"


def _create_rule(client, instance_id: str, *, name: str, category: str = "MySQL",  # noqa: ARG001 —— 单类型便捷参
                 severities: list[str] | None = None, strategies: list[str] | None = None,
                 prompt: str = "", crid: str | None = None, enabled: bool = True):
    return client.post(f"{BASE}/rules", headers=USER_HEADERS,
                       json={"client_request_id": crid or _crid(),
                             "agent_team_instance_id": instance_id, "name": name,
                             "categories": [category], "severities": severities or [],
                             "strategies": strategies or [], "prompt": prompt,
                             "enabled": enabled})


def test_rule_templates_v2_shape(client):
    data = unwrap(client.get(f"{BASE}/rule-templates", headers=USER_HEADERS))
    assert data["severities"] == ["fatal", "critical", "warning"]  # UI 三档（info 雪藏）
    cats = {t["category"] for t in data["templates"]}
    assert cats == {"MySQL", "PostgreSQL", "Docker"}  # 本期三类
    assert len(data["templates"]) == 9
    assert "五步法" in data["default_prompt"] and "写入对话" in data["default_prompt"]


def test_single_rule_roundtrip_with_prompt_and_strategies(client):
    inst = create_instance(client)
    iid = inst["instance_id"]
    rule = unwrap(_create_rule(client, iid, name="支付库MySQL接管", category="MySQL",
                               severities=["fatal", "critical"],
                               strategies=["MySQL 主从延迟监控", "MySQL 慢查询监控"],
                               prompt="自定义提示词：先看主从延迟。"))["rule"]
    assert rule["categories"] == ["MySQL"]
    assert rule["strategies"] == ["MySQL 主从延迟监控", "MySQL 慢查询监控"]
    assert rule["prompt"] == "自定义提示词：先看主从延迟。"

    listed = unwrap(client.get(f"{BASE}/rules", headers=USER_HEADERS,
                               params={"instance_id": iid}))["rules"]
    assert listed[0]["prompt"].startswith("自定义提示词")

    # 同名 → 400；幂等重放同 crid → 命中缓存不重复建
    dup = _create_rule(client, iid, name="支付库MySQL接管")
    assert dup.status_code == 400
    crid = _crid()
    first = unwrap(_create_rule(client, iid, name="幂等规则", crid=crid))
    replay = unwrap(_create_rule(client, iid, name="幂等规则", crid=crid))
    assert replay == first
    assert len(unwrap(client.get(f"{BASE}/rules", headers=USER_HEADERS,
                                 params={"instance_id": iid}))["rules"]) == 2


def test_create_rejects_info_severity_and_missing_category(client):
    inst = create_instance(client)
    bad_sev = _create_rule(client, inst["instance_id"], name="x", severities=["info"])
    assert bad_sev.status_code == 400  # 本期 UI 三档，info 拒收
    bad_cat = client.post(f"{BASE}/rules", headers=USER_HEADERS,
                          json={"client_request_id": _crid(),
                                "agent_team_instance_id": inst["instance_id"],
                                "name": "y", "categories": [""]})
    assert bad_cat.status_code in (400, 422)  # 类型必填（全空串在 service 层 400）


def test_update_rule_partial_fields(client):
    inst = create_instance(client)
    iid = inst["instance_id"]
    rule = unwrap(_create_rule(client, iid, name="待编辑", strategies=["MySQL 慢查询监控"]))["rule"]
    updated = unwrap(client.post(f"{BASE}/rules/{rule['rule_id']}:update", headers=USER_HEADERS,
                                 json={"client_request_id": _crid(),
                                       "severities": ["warning"],
                                       "strategies": ["MySQL 资源饱和标准监控"],
                                       "prompt": "改过的提示词", "enabled": False}))
    assert updated["severities"] == ["warning"]
    assert updated["strategies"] == ["MySQL 资源饱和标准监控"]
    assert updated["prompt"] == "改过的提示词" and updated["enabled"] is False
    assert updated["categories"] == ["MySQL"]  # 未传字段不动


def test_batch_rules_enable_disable_delete_and_forbidden(client):
    inst = create_instance(client)
    iid = inst["instance_id"]
    ids = [unwrap(_create_rule(client, iid, name=f"批量-{i}"))["rule"]["rule_id"]
           for i in range(3)]

    off = unwrap(client.post(f"{BASE}/rules:batch", headers=USER_HEADERS,
                             json={"client_request_id": _crid(), "rule_ids": ids,
                                   "action": "disable"}))
    assert off["updated"] == 3
    rules = unwrap(client.get(f"{BASE}/rules", headers=USER_HEADERS,
                              params={"instance_id": iid}))["rules"]
    assert all(r["enabled"] is False for r in rules)

    on = unwrap(client.post(f"{BASE}/rules:batch", headers=USER_HEADERS,
                            json={"client_request_id": _crid(), "rule_ids": ids[:2],
                                  "action": "enable"}))
    assert on["updated"] == 2

    # 越权：他人操作整批拒绝
    other = client.post(f"{BASE}/rules:batch", headers=OTHER_HEADERS,
                        json={"client_request_id": _crid(), "rule_ids": ids,
                              "action": "delete"})
    assert other.status_code == 403

    gone = unwrap(client.post(f"{BASE}/rules:batch", headers=USER_HEADERS,
                              json={"client_request_id": _crid(), "rule_ids": ids,
                                    "action": "delete"}))
    assert gone["updated"] == 3
    assert unwrap(client.get(f"{BASE}/rules", headers=USER_HEADERS,
                             params={"instance_id": iid}))["rules"] == []

    # 已删除的 id 再批量 → 404
    missing = client.post(f"{BASE}/rules:batch", headers=USER_HEADERS,
                          json={"client_request_id": _crid(), "rule_ids": ids,
                                "action": "enable"})
    assert missing.status_code == 404


def test_subscription_default_disabled_then_enable(client):
    inst = create_instance(client)
    iid = inst["instance_id"]
    sub = unwrap(client.get(f"{BASE}/subscription", headers=USER_HEADERS,
                            params={"instance_id": iid}))
    assert sub["enabled"] is False  # 无行 = 未开通接管
    on = unwrap(client.post(f"{BASE}/subscription:update", headers=USER_HEADERS,
                            json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                                  "enabled": True, "max_open_incidents": 5}))
    assert on["enabled"] is True and on["max_open_incidents"] == 5


def test_admin_config_validation_and_update(client):
    got = unwrap(client.get("/api/openops/v1/admin/alerts/config", headers=ADMIN_HEADERS))["config"]
    assert got["alert_max_concurrent_diagnosis"] == 2
    bad = client.post("/api/openops/v1/admin/alerts/config:update", headers=ADMIN_HEADERS,
                      json={"client_request_id": _crid(),
                            "updates": {"alert_max_concurrent_diagnosis": 0}, "reason": "t"})
    assert bad.status_code == 400
    ok_resp = unwrap(client.post("/api/openops/v1/admin/alerts/config:update",
                                 headers=ADMIN_HEADERS,
                                 json={"client_request_id": _crid(),
                                       "updates": {"alert_enabled": False},
                                       "reason": "演练"}))["config"]
    assert ok_resp["alert_enabled"] is False
    assert client.get("/api/openops/v1/admin/alerts/config",
                      headers=USER_HEADERS).status_code == 403
