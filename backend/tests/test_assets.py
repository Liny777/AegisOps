from __future__ import annotations

import os
import time

import psycopg
import pytest
from app import asset_registry_service
from conftest import ADMIN_HEADERS, OTHER_HEADERS, USER_HEADERS, create_instance, create_run, unwrap, wait_until
from infra import egress
from infra.external import mcp_registry_client, skill_hub_client


def _upload_skill(client, name: str) -> dict:
    return unwrap(client.post(
        "/api/openops/v1/assets/skills", headers=USER_HEADERS,
        json={"client_request_id": f"up_{time.time_ns()}", "display_name": name,
              "manifest_json": {"entrypoint": "run.py"}, "checksum_sha256": ""},
    ))


def _bind_skill(client, instance_id: str, skill: dict) -> dict:
    return unwrap(client.post(
        f"/api/openops/v1/agent-teams/{instance_id}/asset-bindings", headers=USER_HEADERS,
        json={"client_request_id": f"bind_{time.time_ns()}", "asset_type": "skill",
              "skill_id": skill["skill_id"], "skill_version_id": skill["skill_version_id"],
              "mcp_id": None, "mcp_version_id": None},
    ))


def _bindings(client, instance_id: str) -> list[dict]:
    return unwrap(client.get(f"/api/openops/v1/agent-teams/{instance_id}/asset-bindings", headers=USER_HEADERS))


def _sql(query: str, params: dict) -> list[tuple]:
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        return conn.execute(query, params).fetchall()


def test_asset_bind_carries_forward_and_history_immutable(client):
    """二次 bind 不丢首个绑定；旧版本的绑定行不被原地改写（28.7 不可变链）。"""
    instance = create_instance(client)
    s1 = _upload_skill(client, "结转验证 Skill 一")
    s2 = _upload_skill(client, "结转验证 Skill 二")
    r1 = _bind_skill(client, instance["instance_id"], s1)
    cv1 = r1["config_version_id"]
    _bind_skill(client, instance["instance_id"], s2)

    names = {b["display_name"] for b in _bindings(client, instance["instance_id"])}
    assert names == {"结转验证 Skill 一", "结转验证 Skill 二"}  # 结转 + 新增

    # 历史版本（cv1）的绑定行仍原样保留（未软删、未挪动）
    rows = _sql(
        "select status, deleted_at from sre_instance_asset_binding where config_version_id=%(cv)s",
        {"cv": cv1},
    )
    assert rows and all(r[0] == "active" and r[1] is None for r in rows)


def test_asset_save_config_keeps_bindings(client):
    """保存 main 追加（新配置版本）不得丢绑定（此前缺陷：派生版本未结转绑定）。"""
    instance = create_instance(client)
    s = _upload_skill(client, "保存后仍在 Skill")
    _bind_skill(client, instance["instance_id"], s)
    unwrap(client.post(
        f"/api/openops/v1/agent-teams/{instance['instance_id']}/config-versions", headers=USER_HEADERS,
        json={"client_request_id": f"cfg_{time.time_ns()}", "overlay_json": {"main_role_append": "优先看支付链路"},
              "change_reason": "append", "base_config_version_id": None},
    ))
    assert {b["display_name"] for b in _bindings(client, instance["instance_id"])} == {"保存后仍在 Skill"}


def test_asset_unbind_derives_new_version(client):
    """解绑=派生新版本；解绑后可删除资产（先 ASSET_IN_USE 后放行）。"""
    instance = create_instance(client)
    s = _upload_skill(client, "待解绑 Skill")
    _bind_skill(client, instance["instance_id"], s)

    blocked = client.delete(f"/api/openops/v1/assets/skills/{s['skill_id']}", headers=USER_HEADERS)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "ASSET_IN_USE"  # CFG-006

    binding_id = _bindings(client, instance["instance_id"])[0]["binding_id"]
    unwrap(client.delete(f"/api/openops/v1/asset-bindings/{binding_id}", headers=USER_HEADERS))
    assert _bindings(client, instance["instance_id"]) == []
    # 原绑定行仍在（历史版本，不原地改写）
    rows = _sql("select deleted_at from sre_instance_asset_binding where binding_id=%(b)s", {"b": binding_id})
    assert rows and rows[0][0] is None

    unwrap(client.delete(f"/api/openops/v1/assets/skills/{s['skill_id']}", headers=USER_HEADERS))


def test_asset_reconcile_source_openops_and_versions(client):
    """对账：只拉 source=openops；checksum 变化补版本，重复对账幂等（ASSET-001/002）。"""
    first = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))
    # seed 平台 skill 'inspection' checksum 与 Skill Hub 不同 → 追加一个版本
    assert first["skill_versions_added"] == 1
    assert first["tools_unchanged"] == 2  # query_resource / recover_execute schema 未变
    second = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))
    assert second["skill_versions_added"] == 0  # 幂等
    # §2.2 semver 经 reconcile 落 manifest_json → list_skills 透出（展示口径，非本地整数 version_no）
    skills = unwrap(client.get("/api/openops/v1/assets/skills", headers=USER_HEADERS))["items"]
    insp = next(s for s in skills if s["skill_key"] == "inspection")
    assert insp["latest_version"] == "2.0.0"  # _MOCK_LIST 的 latest_version 原串
    assert "manifest_json" not in insp  # 内部 manifest 不透给前端


def test_asset_reconcile_backfills_missing_semver_without_new_version(client):
    """存量回填（DEF）：改动前的旧行 manifest 缺 semver 且 checksum 未变→reconcile 原地回填、不新增版本。
    复现内网现象：latest_version=null / 前端显示 v{n}。"""
    import asyncio

    from infra.repositories import assets

    async def _seed_old_manifest() -> str:
        row = await assets.get_skill_by_key("platform", "inspection")
        latest = await assets.latest_skill_version(str(row["skill_id"]))
        # 模拟旧 reconcile 写的单键 manifest（无 latest_version）——正是内网 B 检查看到的形状
        await assets.update_skill_version_manifest(str(latest["skill_version_id"]), {"synced_from": "skill_hub"})
        return str(latest["version_no"])

    unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))  # 先建 v2（checksum 变）
    old_vno = asyncio.run(_seed_old_manifest())
    before = unwrap(client.get("/api/openops/v1/assets/skills", headers=USER_HEADERS))["items"]
    assert next(s for s in before if s["skill_key"] == "inspection")["latest_version"] is None  # 回填前=null（复现）

    r = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))  # checksum 未变
    assert r["skill_versions_added"] == 0 and r["skill_manifests_refreshed"] >= 1  # 不新增版本、原地回填
    after = unwrap(client.get("/api/openops/v1/assets/skills", headers=USER_HEADERS))["items"]
    insp = next(s for s in after if s["skill_key"] == "inspection")
    assert insp["latest_version"] == "2.0.0"  # semver 已回填
    assert str(insp["version_no"]) == old_vno  # 版本号不变（非新版本）


def test_asset_reconcile_captures_skill_description(client):
    """发现链路：reconcile 建版本时下载包抽 SKILL.md 的 description 落 manifest_json（list API 不带该字段，
    唯有解 SKILL.md 才有）→ 供 run_platform_skill 工具描述注入，Agent 才知道 skill 用途、会主动调。"""
    import asyncio

    from infra.repositories import assets

    unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))  # 建 v2（真 checksum）

    async def _desc() -> str | None:
        row = await assets.get_skill_by_key("platform", "inspection")
        latest = await assets.latest_skill_version(str(row["skill_id"]))
        return (latest.get("manifest_json") or {}).get("description")

    assert asyncio.run(_desc()) == "巡检 Skill"  # _MOCK_SKILL_MD frontmatter 的 description，端到端落库


def test_asset_reconcile_heals_block_scalar_description(client):
    """存量自愈：修 parser 前入库的坏描述（被旧解析器截成裸块标量指示符 `>`）在下轮对账被重解析回填，
    无需重新上传——复现并修复插件页「说明」只显示一个 `>` 的存量行。"""
    import asyncio

    from infra.repositories import assets

    unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))  # 建 v2

    async def _latest():
        row = await assets.get_skill_by_key("platform", "inspection")
        return await assets.latest_skill_version(str(row["skill_id"]))

    latest = asyncio.run(_latest())
    cur = latest.get("manifest_json") or {}
    # 人为写入坏描述：latest_version/category 保持不变，确保仅 need_desc（坏描述）触发原地回填
    asyncio.run(assets.update_skill_version_manifest(str(latest["skill_version_id"]), {**cur, "description": ">"}))

    r = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))  # checksum 未变
    assert r["skill_versions_added"] == 0 and r["skill_manifests_refreshed"] >= 1  # 不新增版本、原地回填
    assert (asyncio.run(_latest()).get("manifest_json") or {}).get("description") == "巡检 Skill"  # `>` 被真描述覆盖


def test_assets_list_paginated_with_serverside_filters(client):
    """分页读（29.3 §2.2 口径）：{items,total,page,page_size}；source_type/q 过滤走**服务端**——
    分页后再客户端过滤每页数量必然错乱（管理台 platform 基线、插件页两组分页都吃这条）。"""
    unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))
    _upload_skill(client, "分页用户技能")

    p = unwrap(client.get("/api/openops/v1/assets/skills?page=1&page_size=1", headers=USER_HEADERS))
    assert {"items", "total", "page", "page_size"} <= set(p)
    assert p["page_size"] == 1 and len(p["items"]) <= 1 and p["total"] >= 2

    plat = unwrap(client.get("/api/openops/v1/assets/skills?source_type=platform", headers=USER_HEADERS))
    mine = unwrap(client.get("/api/openops/v1/assets/skills?source_type=user", headers=USER_HEADERS))
    assert plat["items"] and all(i["source_type"] == "platform" for i in plat["items"])
    assert all(i["source_type"] == "user" for i in mine["items"])
    assert plat["total"] + mine["total"] == p["total"]  # 两组之和=全量（插件页两组同源分页）

    hit = unwrap(client.get("/api/openops/v1/assets/skills?q=分页用户", headers=USER_HEADERS))
    assert hit["total"] == 1 and hit["items"][0]["display_name"] == "分页用户技能"

    # page_size 上限 100（§2.2）——此前上游客户端写死 200 已超限
    assert client.get("/api/openops/v1/assets/skills?page_size=101", headers=USER_HEADERS).status_code == 422
    # MCP 同款信封
    assert {"items", "total"} <= set(unwrap(client.get("/api/openops/v1/assets/mcps", headers=USER_HEADERS)))


def test_assets_list_carries_real_description(client):
    """插件页「说明」的数据源：列表带该 skill **自己**的 description（此前被 service 的 manifest.pop 丢弃）。"""
    unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))
    items = unwrap(client.get("/api/openops/v1/assets/skills?source_type=platform", headers=USER_HEADERS))["items"]
    assert next(i for i in items if i["skill_key"] == "inspection")["description"] == "巡检 Skill"


def test_skill_detail_gives_description_and_skill_md(client):
    """真说明：详情端点回 description + SKILL.md 全文（29.3 §2.4）；不存在的 skill → 404。"""
    unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))
    d = unwrap(client.get("/api/openops/v1/assets/skills/inspection/detail", headers=USER_HEADERS))
    assert d["description"] and "name: inspection" in (d["content"] or "")
    assert d["detail_source"] == "skillhub"
    assert client.get("/api/openops/v1/assets/skills/nope/detail", headers=USER_HEADERS).status_code == 404


def test_skill_detail_degrades_to_local_when_upstream_down(client, monkeypatch):
    """上游挂不阻断 UI：详情降级本地 manifest 已存的 description。"""
    unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))

    async def _boom(*_a, **_k):
        raise RuntimeError("skillhub down")

    monkeypatch.setattr(skill_hub_client, "get_skill_detail", _boom)
    d = unwrap(client.get("/api/openops/v1/assets/skills/inspection/detail", headers=USER_HEADERS))
    assert d["detail_source"] == "local" and d["description"] == "巡检 Skill"


def test_asset_schema_change_annotation_not_inherited(client, monkeypatch, runtime_backend):
    """schema_hash 变化 → 新 catalog 行不继承标注 → 运行时 TOOL_NOT_ANNOTATED fail-closed（ASSET-005，双 runtime）。"""
    tools = [
        {"tool_name": "query_resource",
         "description": "按 APPID 查询资源与指标",
         "input_schema": {"type": "object", "properties": {"appid": {"type": "string"}}}},
        {"tool_name": "recover_execute",
         "description": "执行受控恢复动作（需审批）",
         "input_schema": {"type": "object", "properties": {"appid": {"type": "string"},
                                                           "action": {"type": "string"},
                                                           "grace_seconds": {"type": "number"}}}},  # schema 变了
    ]

    async def fake_discover(_name: str):
        return [{**t, "schema_hash": mcp_registry_client._schema_hash(t["input_schema"])} for t in tools]

    monkeypatch.setattr(mcp_registry_client, "discover_tools", fake_discover)
    summary = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))
    assert summary["tools_schema_changed"] == 1

    # 新行未标注：启动任务 → recover_execute fail-closed（runtime 无关不变量，B6-RT-001）
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    unwrap(client.post(
        f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks", headers=USER_HEADERS,
        json={"client_request_id": f"t{time.time_ns()}", "input_text": "定位"},
    ))
    _assert_recover_blocked(client, run["agent_run_id"], "TOOL_NOT_ANNOTATED")


def test_annotation_reannotate_after_soft_delete_revives_not_500(client, monkeypatch):
    """DEF：schema 变更软删标注后重新标注——复活该行而非再插（唯一索引落 tool_catalog_id 含软删行，
    纯 INSERT 必撞 ux_mcp_tool_annotation_catalog → 500 UniqueViolation）。复现内网管理台编辑标注报错。"""
    base = {"tool_name": "recover_execute", "description": "执行受控恢复动作",
            "input_schema": {"type": "object", "properties": {"appid": {"type": "string"}}}}

    async def discover_v1(_name):
        return [{**base, "schema_hash": mcp_registry_client._schema_hash(base["input_schema"])}]

    monkeypatch.setattr(mcp_registry_client, "discover_tools", discover_v1)
    unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))
    rec = next(t for t in unwrap(client.get("/api/openops/v1/admin/mcp-tools", headers=ADMIN_HEADERS))
               if t["tool_name"] == "recover_execute")
    ann = {"is_approval_required": True, "is_secret_required": False, "scope_mode": "required",
           "appid_arg_path": "$.appid", "status": "allowed", "blocked_reason": None}
    put = f"/api/openops/v1/admin/mcp-tools/{rec['tool_catalog_id']}/annotation"
    unwrap(client.put(put, headers=ADMIN_HEADERS, json=ann))  # 首次标注 → insert

    # schema 变化 → sync_catalog_tool 软删该标注（tool_catalog_id 不变）
    changed = {**base, "input_schema": {"type": "object", "properties": {
        "appid": {"type": "string"}, "grace": {"type": "number"}}}}

    async def discover_v2(_name):
        return [{**changed, "schema_hash": mcp_registry_client._schema_hash(changed["input_schema"])}]

    monkeypatch.setattr(mcp_registry_client, "discover_tools", discover_v2)
    assert unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))["tools_schema_changed"] == 1

    # 重新标注：此前 500 的正是这一步——现应复活成功、且运行时标注生效（allowed）
    r = client.put(put, headers=ADMIN_HEADERS, json={**ann, "status": "allowed"})
    assert r.status_code == 200, r.text
    live = unwrap(client.get("/api/openops/v1/admin/mcp-tools", headers=ADMIN_HEADERS))
    rec2 = next(t for t in live if t["tool_name"] == "recover_execute")
    # 列表 LEFT JOIN 标注 on deleted_at is null → annotation_id 非空即证复活成功、运行时生效
    assert rec2.get("annotation_id") and rec2.get("annotation_status") == "allowed"


def _assert_recover_blocked(client, run_id: str, reason_code: str, expect_plan_update: bool = False) -> None:
    """runtime 无关的 fail-closed 不变量（B6-RT-001）：恢复未执行 + 阻断审计在案 + 完成态不得宣称恢复成功。

    mock 编排器硬失败（task failed）；agentscope 软处理（completed 但结论必须写明「拦截/未执行」）。
    """
    terminal = wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{run_id}/state",
                                  headers=USER_HEADERS))["active_task"]["status"] in ("failed", "completed"),
        timeout=8.0,
    )
    assert terminal is True
    status = unwrap(client.get(f"/api/openops/v1/agent-runs/{run_id}/state", headers=USER_HEADERS))["active_task"]["status"]
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run_id}", headers=USER_HEADERS))
    assert any(e["event_type"] == "openops.tool.blocked" and e["reason_code"] == reason_code for e in events)
    if expect_plan_update:
        assert any(e["event_type"] == "openops.runtime_plan.updated" for e in events)  # 边界检测到标注漂移
    # 恢复动作从未真正执行（无外部 request）
    assert not any(e.get("action") == "recover_execute" and e.get("external_request_id") for e in events)
    if status == "completed":  # agentscope 软处理路径：结论必须写明未执行，不得宣称已恢复
        done = next(e for e in events if e["event_type"] == "openops.task.completed")
        conclusion = str((done.get("payload_redacted_json") or {}).get("conclusion") or "")
        assert ("拦截" in conclusion or "未执行" in conclusion)
        assert "事件闭环" not in conclusion


def test_asset_hot_update_mid_run(client, runtime_backend):
    """运行中热更新（28.7）：pending 审批期间管理员 block 标注 → 批准后按最新标注 fail-closed（双 runtime）。"""
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    unwrap(client.post(
        f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks", headers=USER_HEADERS,
        json={"client_request_id": f"t{time.time_ns()}", "input_text": "定位"},
    ))
    approvals = wait_until(
        lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/approvals", headers=USER_HEADERS)),
        timeout=8.0,
    )
    # 审批挂起期间：管理员把 recover_execute 拉黑（运行中的配置变化）
    catalog = unwrap(client.get("/api/openops/v1/admin/mcp-tools", headers=ADMIN_HEADERS))
    rec = next(t for t in catalog if t["tool_name"] == "recover_execute")
    unwrap(client.put(
        f"/api/openops/v1/admin/mcp-tools/{rec['tool_catalog_id']}/annotation", headers=ADMIN_HEADERS,
        json={"is_approval_required": True, "is_secret_required": False, "scope_mode": "required",
              "appid_arg_path": "$.appid", "status": "blocked", "blocked_reason": "变更冻结"},
    ))
    unwrap(client.post(
        f"/api/openops/v1/approvals/{approvals[0]['approval_request_id']}:decide", headers=USER_HEADERS,
        json={"client_request_id": "ok", "decision": "approved"},
    ))
    _assert_recover_blocked(client, run["agent_run_id"], "TOOL_BLOCKED", expect_plan_update=True)


def test_asset_reconcile_failure_audited_not_fatal(client, monkeypatch):
    """对账失败收口（B6-TEST-001/ASSET-006）：写 asset.reconcile_failed 审计，服务不停摆。"""
    async def boom(_uid: str):
        raise RuntimeError("skill hub down")

    monkeypatch.setattr(skill_hub_client, "list_skills", boom)
    r = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))
    assert r.get("failed") is True
    recent = unwrap(client.get("/api/openops/v1/admin/audit/recent", headers=ADMIN_HEADERS))
    assert any(e["event_type"] == "asset.reconcile_failed" and e["reason_code"] == "RECONCILE_FAILED" for e in recent)
    assert client.get("/api/openops/v1/assets/skills", headers=USER_HEADERS).status_code == 200  # 不停服


def test_asset_mcp_endpoint_redacted(client, monkeypatch):
    """用户 MCP endpoint 展示脱敏（B6-TEST-001/30.5）：只回截断值，完整 endpoint 与 token 不出现。"""
    # 本例测脱敏、不测 egress；register 现在会解析 DNS（check_mcp_egress），打桩免得依赖解析器
    monkeypatch.setattr(egress, "check_mcp_egress", lambda _url: None)
    unwrap(client.post(
        "/api/openops/v1/assets/mcps", headers=USER_HEADERS,
        json={"client_request_id": f"m_{time.time_ns()}", "display_name": "内部 CMDB MCP",
              "transport": "http", "endpoint": "https://internal.example.com/mcp?token=supersecret123",
              "manifest_json": {}},
    ))
    rows = unwrap(client.get("/api/openops/v1/assets/mcps", headers=USER_HEADERS))["items"]
    mine = next(r for r in rows if r["display_name"] == "内部 CMDB MCP")
    assert "endpoint_config_json" not in mine
    assert str(mine["endpoint_config_redacted"]["endpoint"]).endswith("…")
    assert "supersecret123" not in str(mine)


def test_asset_reconcile_ingests_registry_mcp(client, monkeypatch):
    """注册表 server → 平台 MCP 资产入库（内网缺口：此前只刷已有资产 catalog，真 server 永不落库）：
    真 server 入库、占位 http://mock 不入、重复对账幂等、list_servers 挂了不炸 skill 分支。"""
    from infra.external import mcp_registry_client

    async def fake_servers():
        return [
            {"server_id": "alarm-server", "server_name": "alarm-server",
             "server_url": "https://mcpgateway.local/alarm", "description": "告警工具"},
            {"server_id": "mock-mcp", "server_name": "mock MCP", "server_url": "http://mock", "description": "占位"},
        ]

    monkeypatch.setattr(mcp_registry_client, "list_servers", fake_servers)
    first = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))
    assert first["mcps_created"] == 1
    names = {m["display_name"] for m in unwrap(client.get("/api/openops/v1/assets/mcps", headers=USER_HEADERS))["items"]}
    assert "alarm-server" in names and "mock MCP" not in names  # 真 server 入库、占位防呆

    second = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))
    assert second["mcps_created"] == 0  # create-if-missing 幂等

    async def boom():
        raise RuntimeError("console down")

    monkeypatch.setattr(mcp_registry_client, "list_servers", boom)
    third = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))
    assert "console down" in third.get("mcp_ingest_error", "")
    assert "skills_created" in third and "failed" not in third  # 注册表不可达不炸整轮


def test_real_mode_hides_placeholder_platform_mcp(client, monkeypatch):
    """真机（OPENOPS_MCPREGISTRY=real）从插件页列表滤掉 endpoint host=mock 的占位平台 MCP
    （seed 的「oModel 查询与恢复」）；mock/默认模式照常展示，真 endpoint 平台 MCP 不受影响，
    且 count 与 rows 同步收敛（total 恰 -1，分页不错乱）。与 seed 门控互补：门控挡新库、这条隐藏老库既有行。"""
    import asyncio

    from infra.repositories import assets

    def names_and_total():
        d = unwrap(client.get("/api/openops/v1/assets/mcps?source_type=platform", headers=USER_HEADERS))
        return {m["display_name"] for m in d["items"]}, d["total"]

    # 默认 mock：seed 占位 MCP 可见
    names0, total0 = names_and_total()
    assert "oModel 查询与恢复" in names0

    # 再种一个真 endpoint 的平台 MCP（host != mock）
    asyncio.run(assets.create_mcp(None, "platform", "alarm-server", "http",
                                  {"endpoint": "https://mcpgateway.local/alarm"}, {}))
    names1, total1 = names_and_total()
    assert total1 == total0 + 1
    assert {"oModel 查询与恢复", "alarm-server"} <= names1

    # 切真机：仅占位被滤，真 server 保留，total 同步 -1（count 也排除占位）
    monkeypatch.setenv("OPENOPS_MCPREGISTRY", "real")
    names2, total2 = names_and_total()
    assert "oModel 查询与恢复" not in names2
    assert "alarm-server" in names2
    assert total2 == total1 - 1


def test_real_mode_placeholder_annotation_does_not_shadow_real_tool(client, monkeypatch):
    """真机：占位平台 MCP（seed 的「oModel 查询与恢复」，endpoint=http://mock）**自带标注**，
    不得盖掉真 server 上同名工具的管理员标注。

    runtime_annotations() 按 tool_name 扁平收敛、底层 SQL 按 display_name 排序 ⇒「后者赢」；
    中文名排在 `alarm-server` 这类 ASCII 名之后，故修复前占位那条（seed: recover_execute=需审批）
    会盖掉管理员在真 server 上标的「免审批」——用户视角就是「管理台设了不生效、照样弹审批卡」。
    mock/默认模式不变：占位标注仍是 demo 工具（query_resource/recover_execute）的唯一来源。"""
    import asyncio

    from app import mcp_tool_annotation_service as svc
    from infra.repositories import assets as assets_repo
    from infra.repositories import mcp_tools

    async def _setup():
        # 真 server 暴露与占位同名的 recover_execute，管理员标注为**免审批**
        mcp = await assets_repo.create_mcp(None, "platform", "alarm-server", "http",
                                           {"endpoint": "https://mcpgateway.local/alarm"}, {})
        tcid = await mcp_tools.upsert_catalog_tool(
            mcp["mcp_version_id"], "recover_execute", "真 server 的恢复动作",
            {"type": "object", "properties": {}}, "h-real")
        await mcp_tools.save_annotation(tcid, False, False, "none", None, "allowed", None, "admin")

    asyncio.run(_setup())

    # 默认 mock：占位那条排序在后 → 赢（这正是修复前真机上的错误行为，此处作为对照钉住）
    anns = asyncio.run(svc.runtime_annotations())
    assert anns["recover_execute"]["is_approval_required"] is True

    # 真机：占位资产整体被排除 → 管理员在真 server 上的免审批终于生效
    monkeypatch.setenv("OPENOPS_MCPREGISTRY", "real")
    anns2 = asyncio.run(svc.runtime_annotations())
    assert anns2["recover_execute"]["is_approval_required"] is False
    # 仅存在于占位资产上的工具，真机下不再进运行时标注视图（真机的平台 MCP 应由注册表对账入库）
    assert "query_resource" not in anns2


def _make_skill_zip(name: str = "uploaded-skill", with_skill_md: bool = True) -> bytes:
    """内存造一个 Skill ZIP：含 SKILL.md（frontmatter name=<name>）+ run.py。"""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if with_skill_md:
            z.writestr("SKILL.md", f"---\nname: {name}\nversion: 0.0.1\nentrypoint: python3 run.py\n---\n# {name}\n")
        z.writestr("run.py", "print('hello')\n")
    return buf.getvalue()


def test_skill_zip_upload_writes_local_catalog(client):
    """真 ZIP 上传（29.3 §2.1，mock 转发）：写本地目录、真 checksum、UI 立即可见；重复上传加版本。"""
    data = _make_skill_zip("zip-upload-demo")
    res = unwrap(client.post(
        "/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
        files={"file": ("demo.zip", data, "application/zip")},
        data={"category": "运维", "tags": "监控,告警"},
    ))
    assert res["skill_key"] == "zip-upload-demo" and res["action"] == "created"
    # 本地目录立即出现（skill_key 命中），checksum 为 ZIP 字节真 sha256（非名称假造）
    import hashlib
    skills = unwrap(client.get("/api/openops/v1/assets/skills", headers=USER_HEADERS))["items"]
    row = next(s for s in skills if s["skill_key"] == "zip-upload-demo")
    assert row["checksum_sha256"] == hashlib.sha256(data).hexdigest()
    assert row["latest_version"] == "0.0.1"  # §2.1 上传响应 version → manifest → 透出（上传即可展示 semver）

    # 重复上传同 skill_key → 加版本
    again = unwrap(client.post(
        "/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
        files={"file": ("demo.zip", _make_skill_zip("zip-upload-demo"), "application/zip")},
        data={"category": "运维"},
    ))
    assert again["action"] == "version_updated"


def test_skill_zip_upload_folds_block_scalar_description(client):
    """回归插件页「说明」只显示一个 `>`：上传 SKILL.md 用 `description: >` 折叠块标量的 Skill，
    列表（=「说明」数据源）应回折叠后的真描述，而非被截断的 `>`。"""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SKILL.md",
                   "---\nname: folded-desc-skill\nversion: 0.0.1\nentrypoint: python3 run.py\n"
                   "description: >\n  这是一段较长的技能说明第一行，\n  接着第二行。\n---\n# doc\n")
        z.writestr("run.py", "print('hello')\n")
    res = unwrap(client.post(
        "/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
        files={"file": ("folded.zip", buf.getvalue(), "application/zip")},
    ))
    assert res["skill_key"] == "folded-desc-skill"
    skills = unwrap(client.get("/api/openops/v1/assets/skills?source_type=user", headers=USER_HEADERS))["items"]
    row = next(s for s in skills if s["skill_key"] == "folded-desc-skill")
    assert row["description"] == "这是一段较长的技能说明第一行， 接着第二行。"  # 折叠成一行，不再是 `>`


def test_skill_zip_upload_rejects_bad_package(client):
    """守卫：非 zip → SKILL_PACKAGE_INVALID；缺 SKILL.md → SKILL_PACKAGE_INVALID。（分类/标签已移除，非必填）"""
    not_zip = client.post("/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
                          files={"file": ("x.zip", b"not a zip", "application/zip")})
    assert not_zip.status_code == 400 and not_zip.json()["error"]["code"] == "SKILL_PACKAGE_INVALID"

    no_md = client.post("/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
                        files={"file": ("x.zip", _make_skill_zip("x", with_skill_md=False), "application/zip")})
    assert no_md.status_code == 400 and no_md.json()["error"]["code"] == "SKILL_PACKAGE_INVALID"


def test_skill_zip_upload_without_category(client):
    """分类/标签已从上传流程移除：不带 category/tags 也能上传，列表该项 category 为 None（前端回退「—」）。"""
    res = unwrap(client.post(
        "/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
        files={"file": ("nocat.zip", _make_skill_zip("nocat-skill"), "application/zip")},
    ))
    assert res["skill_key"] == "nocat-skill" and res["action"] == "created"
    skills = unwrap(client.get("/api/openops/v1/assets/skills", headers=USER_HEADERS))["items"]
    row = next(s for s in skills if s["skill_key"] == "nocat-skill")
    assert row.get("category") is None


def _personal_skill(skill_key: str, created_by: str = "l00833445") -> dict:
    """构造一条 SkillHub 映射后的个人 skill（source_type='user'，owner_user_id=上游 created_by）。"""
    return {"skill_key": skill_key, "display_name": f"我的{skill_key}", "source": "openops",
            "source_type": "user", "owner_user_id": created_by, "latest_version": "1.0.0",
            "category": "trace", "checksum_sha256": f"c-{skill_key}", "status": "active"}


def test_user_skill_synced_to_viewer_and_not_leaked(client, monkeypatch):
    """个人 skill 按 viewer 同步：归属 viewer 的 user_id（非上游 created_by）→ 本人可见；
    他人（cookie 拿不到该个人 skill）看不到，也不串号。"""
    unwrap(client.post("/api/openops/v1/admin/users/whitelist", headers=ADMIN_HEADERS,  # 让 OTHER 可访问 /assets
                       json={"client_request_id": f"wl_{time.time_ns()}", "user_id": "0099other", "display_name": "Other"}))

    async def fake_list(uid):
        return [_personal_skill("my-runbook")] if uid == "0026demo01" else []
    monkeypatch.setattr(skill_hub_client, "list_skills", fake_list)

    mine = unwrap(client.get("/api/openops/v1/assets/skills", headers=USER_HEADERS))["items"]
    row = next((s for s in mine if s["skill_key"] == "my-runbook"), None)
    assert row is not None and row["source_type"] == "user"
    assert row["owner_user_id"] == "0026demo01"  # 归 viewer，而非上游 created_by "l00833445"
    assert row["latest_version"] == "1.0.0" and row["category"] == "trace"

    other = unwrap(client.get("/api/openops/v1/assets/skills", headers=OTHER_HEADERS))["items"]
    assert all(s["skill_key"] != "my-runbook" for s in other)  # 他人看不到本人的个人 skill


def test_user_skill_sync_throttled_per_user(client, monkeypatch):
    """per-user 节流：TTL 窗口内重复列表只打一次 SkillHub。"""
    calls = {"n": 0}

    async def fake_list(uid):
        calls["n"] += 1
        return [_personal_skill("throttle-x")]
    monkeypatch.setattr(skill_hub_client, "list_skills", fake_list)

    unwrap(client.get("/api/openops/v1/assets/skills", headers=USER_HEADERS))["items"]
    unwrap(client.get("/api/openops/v1/assets/skills", headers=USER_HEADERS))["items"]
    assert calls["n"] == 1  # 第二次命中节流，不再打 SkillHub


def test_user_skill_sync_failure_not_fatal(client, monkeypatch):
    """SkillHub 挂/cookie 失效：个人 skill 同步异常被吞，列表读仍 200（读本地兜底，不 500）。"""
    async def boom(uid):
        raise RuntimeError("skill hub down")
    monkeypatch.setattr(skill_hub_client, "list_skills", boom)

    skills = unwrap(client.get("/api/openops/v1/assets/skills", headers=USER_HEADERS))["items"]
    assert isinstance(skills, list)


def test_reconcile_ignores_user_skills(client, monkeypatch):
    """全局 reconcile 只收平台 skill：个人 skill 归 sync_user_skills，reconcile 不再吞（避免错 owner 双写）。"""
    async def fake_list(uid):
        return [
            {"skill_key": "plat-x", "display_name": "平台X", "source": "openops", "source_type": "platform",
             "owner_user_id": None, "latest_version": "1.0.0", "category": "ops", "checksum_sha256": "p1", "status": "active"},
            _personal_skill("user-y"),
        ]
    monkeypatch.setattr(skill_hub_client, "list_skills", fake_list)

    unwrap(client.post("/api/openops/v1/assets:reconcile", headers=ADMIN_HEADERS))
    keys = {r[0] for r in _sql("select skill_key from sre_skill_asset where deleted_at is null", {})}
    assert "plat-x" in keys       # 平台 skill 照常 reconcile 入库
    assert "user-y" not in keys   # 个人 skill 不被全局 reconcile 吞


# ============================ 个人 skill 默认挂载 + 解绑/重新绑定（mute 模型） ============================

def _available_skill_keys(client, instance_id: str, headers=USER_HEADERS) -> set:
    return {s["skill_key"] for s in unwrap(
        client.get(f"/api/openops/v1/agent-teams/{instance_id}/available-skills", headers=headers))}


def _mute_skill(client, instance_id: str, skill: dict):
    return unwrap(client.post(
        f"/api/openops/v1/agent-teams/{instance_id}/skill-mutes", headers=USER_HEADERS,
        json={"client_request_id": f"m_{time.time_ns()}", "asset_type": "skill",
              "skill_id": skill["skill_id"], "skill_version_id": skill.get("skill_version_id")}))


def test_personal_skill_auto_bound_by_default(client):
    """个人 skill 默认自动挂载：上传后不做任何绑定，available-skills 即含；对之后新建的实例也含。"""
    inst_a = create_instance(client, "实例A")
    _upload_skill(client, "my-personal-skill")
    assert "my-personal-skill" in _available_skill_keys(client, inst_a["instance_id"])
    inst_b = create_instance(client, "实例B")  # 上传之后才建 → 默认仍挂载
    assert "my-personal-skill" in _available_skill_keys(client, inst_b["instance_id"])


def test_personal_skill_unbind_removes_and_records_mute(client):
    """解绑：available-skills 不再含；active 配置版本上记一条 muted 绑定行。"""
    inst = create_instance(client)
    s = _upload_skill(client, "to-mute")
    assert "to-mute" in _available_skill_keys(client, inst["instance_id"])
    _mute_skill(client, inst["instance_id"], s)
    assert "to-mute" not in _available_skill_keys(client, inst["instance_id"])
    muted = _sql("select status from sre_instance_asset_binding "
                 "where skill_id=%(s)s and status='muted' and deleted_at is null", {"s": s["skill_id"]})
    assert muted and muted[0][0] == "muted"


def test_personal_skill_mute_survives_resync(client, monkeypatch):
    """解绑必须扛过 ~5min 个人 skill 重新同步（同 skill_id upsert，mute 不被冲）。"""
    inst = create_instance(client)
    s = _upload_skill(client, "resync-skill")
    _mute_skill(client, inst["instance_id"], s)
    assert "resync-skill" not in _available_skill_keys(client, inst["instance_id"])

    async def fake_list(uid):
        return [_personal_skill("resync-skill")] if uid == "0026demo01" else []
    monkeypatch.setattr(skill_hub_client, "list_skills", fake_list)
    asset_registry_service.invalidate_user_skill_sync("0026demo01")
    unwrap(client.get("/api/openops/v1/assets/skills", headers=USER_HEADERS))["items"]  # 触发重新同步（upsert 同 skill_id）
    assert "resync-skill" not in _available_skill_keys(client, inst["instance_id"])  # mute 仍在


def test_personal_skill_mute_survives_derivation(client):
    """解绑必须扛过配置版本派生（save_config 结转 muted）——承重回归。"""
    inst = create_instance(client)
    s = _upload_skill(client, "derive-skill")
    _mute_skill(client, inst["instance_id"], s)
    assert "derive-skill" not in _available_skill_keys(client, inst["instance_id"])
    unwrap(client.post(
        f"/api/openops/v1/agent-teams/{inst['instance_id']}/config-versions", headers=USER_HEADERS,
        json={"client_request_id": f"cfg_{time.time_ns()}", "overlay_json": {"main_role_append": "x"},
              "change_reason": "append", "base_config_version_id": None}))
    assert "derive-skill" not in _available_skill_keys(client, inst["instance_id"])  # 结转带 muted


def test_personal_skill_rebind_restores(client):
    """重新绑定（drop mute 行）：available-skills 恢复含。"""
    inst = create_instance(client)
    s = _upload_skill(client, "rebind-skill")
    _mute_skill(client, inst["instance_id"], s)
    assert "rebind-skill" not in _available_skill_keys(client, inst["instance_id"])
    muted = [b for b in _bindings(client, inst["instance_id"]) if b.get("binding_status") == "muted"]
    assert len(muted) == 1
    unwrap(client.delete(f"/api/openops/v1/asset-bindings/{muted[0]['binding_id']}", headers=USER_HEADERS))
    assert "rebind-skill" in _available_skill_keys(client, inst["instance_id"])


def test_platform_skill_cannot_be_muted(client):
    """平台 skill 自动装配、不可解绑：调 skill-mutes → 403 且仍在。"""
    inst = create_instance(client)
    unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))  # 平台 inspection 入库
    assert "inspection" in _available_skill_keys(client, inst["instance_id"])
    plat = next(s for s in unwrap(client.get("/api/openops/v1/assets/skills", headers=USER_HEADERS))["items"]
                if s["skill_key"] == "inspection")
    resp = client.post(f"/api/openops/v1/agent-teams/{inst['instance_id']}/skill-mutes", headers=USER_HEADERS,
                       json={"client_request_id": f"m_{time.time_ns()}", "asset_type": "skill",
                             "skill_id": plat["skill_id"], "skill_version_id": plat.get("skill_version_id")})
    assert resp.status_code == 403
    assert "inspection" in _available_skill_keys(client, inst["instance_id"])


def test_legacy_active_skill_binding_can_be_muted(client):
    """存量 active 绑定行（b14b35c 之前手动绑过）也必须能解绑——不得 500。

    ux_iab_skill = (config_version_id, skill_id) 且**索引不含 status** ⇒ 同一配置版本上
    active 与 muted 不能并存：mute 时必须把旧 active 行 drop 掉再写 muted，否则唯一索引冲突。
    """
    inst = create_instance(client)
    s = _upload_skill(client, "legacy-bound-skill")
    _bind_skill(client, inst["instance_id"], s)  # 造存量 active 绑定行（老模型遗留）
    _mute_skill(client, inst["instance_id"], s)
    assert "legacy-bound-skill" not in _available_skill_keys(client, inst["instance_id"])


def test_personal_skill_mute_is_per_instance(client):
    """解绑按实例隔离：inst_a 解绑不影响 inst_b。"""
    inst_a = create_instance(client, "A")
    inst_b = create_instance(client, "B")
    s = _upload_skill(client, "iso-skill")
    _mute_skill(client, inst_a["instance_id"], s)
    assert "iso-skill" not in _available_skill_keys(client, inst_a["instance_id"])
    assert "iso-skill" in _available_skill_keys(client, inst_b["instance_id"])


# ==================== 个人 MCP 默认挂载 + 解绑/重新绑定（与 skill 同一 mute 模型） ====================
# 说明：register_mcp 现在过 check_mcp_egress（endpoint 是用户任填的 URL、且自动装配后运行时真出站）。
# egress 会 **解析 DNS**，故除专测 SSRF 的用例外一律打桩——否则用例依赖解析器（离线/CI 会红）。

@pytest.fixture
def no_egress(monkeypatch):
    monkeypatch.setattr(egress, "check_mcp_egress", lambda _url: None)


def _register_mcp(client, name: str, endpoint: str = "https://cmdb.internal/mcp") -> dict:
    return unwrap(client.post(
        "/api/openops/v1/assets/mcps", headers=USER_HEADERS,
        json={"client_request_id": f"m_{time.time_ns()}", "display_name": name,
              "transport": "http", "endpoint": endpoint, "manifest_json": {}}))


def _mute_mcp(client, instance_id: str, mcp: dict):
    return unwrap(client.post(
        f"/api/openops/v1/agent-teams/{instance_id}/mcp-mutes", headers=USER_HEADERS,
        json={"client_request_id": f"mm_{time.time_ns()}", "asset_type": "mcp",
              "skill_id": None, "skill_version_id": None,
              "mcp_id": mcp["mcp_id"], "mcp_version_id": mcp.get("mcp_version_id")}))


def _available_mcp_names(instance_id: str, uid: str = "0026demo01") -> set:
    """运行时口径：start_task 装配 st.mcp_servers 用的正是 resolve_available_mcps（同源）。"""
    import asyncio

    from app import run_state_service
    from infra.repositories import agent_teams

    async def _go():
        inst = await agent_teams.get_instance(instance_id)
        return await run_state_service.resolve_available_mcps(uid, str(inst["active_config_version_id"]))

    return {m["display_name"] for m in asyncio.run(_go())}


def test_personal_mcp_auto_mounted_by_default(client, no_egress):
    """个人 MCP 默认自动挂载：注册后不做任何绑定即在运行时集合内；对之后新建的实例也含。"""
    inst_a = create_instance(client, "实例A")
    _register_mcp(client, "我的 CMDB MCP")
    assert "我的 CMDB MCP" in _available_mcp_names(inst_a["instance_id"])
    inst_b = create_instance(client, "实例B")  # 注册之后才建 → 默认仍挂载
    assert "我的 CMDB MCP" in _available_mcp_names(inst_b["instance_id"])


def test_personal_mcp_unbind_removes_and_records_mute(client, no_egress):
    """解绑：运行时集合不再含；active 配置版本上记一条 muted 绑定行。"""
    inst = create_instance(client)
    m = _register_mcp(client, "待解绑 MCP")
    assert "待解绑 MCP" in _available_mcp_names(inst["instance_id"])
    _mute_mcp(client, inst["instance_id"], m)
    assert "待解绑 MCP" not in _available_mcp_names(inst["instance_id"])
    muted = _sql("select status from sre_instance_asset_binding "
                 "where mcp_id=%(m)s and status='muted' and deleted_at is null", {"m": m["mcp_id"]})
    assert muted and muted[0][0] == "muted"


def test_personal_mcp_mute_survives_derivation(client, no_egress):
    """解绑必须扛过配置版本派生（save_config 结转 muted）——承重回归。"""
    inst = create_instance(client)
    m = _register_mcp(client, "派生 MCP")
    _mute_mcp(client, inst["instance_id"], m)
    unwrap(client.post(
        f"/api/openops/v1/agent-teams/{inst['instance_id']}/config-versions", headers=USER_HEADERS,
        json={"client_request_id": f"cfg_{time.time_ns()}", "overlay_json": {"main_role_append": "x"},
              "change_reason": "append", "base_config_version_id": None}))
    assert "派生 MCP" not in _available_mcp_names(inst["instance_id"])  # 结转带 muted


def test_personal_mcp_rebind_restores(client, no_egress):
    """重新绑定（drop mute 行）：运行时集合恢复含。"""
    inst = create_instance(client)
    m = _register_mcp(client, "重绑 MCP")
    _mute_mcp(client, inst["instance_id"], m)
    assert "重绑 MCP" not in _available_mcp_names(inst["instance_id"])
    muted = [b for b in _bindings(client, inst["instance_id"]) if b.get("binding_status") == "muted"]
    assert len(muted) == 1
    unwrap(client.delete(f"/api/openops/v1/asset-bindings/{muted[0]['binding_id']}", headers=USER_HEADERS))
    assert "重绑 MCP" in _available_mcp_names(inst["instance_id"])


def test_personal_mcp_mute_is_idempotent_guarded(client, no_egress):
    """重复解绑 → VALIDATION_FAILED（同 (cv, mcp_id) 唯一，ux_iab_mcp）。"""
    inst = create_instance(client)
    m = _register_mcp(client, "幂等 MCP")
    _mute_mcp(client, inst["instance_id"], m)
    resp = client.post(f"/api/openops/v1/agent-teams/{inst['instance_id']}/mcp-mutes", headers=USER_HEADERS,
                       json={"client_request_id": f"mm_{time.time_ns()}", "asset_type": "mcp",
                             "skill_id": None, "skill_version_id": None,
                             "mcp_id": m["mcp_id"], "mcp_version_id": m.get("mcp_version_id")})
    assert resp.status_code == 400


def test_platform_mcp_cannot_be_muted(client, no_egress, monkeypatch):
    """平台 MCP 由模板装配、不可解绑：调 mcp-mutes → 403。"""
    async def fake_servers():
        return [{"server_id": "alarm", "server_name": "alarm-server",
                 "server_url": "https://alarm.example.com/mcp", "description": "d"}]

    monkeypatch.setattr(mcp_registry_client, "list_servers", fake_servers)
    unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))
    inst = create_instance(client)
    plat = next(r for r in unwrap(client.get("/api/openops/v1/assets/mcps", headers=USER_HEADERS))["items"]
                if r["display_name"] == "alarm-server")
    resp = client.post(f"/api/openops/v1/agent-teams/{inst['instance_id']}/mcp-mutes", headers=USER_HEADERS,
                       json={"client_request_id": f"mm_{time.time_ns()}", "asset_type": "mcp",
                             "skill_id": None, "skill_version_id": None,
                             "mcp_id": plat["mcp_id"], "mcp_version_id": plat.get("mcp_version_id")})
    assert resp.status_code == 403
    # 平台 MCP 本就不经 resolve_available_mcps（走注册表发现 + 模板白名单）
    assert "alarm-server" not in _available_mcp_names(inst["instance_id"])


def test_personal_mcp_mute_is_per_instance(client, no_egress):
    """解绑按实例隔离：inst_a 解绑不影响 inst_b。"""
    inst_a = create_instance(client, "A")
    inst_b = create_instance(client, "B")
    m = _register_mcp(client, "隔离 MCP")
    _mute_mcp(client, inst_a["instance_id"], m)
    assert "隔离 MCP" not in _available_mcp_names(inst_a["instance_id"])
    assert "隔离 MCP" in _available_mcp_names(inst_b["instance_id"])


def test_other_user_mcp_not_mounted(client, no_egress):
    """按 owner 纳入：别人的 MCP 不进我的实例（list_mcps 的 owner 过滤 + resolve 的 user 守卫）。"""
    inst = create_instance(client)
    _register_mcp(client, "我的 MCP")
    assert _available_mcp_names(inst["instance_id"], uid="0099other") == set()


def test_bind_mcp_rejected(client, no_egress):
    """MCP 无「绑定」语义：POST /asset-bindings asset_type=mcp → 400（新模型下 active mcp 行无意义）。"""
    inst = create_instance(client)
    m = _register_mcp(client, "不可绑 MCP")
    resp = client.post(f"/api/openops/v1/agent-teams/{inst['instance_id']}/asset-bindings", headers=USER_HEADERS,
                       json={"client_request_id": f"b_{time.time_ns()}", "asset_type": "mcp",
                             "skill_id": None, "skill_version_id": None,
                             "mcp_id": m["mcp_id"], "mcp_version_id": m.get("mcp_version_id")})
    assert resp.status_code == 400


def test_register_mcp_ssrf_blocked(client):
    """SSRF：endpoint 是用户任填的 URL，自动装配后运行时真出站 ⇒ 登记即拦云 metadata。
    用**字面 IP**（不依赖 DNS）保证离线可跑。"""
    resp = client.post("/api/openops/v1/assets/mcps", headers=USER_HEADERS,
                       json={"client_request_id": f"m_{time.time_ns()}", "display_name": "坏 MCP",
                             "transport": "http", "endpoint": "http://169.254.169.254/latest/meta-data/",
                             "manifest_json": {}})
    assert resp.status_code == 400
    assert "169.254.169.254" in str(resp.json()) or "受限" in str(resp.json())


def test_register_mcp_rejects_empty_endpoint(client):
    """空 endpoint 运行时不可达（resolve 会过滤掉）→ 登记即拒，别留死资产。"""
    resp = client.post("/api/openops/v1/assets/mcps", headers=USER_HEADERS,
                       json={"client_request_id": f"m_{time.time_ns()}", "display_name": "空 MCP",
                             "transport": "http", "endpoint": "", "manifest_json": {}})
    assert resp.status_code == 400
