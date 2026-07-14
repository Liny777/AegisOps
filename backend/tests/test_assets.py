from __future__ import annotations

import os
import time

import psycopg
from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, create_run, unwrap, wait_until
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


def test_asset_mcp_endpoint_redacted(client):
    """用户 MCP endpoint 展示脱敏（B6-TEST-001/30.5）：只回截断值，完整 endpoint 与 token 不出现。"""
    unwrap(client.post(
        "/api/openops/v1/assets/mcps", headers=USER_HEADERS,
        json={"client_request_id": f"m_{time.time_ns()}", "display_name": "内部 CMDB MCP",
              "transport": "http", "endpoint": "https://internal.example.com/mcp?token=supersecret123",
              "manifest_json": {}},
    ))
    rows = unwrap(client.get("/api/openops/v1/assets/mcps", headers=USER_HEADERS))
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
    names = {m["display_name"] for m in unwrap(client.get("/api/openops/v1/assets/mcps", headers=USER_HEADERS))}
    assert "alarm-server" in names and "mock MCP" not in names  # 真 server 入库、占位防呆

    second = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))
    assert second["mcps_created"] == 0  # create-if-missing 幂等

    async def boom():
        raise RuntimeError("console down")

    monkeypatch.setattr(mcp_registry_client, "list_servers", boom)
    third = unwrap(client.post("/api/openops/v1/assets:reconcile", headers=USER_HEADERS))
    assert "console down" in third.get("mcp_ingest_error", "")
    assert "skills_created" in third and "failed" not in third  # 注册表不可达不炸整轮


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
    skills = unwrap(client.get("/api/openops/v1/assets/skills", headers=USER_HEADERS))
    row = next(s for s in skills if s["skill_key"] == "zip-upload-demo")
    assert row["checksum_sha256"] == hashlib.sha256(data).hexdigest()

    # 重复上传同 skill_key → 加版本
    again = unwrap(client.post(
        "/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
        files={"file": ("demo.zip", _make_skill_zip("zip-upload-demo"), "application/zip")},
        data={"category": "运维"},
    ))
    assert again["action"] == "version_updated"


def test_skill_zip_upload_rejects_bad_package(client):
    """守卫：非 zip → SKILL_PACKAGE_INVALID；缺 SKILL.md → SKILL_PACKAGE_INVALID；缺分类 → 400。"""
    not_zip = client.post("/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
                          files={"file": ("x.zip", b"not a zip", "application/zip")}, data={"category": "运维"})
    assert not_zip.status_code == 400 and not_zip.json()["error"]["code"] == "SKILL_PACKAGE_INVALID"

    no_md = client.post("/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
                        files={"file": ("x.zip", _make_skill_zip("x", with_skill_md=False), "application/zip")},
                        data={"category": "运维"})
    assert no_md.status_code == 400 and no_md.json()["error"]["code"] == "SKILL_PACKAGE_INVALID"

    no_cat = client.post("/api/openops/v1/assets/skills:upload", headers=USER_HEADERS,
                        files={"file": ("x.zip", _make_skill_zip("x"), "application/zip")}, data={"category": "  "})
    assert no_cat.status_code == 400
