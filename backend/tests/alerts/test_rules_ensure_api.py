"""alerts 切片：POST /alerts/rules:ensure（深链进站收口，三态决策）。

覆盖：created（空实例/开放枚举/重名后缀/竞态外语义）、already_covered（子集覆盖/不限级别/
legacy 单值/prompt_ignored）、merged（级别并集/类别面最小优先/prompt 归一判等）、
_is_generic 门槛（keywords 规则不覆盖不合并）、disabled 不参与、服务端校验 400、
白名单/越权/实例不存在、幂等重放与 409。
"""
from __future__ import annotations

import asyncio
import time
import uuid

from conftest import OTHER_HEADERS, USER_HEADERS, create_instance, unwrap

BASE = "/api/openops/v1/alerts"


def _crid() -> str:
    return f"crid_{time.time_ns()}"


def _ensure(client, iid: str, *, name: str = "深链接管", categories: list[str] | None = None,
            severities: list[str] | None = None, prompt: str = "", crid: str | None = None,
            headers=None):
    return client.post(f"{BASE}/rules:ensure", headers=headers or USER_HEADERS,
                       json={"client_request_id": crid or _crid(),
                             "agent_team_instance_id": iid, "name": name,
                             "categories": categories or ["MySQL"],
                             "severities": severities or ["critical"], "prompt": prompt})


def _create_rule(client, iid: str, *, name: str, categories: list[str] | None = None,
                 severities: list[str] | None = None, prompt: str = "", enabled: bool = True):
    return client.post(f"{BASE}/rules", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "name": name, "categories": categories or ["MySQL"],
                             "severities": severities or [], "prompt": prompt,
                             "enabled": enabled})


def _list_rules(client, iid: str) -> list[dict]:
    return unwrap(client.get(f"{BASE}/rules", headers=USER_HEADERS,
                             params={"instance_id": iid}))["rules"]


def _set_grant(user_id: str, granted: bool) -> None:
    from alerts import repository as repo

    asyncio.run(repo.set_user_grant(user_id, granted, "test"))


def test_ensure_on_empty_instance_creates_rule(client):
    iid = create_instance(client)["instance_id"]
    got = unwrap(_ensure(client, iid, severities=["critical", "fatal"]))
    assert got["outcome"] == "created"
    assert got["renamed"] is False and got["requested_name"] == "深链接管"
    assert got["merge_detail"] is None and got["prompt_ignored"] is False
    rule = got["rule"]
    assert rule["categories"] == ["MySQL"]
    assert rule["severities"] == ["fatal", "critical"]  # SEVERITY_ORDER 有序落库
    assert rule["enabled"] is True and rule["source"] == "custom"


def test_already_covered_by_superset_severities(client):
    iid = create_instance(client)["instance_id"]
    unwrap(_create_rule(client, iid, name="宽面规则", severities=["fatal", "critical"]))
    got = unwrap(_ensure(client, iid, severities=["critical"]))
    assert got["outcome"] == "already_covered"
    assert got["rule"]["name"] == "宽面规则"
    assert got["prompt_ignored"] is False
    assert len(_list_rules(client, iid)) == 1  # 零写库


def test_already_covered_by_unlimited_severities(client):
    iid = create_instance(client)["instance_id"]
    unwrap(_create_rule(client, iid, name="不限级别", severities=[]))
    got = unwrap(_ensure(client, iid, severities=["warning"]))
    assert got["outcome"] == "already_covered" and got["rule"]["name"] == "不限级别"


def test_merge_expands_severities(client):
    iid = create_instance(client)["instance_id"]
    unwrap(_create_rule(client, iid, name="可并规则", severities=["fatal"]))
    got = unwrap(_ensure(client, iid, severities=["critical"]))
    assert got["outcome"] == "merged"
    assert got["merge_detail"] == {"severities_before": ["fatal"],
                                   "severities_after": ["fatal", "critical"],
                                   "added_severities": ["critical"]}
    rules = _list_rules(client, iid)
    assert len(rules) == 1 and rules[0]["severities"] == ["fatal", "critical"]


def test_merge_prefers_narrowest_category_face(client):
    iid = create_instance(client)["instance_id"]
    unwrap(_create_rule(client, iid, name="宽类别A", categories=["MySQL", "Docker"],
                        severities=["fatal"]))
    unwrap(_create_rule(client, iid, name="窄类别B", categories=["MySQL"],
                        severities=["fatal"]))
    got = unwrap(_ensure(client, iid, severities=["critical"]))
    assert got["outcome"] == "merged" and got["rule"]["name"] == "窄类别B"
    by_name = {r["name"]: r for r in _list_rules(client, iid)}
    assert by_name["窄类别B"]["severities"] == ["fatal", "critical"]
    assert by_name["宽类别A"]["severities"] == ["fatal"]  # 类别面最小优先，A 不动


def test_keyworded_rule_neither_covers_nor_merges(client):
    iid = create_instance(client)["instance_id"]
    rid = unwrap(_create_rule(client, iid, name="带关键词",
                              severities=["critical"]))["rule"]["rule_id"]
    unwrap(client.post(f"{BASE}/rules/{rid}:update", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "keywords": ["磁盘"]}))
    got = unwrap(_ensure(client, iid, name="带关键词", severities=["critical"]))
    assert got["outcome"] == "created"  # 关键词只匹配子集，无覆盖/合并资格
    assert got["rule"]["name"] == "带关键词-2" and got["renamed"] is True
    assert len(_list_rules(client, iid)) == 2


def test_legacy_single_category_rule_covers(client):
    from alerts import repository as repo

    iid = create_instance(client)["instance_id"]
    asyncio.run(repo.create_rule(
        instance_id=iid, owner="0026demo01", name="存量单值", description="",
        source="custom", match={"category": "MySQL", "severities": ["critical"]},
        prompt="", enabled=True, by="test"))
    got = unwrap(_ensure(client, iid, severities=["critical"]))
    assert got["outcome"] == "already_covered"  # matcher.rule_categories 归一 legacy 单值
    assert got["rule"]["name"] == "存量单值" and got["rule"]["categories"] == ["MySQL"]


def test_disabled_rule_is_ignored(client):
    iid = create_instance(client)["instance_id"]
    unwrap(_create_rule(client, iid, name="已停用", severities=["fatal", "critical", "warning"],
                        enabled=False))
    got = unwrap(_ensure(client, iid, name="新建放行", severities=["critical"]))
    assert got["outcome"] == "created"
    assert len(_list_rules(client, iid)) == 2


def test_prompt_normalization_gates_merge_and_flags_ignore(client):
    from alerts.rule_templates import DEFAULT_RULE_PROMPT

    # 既有自定义 prompt，请求默认 → 归一不等，不并 → created
    iid = create_instance(client)["instance_id"]
    unwrap(_create_rule(client, iid, name="自定义提示", severities=["fatal"],
                        prompt="自定义：先看主从延迟"))
    got = unwrap(_ensure(client, iid, name="默认提示", severities=["critical"]))
    assert got["outcome"] == "created"

    # 既有 prompt = 默认原文，请求空 → 归一相等 → merged
    iid2 = create_instance(client, name="归一实例")["instance_id"]
    unwrap(_create_rule(client, iid2, name="默认原文", severities=["fatal"],
                        prompt=DEFAULT_RULE_PROMPT))
    got2 = unwrap(_ensure(client, iid2, severities=["critical"]))
    assert got2["outcome"] == "merged" and got2["rule"]["name"] == "默认原文"

    # already_covered 且请求带不同自定义 prompt → prompt_ignored=True
    iid3 = create_instance(client, name="忽略实例")["instance_id"]
    unwrap(_create_rule(client, iid3, name="覆盖面", severities=["fatal", "critical"]))
    got3 = unwrap(_ensure(client, iid3, severities=["critical"], prompt="深链自带提示词"))
    assert got3["outcome"] == "already_covered" and got3["prompt_ignored"] is True


def test_name_conflict_gets_suffix(client):
    iid = create_instance(client)["instance_id"]
    unwrap(_create_rule(client, iid, name="同名不同面", categories=["Docker"],
                        severities=["fatal"]))
    got = unwrap(_ensure(client, iid, name="同名不同面", severities=["critical"]))
    assert got["outcome"] == "created"
    assert got["rule"]["name"] == "同名不同面-2"
    assert got["renamed"] is True and got["requested_name"] == "同名不同面"


def test_service_side_validation_and_open_enum(client):
    iid = create_instance(client)["instance_id"]
    bad_sev = _ensure(client, iid, severities=["info"])
    assert bad_sev.status_code == 400  # UI 三档，info 拒收（服务端 400 非 422）
    two_cats = _ensure(client, iid, categories=["MySQL", "Docker"])
    assert two_cats.status_code == 400 and "单个策略类型" in two_cats.text
    eks = unwrap(_ensure(client, iid, name="EKS 接管", categories=["EKS"]))
    assert eks["outcome"] == "created"  # 模板外开放枚举放行（category=moType 原样）


def test_grant_gate_ownership_and_missing_instance(client):
    iid = create_instance(client)["instance_id"]
    _set_grant("0026demo01", False)
    blocked = _ensure(client, iid)
    assert blocked.status_code == 403 and "告警接管功能未开通" in blocked.text
    _set_grant("0026demo01", True)

    other = _ensure(client, iid, headers=OTHER_HEADERS)
    assert other.status_code == 403  # 他人实例越权
    missing = _ensure(client, str(uuid.uuid4()))
    assert missing.status_code == 404


def test_idempotent_replay_and_conflict(client):
    iid = create_instance(client)["instance_id"]
    unwrap(_create_rule(client, iid, name="幂等合并", severities=["fatal"]))
    crid = _crid()
    first = unwrap(_ensure(client, iid, severities=["critical"], crid=crid))
    assert first["outcome"] == "merged"
    # 重放命中缓存：outcome 仍是 merged（真重算会变 already_covered），库面零变化
    replay = unwrap(_ensure(client, iid, severities=["critical"], crid=crid))
    assert replay == first
    rules = _list_rules(client, iid)
    assert len(rules) == 1 and rules[0]["severities"] == ["fatal", "critical"]
    # 同 crid 不同体 → 409
    conflict = _ensure(client, iid, severities=["warning"], crid=crid)
    assert conflict.status_code == 409


def test_owner_only_rule_not_treated_as_coverage(client):
    """owner_only 规则比宽规则窄（只接本人责任告警），不得作为 ensure 的覆盖判定基准
    （2026-08-21 _is_generic 排除）——同类型同级别下 ensure 仍新建。"""
    iid = create_instance(client)["instance_id"]
    unwrap(client.post(f"{BASE}/rules", headers=USER_HEADERS,
                       json={"client_request_id": _crid(), "agent_team_instance_id": iid,
                             "name": "仅本人宽面", "categories": ["MySQL"],
                             "severities": ["fatal", "critical"], "strategies": [],
                             "prompt": "", "owner_only": True}))
    got = unwrap(_ensure(client, iid, severities=["critical"]))
    assert got["outcome"] == "created"  # 不判 already_covered，也不合并进 owner_only 规则
    assert got["rule"]["name"] != "仅本人宽面"
