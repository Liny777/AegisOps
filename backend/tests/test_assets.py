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
    assert insp["updated_date"] == "2026-07-20 10:30:00"  # §2.2 updated_date 原串（管理台「更新时间」列）
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
    assert insp["updated_date"] == "2026-07-20 10:30:00"  # updated_date 同轮原地回填（存量行零迁移）
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

    async def fake_discover(_name: str, _extra=None):  # 平台发现路径现传 extra_headers（x-ec2-ip）
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

    async def discover_v1(_name, _extra=None):
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

    async def discover_v2(_name, _extra=None):
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

    async def fake_servers(user_id: str = ""):
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

    async def boom(user_id: str = ""):
        raise RuntimeError("console down")

    monkeypatch.setattr(mcp_registry_client, "list_servers", boom)
    third = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))
    assert "console down" in third.get("mcp_ingest_error", "")
    assert "skills_created" in third and "failed" not in third  # 注册表不可达不炸整轮


def test_reconcile_mock_mode_does_not_pollute_real_endpoint_assets(client, monkeypatch):
    """方向守卫（内网污染防呆·路径 a）：mock 模式 discover_tools 无视 URL 恒回内置 _TOOLS
    （设计内行为，勿当 bug 修），但只许写进占位/种子资产——真 endpoint 资产被守卫跳过，
    catalog 零行、发现函数零调用。复现内网事故最小场景：真 server 资产在库 + mock 实例跑对账
    （事故形态：每个真 server 名下都多出 query_resource/recover_execute）。"""
    import asyncio

    from app import asset_reconcile_service as ars
    from infra.repositories import assets as assets_repo

    asyncio.run(assets_repo.create_mcp(None, "platform", "alarm-server", "http",
                                       {"endpoint": "https://mcpgateway.local/alarm"}, {}))

    seen_urls: list[str] = []
    real_disc = mcp_registry_client.discover_tools

    async def _spy(server_url, extra_headers=None):
        seen_urls.append(server_url)
        return await real_disc(server_url, extra_headers)

    monkeypatch.setattr(ars.mcp_registry_client, "discover_tools", _spy)
    summary = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))

    assert "failed" not in summary
    assert "alarm-server" in (summary.get("tools_skipped_guard") or [])
    assert seen_urls and all(mcp_registry_client.is_placeholder_endpoint(u) for u in seen_urls)  # 只碰占位
    assert summary["tools_unchanged"] == 2  # 种子占位资产照常同步（mock 本地端到端不受影响）
    rows = _sql(
        """select count(*) from sre_mcp_tool_catalog c
             join sre_mcp_asset_version v on v.mcp_version_id = c.mcp_version_id
             join sre_mcp_asset m on m.mcp_id = v.mcp_id
            where m.display_name = %(n)s""",
        {"n": "alarm-server"},
    )
    assert rows[0][0] == 0  # 真 server 名下 catalog 零行（污染防住）


def test_reconcile_real_mode_skips_placeholder_and_empty_endpoint_assets(client, monkeypatch):
    """方向守卫（内网污染防呆·路径 b）：real 模式下占位种子资产与 endpoint 为空的错配资产都被
    跳过（记 tools_skipped_guard，不出网不抛错、整轮不 failed）——此前 real 会对占位 endpoint
    走 discover_tools 的占位短路拿到 _TOOLS 写库。真 endpoint 资产照常同步（防守卫条件写反）。"""
    import asyncio

    from app import asset_reconcile_service as ars
    from infra.repositories import assets as assets_repo

    async def _setup():
        # endpoint 为空的错配行（内网路径 b 形态）：注册接口会校验 URL，直接走 repo 造
        await assets_repo.create_mcp(None, "platform", "空endpoint-server", "http", {"endpoint": ""}, {})
        await assets_repo.create_mcp(None, "platform", "真endpoint-server", "http",
                                     {"endpoint": "https://mcpgateway.local/real"}, {})

    asyncio.run(_setup())

    async def _servers(user_id: str = ""):
        return []  # 隔离 ingest 分支（real 需 BASE_URL，与本用例无关）

    async def _disc(server_url, extra_headers=None):
        assert not mcp_registry_client.is_placeholder_endpoint(server_url), \
            "real 模式下发现函数不该收到占位/空 URL"
        return [{"tool_name": "real_tool", "description": "d", "readonly": True,
                 "input_schema": {"type": "object", "properties": {}}, "schema_hash": "h-real"}]

    monkeypatch.setenv("OPENOPS_MCPREGISTRY", "real")
    monkeypatch.setattr(ars.mcp_registry_client, "list_servers", _servers)
    monkeypatch.setattr(ars.mcp_registry_client, "discover_tools", _disc)
    ars._reset()
    summary = asyncio.run(ars.reconcile(force=True, trigger="test"))

    assert summary.get("failed") is not True
    skipped = set(summary.get("tools_skipped_guard") or [])
    assert {"oModel 查询与恢复", "空endpoint-server"} <= skipped
    assert "真endpoint-server" not in skipped
    assert summary.get("tools_created", 0) >= 1  # 真 endpoint 照常同步


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


def test_same_name_annotations_coexist_by_composite_key(client, monkeypatch):
    """复合键身份（同名冲突根治）：占位平台 MCP（seed 的「oModel 查询与恢复」）与真 server 同名工具
    的标注**按「server::tool」各留各条、互不覆盖**——不再有「后者赢」（旧缺陷：SQL 按 display_name
    排序、中文名靠后，占位的 recover_execute=需审批盖掉管理员在真 server 上标的免审批，表现为
    「管理台设了不生效、照样弹审批卡」）。

    裸名别名只在全局唯一时给：mock 下两家同名 → 裸名缺席（fail-closed 引导用复合键）；
    real 下占位资产被排除 → 裸名别名恢复指向真 server。"""
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

    # 默认 mock：两家同名标注共存、各按各的复合键取值；裸名不再指向任何一条
    anns = asyncio.run(svc.runtime_annotations())
    assert anns["oModel 查询与恢复::recover_execute"]["is_approval_required"] is True
    assert anns["alarm-server::recover_execute"]["is_approval_required"] is False
    assert "recover_execute" not in anns  # 同名多家 → 裸名别名缺席（不猜、不互踩）
    assert anns["query_resource"]["mcp_display_name"] == "oModel 查询与恢复"  # 全局唯一的裸名别名仍在

    # 真机：占位资产整体被排除 → 裸名别名恢复指向真 server，管理员的免审批生效
    monkeypatch.setenv("OPENOPS_MCPREGISTRY", "real")
    anns2 = asyncio.run(svc.runtime_annotations())
    assert "oModel 查询与恢复::recover_execute" not in anns2
    assert anns2["recover_execute"]["is_approval_required"] is False
    assert anns2["recover_execute"]["mcp_display_name"] == "alarm-server"
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
    """真 ZIP 上传（29.3 §2.1，mock 转发）：写本地目录、真 checksum、UI 立即可见；重复上传加版本。

    29.9 键口径：本地 skill_key = 上传响应的命名空间化 skill_id（个人级 `user-{uid}-{name}`），
    display_name = SKILL.md 原始名。"""
    data = _make_skill_zip("zip-upload-demo")
    res = unwrap(client.post(
        "/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
        files={"file": ("demo.zip", data, "application/zip")},
        data={"category": "运维", "tags": "监控,告警"},
    ))
    assert res["skill_key"] == "user-0026demo01-zip-upload-demo" and res["action"] == "created"
    assert res["display_name"] == "zip-upload-demo"  # 展示名 = 原始名（横幅/插件页用）
    # 本地目录立即出现（skill_key 命中），checksum 为 ZIP 字节真 sha256（非名称假造）
    import hashlib
    skills = unwrap(client.get("/api/openops/v1/assets/skills", headers=USER_HEADERS))["items"]
    row = next(s for s in skills if s["skill_key"] == "user-0026demo01-zip-upload-demo")
    assert row["checksum_sha256"] == hashlib.sha256(data).hexdigest()
    assert row["latest_version"] == "0.0.1"  # §2.1 上传响应 version → manifest → 透出（上传即可展示 semver）
    assert row["display_name"] == "zip-upload-demo"

    # 重复上传同 skill_key（同 uploader 同名 → 同命名空间化 id）→ 加版本
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
    assert res["skill_key"] == "user-0026demo01-folded-desc-skill"  # 29.9 命名空间化键
    skills = unwrap(client.get("/api/openops/v1/assets/skills?source_type=user", headers=USER_HEADERS))["items"]
    row = next(s for s in skills if s["skill_key"] == "user-0026demo01-folded-desc-skill")
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
    assert res["skill_key"] == "user-0026demo01-nocat-skill" and res["action"] == "created"  # 29.9 命名空间化键
    skills = unwrap(client.get("/api/openops/v1/assets/skills", headers=USER_HEADERS))["items"]
    row = next(s for s in skills if s["skill_key"] == "user-0026demo01-nocat-skill")
    assert row.get("category") is None


# ============================ SkillHub 29.9：上传键口径 / 错误映射 / 删除对接 ============================


def _upload_zip_skill(client, name: str) -> dict:
    """ZIP 端点上传（mock SkillHub → synced_from='upload'，skill_key 为命名空间化 id）。"""
    return unwrap(client.post(
        "/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
        files={"file": (f"{name}.zip", _make_skill_zip(name), "application/zip")},
    ))


def test_skill_upload_prefers_response_skill_id(client, monkeypatch):
    """29.9 键口径：本地 skill_key 优先取上传响应的 skill_id（即使与 SKILL.md 裸名不同——上游才是键的
    权威）；响应缺失（29.9 未上线的旧网关）回退裸名，行为与今日一致。"""
    async def fake_upload(filename, zip_bytes, category, tags, source="openops",
                          is_system=False, uploader_id=None):
        return {"skill_id": "user-0026demo01-resp-wins", "name": "resp-wins",
                "version": "0.0.9", "status": "active", "action": "created"}
    monkeypatch.setattr(skill_hub_client, "upload_skill", fake_upload)
    res = unwrap(client.post(
        "/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
        files={"file": ("r.zip", _make_skill_zip("resp-wins"), "application/zip")},
    ))
    assert res["skill_key"] == "user-0026demo01-resp-wins" and res["display_name"] == "resp-wins"

    async def legacy_upload(filename, zip_bytes, category, tags, source="openops",
                            is_system=False, uploader_id=None):
        return {"version": "0.0.1", "action": "created"}  # 旧网关：响应无 skill_id/name
    monkeypatch.setattr(skill_hub_client, "upload_skill", legacy_upload)
    res2 = unwrap(client.post(
        "/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
        files={"file": ("l.zip", _make_skill_zip("legacy-name"), "application/zip")},
    ))
    assert res2["skill_key"] == "legacy-name" and res2["display_name"] == "legacy-name"


def test_skill_upload_name_conflict_maps_409(client, monkeypatch):
    """29.9 错误映射：上游 2004（跨作用域同名）/ 2003（他人已发布）→ 409 SKILL_NAME_CONFLICT，
    透传上游 message + 改名引导（用户可自救）；其余业务码维持 502 IAM_UPSTREAM。"""
    async def conflict(*a, **k):
        raise skill_hub_client.SkillHubError(
            "biz", "系统级已存在同名skill [foo]，个人级skill不能与系统级同名", biz_code=2004)
    monkeypatch.setattr(skill_hub_client, "upload_skill", conflict)
    resp = client.post("/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
                       files={"file": ("c.zip", _make_skill_zip("foo"), "application/zip")})
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "SKILL_NAME_CONFLICT"
    assert "系统级已存在同名skill" in err["message"] and "重新上传" in err["message"]

    async def other_biz(*a, **k):
        raise skill_hub_client.SkillHubError("biz", "服务器内部错误", biz_code=5001)
    monkeypatch.setattr(skill_hub_client, "upload_skill", other_biz)
    resp2 = client.post("/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
                        files={"file": ("c.zip", _make_skill_zip("foo"), "application/zip")})
    assert resp2.status_code == 502 and resp2.json()["error"]["code"] == "IAM_UPSTREAM"


def test_skill_upload_reserved_prefix_rejected(client):
    """29.9 §7 本地预校验：SKILL.md name 以 user-/system- 开头 → 400 SKILL_PACKAGE_INVALID（不打上游）。"""
    bad = client.post("/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
                      files={"file": ("b.zip", _make_skill_zip("user-999-evil"), "application/zip")})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "SKILL_PACKAGE_INVALID"
    assert "保留" in bad.json()["error"]["message"] or "不能以" in bad.json()["error"]["message"]


def test_skill_delete_calls_upstream_then_soft_deletes(client, monkeypatch):
    """删除对接（29.9）：来自 SkillHub 的个人 skill 先回删上游（入参=skill_key 即上游 id）再本地软删。"""
    row = _upload_zip_skill(client, "del-target")
    calls: list[str] = []

    async def fake_delete(skill_id):
        calls.append(skill_id)
        return {"skill_id": skill_id, "action": "deleted"}
    monkeypatch.setattr(skill_hub_client, "delete_skill", fake_delete)

    unwrap(client.delete(f"/api/openops/v1/assets/skills/{row['skill_id']}", headers=USER_HEADERS))
    assert calls == ["user-0026demo01-del-target"]
    skills = unwrap(client.get("/api/openops/v1/assets/skills?source_type=user", headers=USER_HEADERS))["items"]
    assert all(s["skill_key"] != "user-0026demo01-del-target" for s in skills)


def test_skill_delete_upstream_refusal_keeps_local(client, monkeypatch):
    """上游明确拒绝（biz，非缺席码）→ 502 且本地不删：否则本地消失、下轮同步复活，制造"删不掉还闪现"的困惑。"""
    row = _upload_zip_skill(client, "del-refuse")

    async def refuse(skill_id):
        raise skill_hub_client.SkillHubError("biz", "系统级skill不支持删除", biz_code=1003)
    monkeypatch.setattr(skill_hub_client, "delete_skill", refuse)

    resp = client.delete(f"/api/openops/v1/assets/skills/{row['skill_id']}", headers=USER_HEADERS)
    assert resp.status_code == 502 and "拒绝删除" in resp.json()["error"]["message"]
    skills = unwrap(client.get("/api/openops/v1/assets/skills?source_type=user", headers=USER_HEADERS))["items"]
    assert any(s["skill_key"] == "user-0026demo01-del-refuse" for s in skills)  # 本地保留


def test_skill_delete_upstream_absent_1002_deletes_local(client, monkeypatch):
    """上游 1002（资源不存在——owner 校验已前置，几乎必是"hub 已删"）→ 视作已删，本地照删。
    这是"hub 删了、本地僵尸"场景下手动清理的兜底路径（缺席同步的空子集护栏挡住时也能删）。"""
    row = _upload_zip_skill(client, "del-absent")

    async def absent(skill_id):
        raise skill_hub_client.SkillHubError("biz", "资源不存在", biz_code=1002)
    monkeypatch.setattr(skill_hub_client, "delete_skill", absent)

    unwrap(client.delete(f"/api/openops/v1/assets/skills/{row['skill_id']}", headers=USER_HEADERS))
    skills = unwrap(client.get("/api/openops/v1/assets/skills?source_type=user", headers=USER_HEADERS))["items"]
    assert all(s["skill_key"] != "user-0026demo01-del-absent" for s in skills)
    # 审计带 upstream=already_absent（降级路径可观测）
    rows = _sql("select payload_redacted_json->>'upstream' from sre_audit_event "
                "where event_type='skill.deleted' and action='delete' "
                "and payload_redacted_json->>'skill_key'='user-0026demo01-del-absent'", {})
    assert rows and rows[0][0] == "already_absent"


def test_skill_delete_endpoint_missing_degrades_local_only(client, monkeypatch):
    """上游接口未上线（HTTP 404）→ 降级仅本地删（= 29.9 前行为），不阻断用户。"""
    row = _upload_zip_skill(client, "del-degrade")

    async def gone(skill_id):
        raise skill_hub_client.SkillHubError("http", "console HTTP 404：Not Found", status_code=404)
    monkeypatch.setattr(skill_hub_client, "delete_skill", gone)

    unwrap(client.delete(f"/api/openops/v1/assets/skills/{row['skill_id']}", headers=USER_HEADERS))
    skills = unwrap(client.get("/api/openops/v1/assets/skills?source_type=user", headers=USER_HEADERS))["items"]
    assert all(s["skill_key"] != "user-0026demo01-del-degrade" for s in skills)


def test_skill_delete_network_error_retryable_keeps_local(client, monkeypatch):
    """上游不可达（network）→ 502 retryable、本地不删（结果未知，不制造两边不一致）。"""
    row = _upload_zip_skill(client, "del-netdown")

    async def down(skill_id):
        raise skill_hub_client.SkillHubError("network", "SkillHub 不可达：ConnectError")
    monkeypatch.setattr(skill_hub_client, "delete_skill", down)

    resp = client.delete(f"/api/openops/v1/assets/skills/{row['skill_id']}", headers=USER_HEADERS)
    assert resp.status_code == 502
    err = resp.json()["error"]
    assert err["retryable"] is True and "稍后重试" in err["message"]
    skills = unwrap(client.get("/api/openops/v1/assets/skills?source_type=user", headers=USER_HEADERS))["items"]
    assert any(s["skill_key"] == "user-0026demo01-del-netdown" for s in skills)


def test_skill_delete_local_manual_row_skips_upstream(client, monkeypatch):
    """本地手造行（旧 JSON 端点，manifest 无 synced_from）没有上游对应 → 不调上游、直接本地删。"""
    row = _upload_skill(client, "手造行")
    called: list[str] = []

    async def fake_delete(skill_id):
        called.append(skill_id)
        return {}
    monkeypatch.setattr(skill_hub_client, "delete_skill", fake_delete)

    unwrap(client.delete(f"/api/openops/v1/assets/skills/{row['skill_id']}", headers=USER_HEADERS))
    assert called == []


def test_skill_delete_mock_mode_end_to_end(client):
    """mock 模式（不打桩）：真 skill_hub_client.delete_skill 的 mock 分支恒成功，本地删闭环。"""
    row = _upload_zip_skill(client, "del-mock-e2e")
    unwrap(client.delete(f"/api/openops/v1/assets/skills/{row['skill_id']}", headers=USER_HEADERS))
    skills = unwrap(client.get("/api/openops/v1/assets/skills?source_type=user", headers=USER_HEADERS))["items"]
    assert all(s["skill_key"] != "user-0026demo01-del-mock-e2e" for s in skills)


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


# ============================ 缺席即墓碑（上游删除 → 本地收敛） ============================


def _age_skill_rows(*skill_keys: str) -> None:
    """把行龄拨老 1 小时：绕过缺席墓碑的创建宽限期（OPENOPS_SKILL_ABSENT_GRACE_S 默认 600s）。
    （_sql 是 SELECT 专用——UPDATE 无结果集，fetchall 会炸，这里直接 execute。）"""
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("update sre_skill_asset set creation_date = creation_date - interval '1 hour' "
                     "where skill_key = any(%(k)s)", {"k": list(skill_keys)})


def _live_user_keys(client) -> set:
    return {s["skill_key"] for s in
            unwrap(client.get("/api/openops/v1/assets/skills?source_type=user", headers=USER_HEADERS))["items"]}


def test_user_skill_absent_tombstoned_and_reappear_recreates(client, monkeypatch):
    """上游列表（非空）不再含某已同步个人 skill → 本地软删（审计 upstream_absent）；重新出现 → 重建。"""
    async def two(uid):
        return [_personal_skill("keep-a"), _personal_skill("gone-b")] if uid == "0026demo01" else []
    monkeypatch.setattr(skill_hub_client, "list_skills", two)
    assert {"keep-a", "gone-b"} <= _live_user_keys(client)  # 触发同步落库
    _age_skill_rows("keep-a", "gone-b")

    async def one(uid):
        return [_personal_skill("keep-a")] if uid == "0026demo01" else []
    monkeypatch.setattr(skill_hub_client, "list_skills", one)
    asset_registry_service.invalidate_user_skill_sync("0026demo01")
    keys = _live_user_keys(client)
    assert "keep-a" in keys and "gone-b" not in keys
    rows = _sql("select status, deleted_at from sre_skill_asset where skill_key='gone-b'", {})
    assert rows and rows[0][0] == "deleted" and rows[0][1] is not None  # 软删墓碑，非硬删
    audit_rows = _sql("select 1 from sre_audit_event where event_type='skill.deleted' "
                      "and action='upstream_absent' and payload_redacted_json->>'skill_key'='gone-b'", {})
    assert audit_rows

    # 上游重新出现 → 下轮同步重建新行（收敛闭环）
    monkeypatch.setattr(skill_hub_client, "list_skills", two)
    asset_registry_service.invalidate_user_skill_sync("0026demo01")
    assert "gone-b" in _live_user_keys(client)


def test_user_skill_absent_empty_upstream_guard(client, monkeypatch):
    """护栏：上游个人子集为**空**（cookie 失效的典型形状——list 不要求认证，200 但个人被滤光）
    → 整段跳过，不误清个人 skill。"""
    async def one(uid):
        return [_personal_skill("survive-x")] if uid == "0026demo01" else []
    monkeypatch.setattr(skill_hub_client, "list_skills", one)
    assert "survive-x" in _live_user_keys(client)
    _age_skill_rows("survive-x")

    async def empty(uid):
        return []
    monkeypatch.setattr(skill_hub_client, "list_skills", empty)
    asset_registry_service.invalidate_user_skill_sync("0026demo01")
    assert "survive-x" in _live_user_keys(client)  # 空子集不当"全删了"


def test_user_skill_absent_handmade_row_survives(client, monkeypatch):
    """老 JSON 端点手造行（manifest 无 synced_from）没有上游对应 → 上游恒缺席也不动。"""
    _upload_skill(client, "handmade")  # 本地手造（skill_key='handmade'，无 synced_from）
    _age_skill_rows("handmade")
    async def other(uid):
        return [_personal_skill("someone-else")] if uid == "0026demo01" else []
    monkeypatch.setattr(skill_hub_client, "list_skills", other)
    asset_registry_service.invalidate_user_skill_sync("0026demo01")
    assert "handmade" in _live_user_keys(client)


def test_user_skill_absent_grace_protects_fresh_row(client, monkeypatch):
    """护栏：刚上传的行（宽限期内）即使上游列表缺席也不动——防并发同步用旧列表误删新上传。"""
    async def other(uid):
        return [_personal_skill("someone-else")] if uid == "0026demo01" else []
    monkeypatch.setattr(skill_hub_client, "list_skills", other)
    _upload_zip_skill(client, "fresh-up")  # synced_from='upload'，creation_date=刚刚
    asset_registry_service.invalidate_user_skill_sync("0026demo01")
    assert "user-0026demo01-fresh-up" in _live_user_keys(client)  # 上游缺席但在宽限期内 → 保留


def test_user_skill_sync_failure_no_tombstone(client, monkeypatch):
    """列表取回失败（异常）→ 整轮放弃，不做任何墓碑（缺席集必须建立在完整列表上）。"""
    async def one(uid):
        return [_personal_skill("keep-on-fail")] if uid == "0026demo01" else []
    monkeypatch.setattr(skill_hub_client, "list_skills", one)
    assert "keep-on-fail" in _live_user_keys(client)
    _age_skill_rows("keep-on-fail")

    async def boom(uid):
        raise RuntimeError("skill hub down")
    monkeypatch.setattr(skill_hub_client, "list_skills", boom)
    asset_registry_service.invalidate_user_skill_sync("0026demo01")
    assert "keep-on-fail" in _live_user_keys(client)  # 失败不阻断读，也不墓碑


def _platform_skill(skill_key: str) -> dict:
    return {"skill_key": skill_key, "display_name": f"平台{skill_key}", "source": "openops",
            "source_type": "platform", "owner_user_id": None, "latest_version": "1.0.0",
            "category": "ops", "description": f"{skill_key} 描述", "checksum_sha256": f"p-{skill_key}",
            "status": "active"}


def test_reconcile_platform_absent_tombstoned(client, monkeypatch):
    """平台面缺席墓碑：上游列表少了一条 reconcile 写入的行 → 软删 + summary 计数；
    seed 形状行（manifest 无 synced_from）恒豁免；上游平台子集为空 → 跳过 + summary 标记。"""
    async def both(uid):
        return [_platform_skill("plat-x"), _platform_skill("plat-y")]
    monkeypatch.setattr(skill_hub_client, "list_skills", both)
    unwrap(client.post("/api/openops/v1/assets:reconcile", headers=ADMIN_HEADERS))
    _age_skill_rows("plat-x", "plat-y", "inspection")  # seed 行一并老化：证明豁免靠 synced_from 而非宽限期

    async def only_x(uid):
        return [_platform_skill("plat-x")]
    monkeypatch.setattr(skill_hub_client, "list_skills", only_x)
    summary = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=ADMIN_HEADERS))
    assert summary["skills_tombstoned"] == 1
    keys = {r[0] for r in _sql("select skill_key from sre_skill_asset "
                               "where source_type='platform' and deleted_at is null", {})}
    assert "plat-x" in keys and "plat-y" not in keys
    assert "inspection" in keys  # seed 行（无 synced_from）不被墓碑

    # 上游平台子集为空 → 护栏跳过（清空型操作要有非空上游证据）
    async def empty(uid):
        return []
    monkeypatch.setattr(skill_hub_client, "list_skills", empty)
    summary2 = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=ADMIN_HEADERS))
    assert summary2.get("skills_tombstone_skipped") == "empty_upstream"
    keys2 = {r[0] for r in _sql("select skill_key from sre_skill_asset "
                                "where source_type='platform' and deleted_at is null", {})}
    assert "plat-x" in keys2  # 未被清


def test_tombstoned_bound_skill_marked_deleted_in_bindings(client, monkeypatch):
    """被绑定的 skill 被缺席墓碑后：绑定行保留（不可变历史），asset_status 标 deleted 而非 unknown。"""
    inst = create_instance(client)
    row = _upload_zip_skill(client, "bound-gone")
    _bind_skill(client, inst["instance_id"], row)
    _age_skill_rows("user-0026demo01-bound-gone")

    async def other(uid):
        return [_personal_skill("someone-else")] if uid == "0026demo01" else []
    monkeypatch.setattr(skill_hub_client, "list_skills", other)
    asset_registry_service.invalidate_user_skill_sync("0026demo01")
    assert "user-0026demo01-bound-gone" not in _live_user_keys(client)  # in-use 不豁免：上游已删，留着也执行不了

    bindings = [b for b in _bindings(client, inst["instance_id"]) if b["asset_type"] == "skill"]
    ghost = next(b for b in bindings if str(b.get("skill_id")) == str(row["skill_id"]))
    assert ghost["asset_status"] == "deleted"  # join-miss 标注为已删，而非 unknown


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
    async def fake_servers(user_id: str = ""):
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


# ==================== 管理台平台级 Skill / MCP：上传·注册·删除·同名收敛 ====================


def _admin_upload_zip(client, name: str, **form):
    """管理台 ZIP 上传（mock SkillHub → is_system=true，skill_key='system-{name}'）。"""
    return client.post(
        "/api/openops/v1/admin/skills:upload", headers=ADMIN_HEADERS,
        files={"file": (f"{name}.zip", _make_skill_zip(name), "application/zip")},
        data=form or None)


def _live_platform_rows() -> list[tuple]:
    return _sql("select skill_key, display_name, skill_id::text from sre_skill_asset "
                "where source_type='platform' and deleted_at is null order by skill_key", {})


def _seed_platform_row(skill_key: str, display_name: str, synced_from: str | None = "skill_hub") -> str:
    """直塞一条活平台行（连版本行），模拟 reconcile 落的存量行 / seed 手造行。"""
    import json as _json
    import uuid as _uuid

    sid, vid = str(_uuid.uuid4()), str(_uuid.uuid4())
    manifest = {} if synced_from is None else {"synced_from": synced_from}
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "insert into sre_skill_asset (skill_id, source, source_type, owner_user_id, display_name,"
            " skill_key, status, created_by, last_updated_by) values"
            " (%(s)s,'openops','platform',null,%(d)s,%(k)s,'active','system','system')",
            {"s": sid, "d": display_name, "k": skill_key})
        conn.execute(
            "insert into sre_skill_asset_version (skill_version_id, skill_id, version_no, manifest_json,"
            " checksum_sha256, status, created_by, last_updated_by) values"
            " (%(v)s,%(s)s,1,%(m)s,%(c)s,'active','system','system')",
            {"v": vid, "s": sid, "m": _json.dumps(manifest), "c": f"seed-{skill_key}"})
    return sid


def test_admin_skill_upload_creates_platform_row(client):
    """管理台上传写的是**平台行**（source_type='platform'、owner 为 NULL），不是 admin 名下的个人行。

    回归闸：沿用用户面 upload_skill_package 会写成 source_type='user' 的行，与 reconcile 落的
    平台行并存 —— 这正是「管理台出现两个同名 skill」的一条来源。"""
    res = unwrap(_admin_upload_zip(client, "plat-new", category="运维", tags="监控,告警"))
    assert res["skill_key"] == "system-plat-new" and res["action"] == "created"
    assert res["display_name"] == "plat-new" and res["merged"] == 0

    rows = _sql("select source_type, owner_user_id from sre_skill_asset "
                "where skill_key='system-plat-new' and deleted_at is null", {})
    assert rows == [("platform", None)]

    plat = unwrap(client.get("/api/openops/v1/assets/skills?source_type=platform",
                             headers=ADMIN_HEADERS))["items"]
    assert any(s["skill_key"] == "system-plat-new" for s in plat)
    # 不该混进个人面（admin 自己的个人 skill 列表）
    mine = unwrap(client.get("/api/openops/v1/assets/skills?source_type=user",
                             headers=ADMIN_HEADERS))["items"]
    assert not any(s["skill_key"] == "system-plat-new" for s in mine)


def test_admin_skill_upload_sends_is_system_true(client, monkeypatch):
    """上游必须收到 is_system=True（平台级），uploader_id 为管理员工号。"""
    seen = {}

    async def fake_upload(filename, zip_bytes, category, tags, source="openops",
                          is_system=False, uploader_id=None):
        seen.update({"source": source, "is_system": is_system, "uploader_id": uploader_id,
                     "category": category, "tags": tags})
        return {"skill_id": "system-flagcheck", "name": "flagcheck", "version": "0.0.1",
                "status": "active", "action": "created"}

    monkeypatch.setattr(skill_hub_client, "upload_skill", fake_upload)
    unwrap(_admin_upload_zip(client, "flagcheck", category="运维", tags='["a","b"]'))
    assert seen["is_system"] is True and seen["source"] == "openops"
    assert seen["uploader_id"] == "admin" and seen["category"] == "运维" and seen["tags"] == ["a", "b"]


def test_admin_skill_upload_converges_legacy_bare_key(client):
    """**头号用例**：Hub 侧同名覆盖后换了键（存量裸名 `conv-me` → `system-conv-me`），
    本地旧行必须被收敛掉，管理台不再出现两个同名 skill。

    危害不止显示：resolve_skill_alias 精确键优先，两行并存时 `/conv-me` 会解析到过期旧行。"""
    old_id = _seed_platform_row("conv-me", "conv-me")  # reconcile 落的存量裸名行
    res = unwrap(_admin_upload_zip(client, "conv-me"))
    assert res["skill_key"] == "system-conv-me" and res["merged"] == 1

    live = [r for r in _live_platform_rows() if r[1] == "conv-me"]
    assert len(live) == 1 and live[0][0] == "system-conv-me", f"应只剩一条，实得 {live}"
    dead = _sql("select status, deleted_at from sre_skill_asset where skill_id=%(s)s", {"s": old_id})
    assert dead[0][0] == "deleted" and dead[0][1] is not None  # 软删而非物删

    audit = _sql("select 1 from sre_audit_event where event_type='skill.deleted' and action='converged'"
                 " and payload_redacted_json->>'skill_key'='conv-me'"
                 " and payload_redacted_json->>'reason'='same_name_rekeyed'", {})
    assert audit


def test_admin_skill_upload_converges_duplicate_same_key(client):
    """同 skill_key 的存量双行 → 上传时自愈到单行，且收敛后库重新满足唯一索引。

    这类脏行在**新库里已建不出来**（ux_skill_asset_platform_key 直接拦），故先临时摘掉索引来
    模拟「尚未跑迁移的存量内网库」——服务层的 duplicate_key 分支正是为这种库准备的兜底。"""
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("drop index ux_skill_asset_platform_key")
    a = _seed_platform_row("system-dup2", "dup2")
    b = _seed_platform_row("system-dup2", "dup2")
    assert len({a, b}) == 2

    res = unwrap(_admin_upload_zip(client, "dup2"))
    # upsert 按 key 命中**最新**那条脏行、给它加版本（幸存），收敛再扫掉另一条——
    # 收敛按 UUID 而非按 key 排除幸存行，正是为了让这种「双行共享同一 key」在同一遍里自愈
    assert res["skill_key"] == "system-dup2" and res["action"] == "version_updated"
    assert res["skill_id"] == b and res["merged"] == 1

    live = [r for r in _live_platform_rows() if r[0] == "system-dup2"]
    assert len(live) == 1 and live[0][2] == b
    assert _sql("select deleted_at from sre_skill_asset where skill_id=%(s)s", {"s": a})[0][0] is not None
    audit = _sql("select 1 from sre_audit_event where event_type='skill.deleted' and action='converged'"
                 " and payload_redacted_json->>'reason'='duplicate_key'", {})
    assert audit
    # 收敛后库已可重新建唯一索引（= 迁移脚本「先去重再建索引」的同一状态）
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("create unique index ux_skill_asset_platform_key on sre_skill_asset (skill_key)"
                     " where source_type='platform' and deleted_at is null")


def test_admin_skill_upload_concurrent_duplicate_hits_index(client):
    """唯一索引是并发兜底：同 key 的第二条**活**平台行插不进去（软删行不受限，收敛后仍可再上传）。"""
    _seed_platform_row("system-race", "race")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _seed_platform_row("system-race", "race")


def test_admin_skill_upload_spares_seed_and_handmade_rows(client):
    """护栏：manifest 无 synced_from 的行（seed / 老 JSON 端点手造）即使同名也不动。"""
    seed_id = _seed_platform_row("legacy-handmade", "spare-me", synced_from=None)
    res = unwrap(_admin_upload_zip(client, "spare-me"))
    assert res["merged"] == 0
    still = _sql("select deleted_at from sre_skill_asset where skill_id=%(s)s", {"s": seed_id})
    assert still[0][0] is None  # 豁免，未被收敛


def test_admin_skill_upload_second_time_adds_version(client):
    """同一平台 skill 二次上传 → 加版本、仍只有一条活行（0.05 → 0.06 的常规路径）。"""
    unwrap(_admin_upload_zip(client, "verbump"))
    again = unwrap(_admin_upload_zip(client, "verbump"))
    assert again["action"] == "version_updated"
    assert len([r for r in _live_platform_rows() if r[0] == "system-verbump"]) == 1
    vers = _sql("select count(*) from sre_skill_asset_version v join sre_skill_asset s"
                " on s.skill_id=v.skill_id where s.skill_key='system-verbump'", {})
    assert vers[0][0] == 2


@pytest.mark.parametrize("kind,biz,status,code", [
    ("biz", 1003, 403, "FORBIDDEN"),
    ("biz", 2003, 409, "SKILL_NAME_CONFLICT"),
    ("biz", 2004, 409, "SKILL_NAME_CONFLICT"),
    ("biz", 2002, 400, "VALIDATION_FAILED"),
    ("biz", 2005, 400, "VALIDATION_FAILED"),
    ("biz", 5001, 502, "IAM_UPSTREAM"),
    ("network", None, 502, "IAM_UPSTREAM"),
])
def test_admin_skill_upload_error_mapping(client, monkeypatch, kind, biz, status, code):
    """上游业务码 → HTTP 语义；且每种失败都**不得**在本地建行（上游优先的顺序保证）。"""
    async def boom(*a, **k):
        raise skill_hub_client.SkillHubError(kind, "上游说不行", biz_code=biz)

    monkeypatch.setattr(skill_hub_client, "upload_skill", boom)
    resp = _admin_upload_zip(client, "err-case")
    assert resp.status_code == status
    body = resp.json()["error"]
    assert body["code"] == code
    if kind == "network":
        assert body["retryable"] is True
    assert not [r for r in _live_platform_rows() if r[1] == "err-case"]


def test_admin_skill_upload_requires_admin(client):
    """普通用户打管理台上传端点 → 403。"""
    resp = client.post("/api/openops/v1/admin/skills:upload", headers=USER_HEADERS,
                       files={"file": ("x.zip", _make_skill_zip("nope"), "application/zip")})
    assert resp.status_code == 403


def test_admin_skill_upload_rejects_bad_package(client):
    """非 ZIP / 缺 SKILL.md → 400，且不打上游。"""
    assert client.post("/api/openops/v1/admin/skills:upload", headers=ADMIN_HEADERS,
                       files={"file": ("x.zip", b"not-a-zip", "application/zip")}).status_code == 400
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("run.py", "x")
    assert client.post("/api/openops/v1/admin/skills:upload", headers=ADMIN_HEADERS,
                       files={"file": ("x.zip", buf.getvalue(), "application/zip")}).status_code == 400


def test_admin_uploaded_row_tombstoned_by_reconcile(client, monkeypatch):
    """管理台上传的行（synced_from='platform_upload'）也必须进平台缺席墓碑的允许集——
    否则 Hub 侧删掉后本地永远收敛不掉。漏改 asset_reconcile_service 的允许集这条必挂。"""
    unwrap(_admin_upload_zip(client, "tomb-me"))
    _age_skill_rows("system-tomb-me")

    async def without_it(uid):
        return [_platform_skill("other-plat")]

    monkeypatch.setattr(skill_hub_client, "list_skills", without_it)
    summary = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=ADMIN_HEADERS))
    assert summary["skills_tombstoned"] >= 1
    assert not [r for r in _live_platform_rows() if r[0] == "system-tomb-me"]


# -------------------------------- 平台 Skill 删除 --------------------------------


def test_admin_skill_delete_calls_upstream_then_soft_deletes(client, monkeypatch):
    """回删上游用的是命名空间化 key，再本地软删；审计记 upstream='deleted'。"""
    res = unwrap(_admin_upload_zip(client, "del-plat"))
    seen = {}

    async def fake_delete(skill_id):
        seen["skill_id"] = skill_id
        return {"skill_id": skill_id, "action": "deleted"}

    monkeypatch.setattr(skill_hub_client, "delete_skill", fake_delete)
    out = unwrap(client.delete(f"/api/openops/v1/admin/skills/{res['skill_id']}", headers=ADMIN_HEADERS))
    assert out == {"deleted": True, "upstream": "deleted"}
    assert seen["skill_id"] == "system-del-plat"
    assert not [r for r in _live_platform_rows() if r[0] == "system-del-plat"]
    assert _sql("select 1 from sre_audit_event where event_type='skill.deleted' and action='admin_delete'"
                " and payload_redacted_json->>'upstream'='deleted'", {})


@pytest.mark.parametrize("kind,biz,st,status,code", [
    ("biz", 1003, None, 403, "FORBIDDEN"),
    ("biz", 5001, None, 502, "IAM_UPSTREAM"),
    ("network", None, None, 502, "IAM_UPSTREAM"),
])
def test_admin_skill_delete_upstream_refusal_keeps_local(client, monkeypatch, kind, biz, st, status, code):
    """上游明确拒绝/不可达 → 不本地删（否则本地消失、下轮同步复活，更困惑）。"""
    res = unwrap(_admin_upload_zip(client, "del-keep"))

    async def boom(skill_id):
        raise skill_hub_client.SkillHubError(kind, "拒绝", biz_code=biz, status_code=st)

    monkeypatch.setattr(skill_hub_client, "delete_skill", boom)
    resp = client.delete(f"/api/openops/v1/admin/skills/{res['skill_id']}", headers=ADMIN_HEADERS)
    assert resp.status_code == status and resp.json()["error"]["code"] == code
    assert [r for r in _live_platform_rows() if r[0] == "system-del-keep"]  # 本地仍在


@pytest.mark.parametrize("kind,biz,st,expect", [
    ("http", None, 404, "endpoint_missing"),   # 对端接口未上线 → 降级仅本地删
    ("biz", 1002, None, "already_absent"),     # 上游已无此 skill → 视作已删继续
])
def test_admin_skill_delete_degrades_and_deletes_local(client, monkeypatch, kind, biz, st, expect):
    res = unwrap(_admin_upload_zip(client, "del-degr"))

    async def boom(skill_id):
        raise skill_hub_client.SkillHubError(kind, "x", biz_code=biz, status_code=st)

    monkeypatch.setattr(skill_hub_client, "delete_skill", boom)
    out = unwrap(client.delete(f"/api/openops/v1/admin/skills/{res['skill_id']}", headers=ADMIN_HEADERS))
    assert out["upstream"] == expect
    assert not [r for r in _live_platform_rows() if r[0] == "system-del-degr"]


def test_admin_skill_delete_seed_row_skips_upstream(client, monkeypatch):
    """seed / 手造行（无 synced_from）无上游对应 → 不回删，只本地删。"""
    sid = _seed_platform_row("seedy", "seedy", synced_from=None)

    async def never(skill_id):
        raise AssertionError("不该回删上游")

    monkeypatch.setattr(skill_hub_client, "delete_skill", never)
    out = unwrap(client.delete(f"/api/openops/v1/admin/skills/{sid}", headers=ADMIN_HEADERS))
    assert out["upstream"] == "skipped"


def test_admin_skill_delete_rejects_user_scope_row(client):
    """管理台端点只删平台级；个人 skill 走插件页（403 而非静默删别人的资产）。"""
    row = _upload_zip_skill(client, "not-platform")
    resp = client.delete(f"/api/openops/v1/admin/skills/{row['skill_id']}", headers=ADMIN_HEADERS)
    assert resp.status_code == 403


def test_admin_skill_delete_mock_mode_end_to_end(client):
    """零 monkeypatch：mock SkillHub 下上传→删除全链闭环。"""
    res = unwrap(_admin_upload_zip(client, "e2e-plat"))
    out = unwrap(client.delete(f"/api/openops/v1/admin/skills/{res['skill_id']}", headers=ADMIN_HEADERS))
    assert out["deleted"] is True and out["upstream"] == "deleted"
    assert not [r for r in _live_platform_rows() if r[0] == "system-e2e-plat"]


def test_admin_skill_delete_allows_in_use_asset(client):
    """本次拍板：管理员删平台资产**不**被 active 绑定拦住（绑定行走 ghost 降级显示「已删除」）。"""
    res = unwrap(_admin_upload_zip(client, "bound-plat"))
    inst = create_instance(client)
    vid = _sql("select skill_version_id::text from sre_skill_asset_version where skill_id=%(s)s",
               {"s": res["skill_id"]})[0][0]
    unwrap(client.post(f"/api/openops/v1/agent-teams/{inst['instance_id']}/asset-bindings",
                       headers=USER_HEADERS,
                       json={"client_request_id": f"b_{time.time_ns()}", "asset_type": "skill",
                             "skill_id": res["skill_id"], "skill_version_id": vid,
                             "mcp_id": None, "mcp_version_id": None}))
    out = unwrap(client.delete(f"/api/openops/v1/admin/skills/{res['skill_id']}", headers=ADMIN_HEADERS))
    assert out["deleted"] is True
    binding = next(b for b in _bindings(client, inst["instance_id"]) if b.get("skill_id") == res["skill_id"])
    assert binding["asset_status"] == "deleted"  # 既有 ghost 降级链接住


# -------------------------------- 平台 MCP 注册 / 删除 --------------------------------


def _admin_register_mcp(client, name: str, url: str = "https://plat-mcp.internal/mcp", **kw):
    body = {"client_request_id": f"am_{time.time_ns()}", "server_name": name, "server_url": url,
            "description": "平台 MCP", "version": "1.0.0", "category": "监控", "tags": ["指标"],
            "transport": "streamable_http", **kw}
    return client.post("/api/openops/v1/admin/mcps", headers=ADMIN_HEADERS, json=body)


def _live_platform_mcps() -> list[tuple]:
    return _sql("select display_name, mcp_id::text from sre_mcp_asset "
                "where source_type='platform' and deleted_at is null order by display_name", {})


def test_admin_mcp_register_creates_platform_row(client, no_egress):
    """注册平台 MCP → 本地 platform 行；transport 列恒为 'http'，上游词汇存 manifest。"""
    res = unwrap(_admin_register_mcp(client, "plat-mcp-a"))
    assert res["action"] == "created" and res["server_id"] == "plat-mcp-a" and res["merged"] == 0
    row = _sql("select source_type, owner_user_id, transport, endpoint_config_json->>'endpoint'"
               " from sre_mcp_asset where mcp_id=%(m)s", {"m": res["mcp_id"]})[0]
    assert row[:3] == ("platform", None, "http") and row[3] == "https://plat-mcp.internal/mcp"
    man = _sql("select manifest_json from sre_mcp_asset_version where mcp_id=%(m)s", {"m": res["mcp_id"]})[0][0]
    assert man["server_id"] == "plat-mcp-a" and man["transport"] == "streamable_http"
    assert man["synced_from"] == "platform_register"


def test_admin_mcp_register_updates_in_place_preserving_version(client, no_egress):
    """重注册（改 URL）**原地更新**：mcp_version_id 不变 → tool catalog 与其标注不失联。"""
    first = unwrap(_admin_register_mcp(client, "plat-mcp-b", url="https://old.internal/mcp"))
    vid_before = _sql("select mcp_version_id::text from sre_mcp_asset_version where mcp_id=%(m)s",
                      {"m": first["mcp_id"]})[0][0]
    again = unwrap(_admin_register_mcp(client, "plat-mcp-b", url="https://new.internal/mcp"))
    assert again["action"] == "updated" and again["mcp_id"] == first["mcp_id"]
    vid_after = _sql("select mcp_version_id::text from sre_mcp_asset_version where mcp_id=%(m)s",
                     {"m": first["mcp_id"]})
    assert len(vid_after) == 1 and vid_after[0][0] == vid_before  # 未派生新版本
    ep = _sql("select endpoint_config_json->>'endpoint' from sre_mcp_asset where mcp_id=%(m)s",
              {"m": first["mcp_id"]})[0][0]
    assert ep == "https://new.internal/mcp"  # endpoint 已更新


def test_admin_mcp_register_converges_duplicate(client, no_egress, monkeypatch):
    """同 server_id 的存量重复行（上游改名留下的）→ 注册时收敛到单行。"""
    first = unwrap(_admin_register_mcp(client, "plat-mcp-c"))
    # 造一条同 server_id、不同 display_name 的存量行（模拟上游改名前 reconcile 落的）
    import json as _json
    import uuid as _uuid
    dup, dupv = str(_uuid.uuid4()), str(_uuid.uuid4())
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("insert into sre_mcp_asset (mcp_id, source, source_type, owner_user_id, display_name,"
                     " transport, endpoint_config_json, status, created_by, last_updated_by) values"
                     " (%(m)s,'openops','platform',null,'plat-mcp-c-old','http',"
                     " '{\"endpoint\":\"https://x/mcp\"}','active','system','system')", {"m": dup})
        conn.execute("insert into sre_mcp_asset_version (mcp_version_id, mcp_id, version_no, manifest_json,"
                     " status, created_by, last_updated_by) values"
                     " (%(v)s,%(m)s,1,%(j)s,'active','system','system')",
                     {"v": dupv, "m": dup, "j": _json.dumps({"synced_from": "mcp_registry",
                                                             "server_id": "plat-mcp-c"})})
    again = unwrap(_admin_register_mcp(client, "plat-mcp-c"))
    assert again["mcp_id"] == first["mcp_id"] and again["merged"] == 1
    assert _sql("select deleted_at from sre_mcp_asset where mcp_id=%(m)s", {"m": dup})[0][0] is not None
    assert not [r for r in _live_platform_mcps() if r[0] == "plat-mcp-c-old"]


def test_admin_mcp_register_spares_seed_placeholder(client, no_egress):
    """seed 占位资产（manifest 为 {}）不被收敛扫到。"""
    before = {r[1] for r in _live_platform_mcps()}
    unwrap(_admin_register_mcp(client, "plat-mcp-d"))
    after = {r[1] for r in _live_platform_mcps()}
    assert before <= after  # 只增不减：既有平台行（含 seed 占位）一个没少


def test_admin_mcp_register_rejects_bad_transport_before_upstream(client, no_egress, monkeypatch):
    """非法 transport 在打上游前就拒。422 = schema 层 pattern 拦下（与 asset_type/status 等既有
    Field(pattern=…) 同款口径，本仓无 RequestValidationError 处理器）；服务层同名校验作为
    非 HTTP 调用方的兜底保留。要点是**没打上游**。"""
    async def never(**kw):
        raise AssertionError("不该打上游")

    monkeypatch.setattr(mcp_registry_client, "register_server", never)
    assert _admin_register_mcp(client, "bad-tp", transport="grpc").status_code == 422


def test_admin_mcp_register_ssrf_blocked(client):
    """平台 MCP 的 URL 同样人手填、运行时真出站 → 同一道 SSRF 闸。"""
    resp = _admin_register_mcp(client, "evil-mcp", url="http://169.254.169.254/latest/meta-data/")
    assert resp.status_code == 400


@pytest.mark.parametrize("kind,biz,status,code", [
    ("biz", 2001, 409, "ASSET_IN_USE"),
    ("biz", 1003, 403, "FORBIDDEN"),
    ("biz", 1005, 400, "VALIDATION_FAILED"),
    ("biz", 5001, 502, "IAM_UPSTREAM"),
    ("network", None, 502, "IAM_UPSTREAM"),
])
def test_admin_mcp_register_error_mapping(client, no_egress, monkeypatch, kind, biz, status, code):
    async def boom(**kw):
        raise mcp_registry_client.McpRegistryError(kind, "上游说不行", biz_code=biz)

    monkeypatch.setattr(mcp_registry_client, "register_server", boom)
    resp = _admin_register_mcp(client, "err-mcp")
    assert resp.status_code == status and resp.json()["error"]["code"] == code
    assert not [r for r in _live_platform_mcps() if r[0] == "err-mcp"]


def test_admin_mcp_delete_cascades_and_audits(client, no_egress, monkeypatch):
    """删平台 MCP：回删注册表 → 本地软删 → 模板草稿引用级联清理；审计带 upstream。"""
    res = unwrap(_admin_register_mcp(client, "plat-mcp-del"))
    seen = {}

    async def fake_del(server_id):
        seen["server_id"] = server_id
        return {"server_id": server_id, "deleted_by": "admin"}

    monkeypatch.setattr(mcp_registry_client, "delete_server", fake_del)
    out = unwrap(client.delete(f"/api/openops/v1/admin/mcps/{res['mcp_id']}", headers=ADMIN_HEADERS))
    assert out == {"deleted": True, "upstream": "deleted"} and seen["server_id"] == "plat-mcp-del"
    assert not [r for r in _live_platform_mcps() if r[0] == "plat-mcp-del"]
    assert _sql("select 1 from sre_audit_event where event_type='mcp.deleted' and action='admin_delete'"
                " and payload_redacted_json->>'upstream'='deleted'", {})


def test_admin_mcp_delete_upstream_refusal_keeps_local(client, no_egress, monkeypatch):
    res = unwrap(_admin_register_mcp(client, "plat-mcp-keep"))

    async def boom(server_id):
        raise mcp_registry_client.McpRegistryError("biz", "非超管", biz_code=1003)

    monkeypatch.setattr(mcp_registry_client, "delete_server", boom)
    resp = client.delete(f"/api/openops/v1/admin/mcps/{res['mcp_id']}", headers=ADMIN_HEADERS)
    assert resp.status_code == 403
    assert [r for r in _live_platform_mcps() if r[0] == "plat-mcp-keep"]


def test_admin_mcp_delete_rejects_user_scope_row(client, no_egress):
    m = _register_mcp(client, "我的自定义 MCP")
    resp = client.delete(f"/api/openops/v1/admin/mcps/{m['mcp_id']}", headers=ADMIN_HEADERS)
    assert resp.status_code == 403


def test_admin_mcp_register_delete_mock_end_to_end(client, no_egress):
    """零 monkeypatch：mock 注册表下注册→删除全链闭环。"""
    res = unwrap(_admin_register_mcp(client, "plat-mcp-e2e"))
    out = unwrap(client.delete(f"/api/openops/v1/admin/mcps/{res['mcp_id']}", headers=ADMIN_HEADERS))
    assert out["deleted"] is True and out["upstream"] == "deleted"


def test_reconcile_mcp_ingest_dedupes_by_server_id(client, monkeypatch):
    """reconcile 的 MCP ingest 按 display_name ∪ server_id 双判：本地名与上游 server_name 不一致
    （管理台原地更新不改名的正常结果）时，不得再造一条重复行。"""
    import json as _json
    import uuid as _uuid
    mid, vid = str(_uuid.uuid4()), str(_uuid.uuid4())
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("insert into sre_mcp_asset (mcp_id, source, source_type, owner_user_id, display_name,"
                     " transport, endpoint_config_json, status, created_by, last_updated_by) values"
                     " (%(m)s,'openops','platform',null,'local-old-name','http',"
                     " '{\"endpoint\":\"https://s/mcp\"}','active','system','system')", {"m": mid})
        conn.execute("insert into sre_mcp_asset_version (mcp_version_id, mcp_id, version_no, manifest_json,"
                     " status, created_by, last_updated_by) values"
                     " (%(v)s,%(m)s,1,%(j)s,'active','system','system')",
                     {"v": vid, "m": mid, "j": _json.dumps({"synced_from": "mcp_registry",
                                                            "server_id": "srv-renamed"})})

    async def upstream(user_id=""):
        return [{"server_id": "srv-renamed", "server_name": "upstream-new-name",
                 "server_url": "https://s/mcp", "description": ""}]

    monkeypatch.setattr(mcp_registry_client, "list_servers", upstream)
    summary = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=ADMIN_HEADERS))
    assert summary["mcps_created"] == 0, "同 server_id 已在本地，不该按新名字再造一行"
    assert not [r for r in _live_platform_mcps() if r[0] == "upstream-new-name"]
