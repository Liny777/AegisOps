"""B8 沙箱执行面测试（fake 后端；真 Docker 用例见 test_sandbox_docker 标记）。

覆盖 31 号：SBX-001（首 run 建容器 / 末 run 关闭后 idle→TTL 回收）、SBX-002（同用户复用）、
CANCEL-007（容量满开 run 被拒）。生命周期直接驱动 SandboxExecutor（不依赖 Docker）。
"""
from __future__ import annotations

import asyncio
import os
import shutil
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
        # 繁忙文案对齐设计文档（11-单机容量与资源配额）：含 当前/上限 计数、可重试；断言防措辞漂移
        assert ei.value.message == "当前沙箱资源已满，已有 2/2 个用户容器占用中。请稍后或低峰期再试。"
        assert ei.value.retryable is True
        # u1 关闭 → idle 但未到 TTL：strict_ttl 下不提前回收，u3 仍被拒
        await sandbox_executor.release_user_container("u1", "r1")
        with pytest.raises(ApiError):
            await sandbox_executor.ensure_user_container("u3", "r3", _CFG)
        # idle TTL=0 → 立即可回收腾位，u3 成功
        c = await sandbox_executor.ensure_user_container("u3", "r3", {**_CFG, "user_container_idle_ttl_minutes": 0})
        assert c.status == "active"
        await sandbox_executor.close_all()

    asyncio.run(scenario())


def test_sbx_004_dead_container_reclaimed_on_capacity_full(client):
    """设计 R3 步骤4/5：带外死亡的容器（failed 残留）不该一直占名额——容量满时与真相源对账回收腾位。

    d1/d2 都持有**活跃 run**（非 idle，TTL 回收够不着）；d1 容器带外死亡后，d3 仍应能开会话。
    """
    async def scenario():
        c1 = await sandbox_executor.ensure_user_container("d1", "r1", _CFG)
        c2 = await sandbox_executor.ensure_user_container("d2", "r2", _CFG)  # 满（max=2）
        # 模拟带外死亡：fake 后端的临时根目录被外部删除 == docker 容器被 kill/OOM 后消失
        shutil.rmtree(c1.backend._root, ignore_errors=True)
        assert await c1.backend.alive() is False and await c2.backend.alive() is True

        c3 = await sandbox_executor.ensure_user_container("d3", "r3", _CFG)
        assert c3.status == "active"                    # 死容器腾位后新用户开得了会话（修前这里被误拒）
        assert sandbox_executor.get("d1") is None       # 死容器已摘出注册表
        assert c1.status == "failed"                    # 标记为 failed 残留
        assert sandbox_executor.get("d2") is not None   # 活容器不被误杀
        await sandbox_executor.close_all()

    asyncio.run(scenario())


def test_sbx_005_alive_probe_failure_never_evicts(client):
    """探活不确定（超时/异常）= 保守视为存活：绝不因 daemon 抖动误杀在跑的容器名额。"""
    from domain.errors import ApiError

    async def scenario():
        c1 = await sandbox_executor.ensure_user_container("p1", "r1", _CFG)
        await sandbox_executor.ensure_user_container("p2", "r2", _CFG)  # 满（max=2）

        async def _boom() -> bool:
            raise RuntimeError("docker daemon 抖动")

        c1.backend.alive = _boom  # type: ignore[method-assign]
        with pytest.raises(ApiError):  # 探活异常 → 视为存活 → 照常拒绝，不误回收
            await sandbox_executor.ensure_user_container("p3", "r3", _CFG)
        assert sandbox_executor.get("p1") is not None  # 没被误杀
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


def test_sbx_004_reclaim_idle_runs_frees_slot(client):
    """Fix#2「TTL 兜底 run 泄漏」：无活动超 run_idle_ttl 的活跃 run 被回收——关 run（idle_timeout/system）
    + 容器名额释放（count 归零转 idle）；有 running 主任务的 run 存活否决不回收。合成用户隔离，避免跨用例污染。"""
    import uuid as _uuid

    from app import run_state_service
    from infra.db import exec1
    from infra.repositories import runs as runs_repo
    from runtime import task_registry
    from runtime.task_registry import TaskState

    uid = "u_reclaim_" + _uuid.uuid4().hex[:6]
    rid = str(_uuid.uuid4())
    cfg = {**_CFG, "run_idle_ttl_minutes": 30}  # 容器 idle TTL 保持 15：sweep 不误伤其他用例的新 idle 容器

    async def scenario():
        # 合成 active run（sre_agent_run 无 FK，可直插）+ 容器；started_at 拨到 1 小时前 → 越阈值候选
        await runs_repo.create_run(uid, str(_uuid.uuid4()), str(_uuid.uuid4()),
                                   "fsid-" + _uuid.uuid4().hex[:8], str(_uuid.uuid4()), run_id=rid)
        await exec1("update sre_agent_run set started_at = now() - interval '1 hour' where agent_run_id=%(r)s", {"r": rid})
        await sandbox_executor.ensure_user_container(uid, rid, cfg)
        assert sandbox_executor.get(uid).active_run_count == 1

        # 负例：内存有 running 主任务 → 存活否决，run 不被回收
        st = TaskState(task_id="tsk_" + _uuid.uuid4().hex[:10], run_id=rid, user_id=uid,
                       instance_id=str(_uuid.uuid4()), input_text="x")
        task_registry.put(st)
        await run_state_service.reclaim_idle_runs(cfg)
        assert (await runs_repo.get_run(rid))["run_status"] == "active"

        # 任务结束后回收：run closed(idle_timeout) + 容器 active_run_count 归零转 idle
        st.status = "completed"
        n = await run_state_service.reclaim_idle_runs(cfg)
        assert n == 1
        row = await runs_repo.get_run(rid)
        assert row["run_status"] == "closed" and row["status_reason_code"] == "idle_timeout"
        c = sandbox_executor.get(uid)
        assert c is not None and c.status == "idle" and c.active_run_count == 0

        # 清理：容器 + registry + DB 行
        await sandbox_executor.destroy(uid)
        task_registry._by_run.pop(rid, None)
        await exec1("delete from sre_agent_run where agent_run_id=%(r)s", {"r": rid})
        await exec1("delete from sre_audit_event where agent_run_id=%(r)s", {"r": rid})

    asyncio.run(scenario())


def test_fix3a_emit_snapshot_skips_subagent_rows(client):
    """Fix#3 主修：子 Agent（leader_task_id 非空）的 approval.required 不落 sre_task_state（否则泄漏永不
    终态的 running 子行污染 count_running）；主任务照常落快照。"""
    import uuid as _uuid

    from infra.db import exec1
    from infra.repositories import task_states
    from runtime.emit import emit
    from runtime.task_registry import TaskState

    rid, trace, inst = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    uid = "u_emit_" + _uuid.uuid4().hex[:6]
    run = {"agent_run_id": rid, "audit_trace_id": trace, "agent_team_instance_id": inst}
    main = TaskState(task_id="tsk_" + _uuid.uuid4().hex[:10], run_id=rid, user_id=uid, instance_id=inst, input_text="x")
    child = TaskState(task_id=main.task_id + ".inspect-deadbeef", run_id=rid, user_id=uid, instance_id=inst, input_text="y")
    child.leader_task_id, child.agent_key = main.task_id, "inspect"

    async def scenario():
        await emit(main, run, "openops.task.started", action="start")
        await emit(child, run, "openops.approval.required", action="ask")
        assert await task_states.get_by_task(main.task_id) is not None   # 主任务落快照
        assert await task_states.get_by_task(child.task_id) is None      # 子行不落
        await exec1("delete from sre_task_state where task_id=%(t)s", {"t": main.task_id})
        await exec1("delete from sre_audit_event where agent_run_id=%(r)s", {"r": rid})

    asyncio.run(scenario())


def test_fix3b_count_running_and_latest_exclude_subagent_rows(client):
    """Fix#3 纵深：count_running / get_latest_by_run 只认主任务（task_id 无 '.'）——即便进程内已存在
    历史泄漏的 running 子行，也不计入并发限额、不顶替 get_state 投影（无需等重启 converge）。"""
    import uuid as _uuid

    from infra.db import exec1
    from infra.repositories import task_states
    from runtime.task_registry import TaskState

    uid = "u_cnt_" + _uuid.uuid4().hex[:6]
    rid = str(_uuid.uuid4())
    main = TaskState(task_id="tsk_" + _uuid.uuid4().hex[:10], run_id=rid, user_id=uid,
                     instance_id=str(_uuid.uuid4()), input_text="m")
    child = TaskState(task_id=main.task_id + ".inspect-deadbeef", run_id=rid, user_id=uid,
                      instance_id=main.instance_id, input_text="c")
    child.leader_task_id = main.task_id

    async def scenario():
        await task_states.upsert_snapshot(main, "running", str(_uuid.uuid4()))
        await task_states.upsert_snapshot(child, "running", str(_uuid.uuid4()))  # 模拟历史泄漏子行（晚于主行）
        assert await task_states.count_running(uid) == 1                          # 只数主任务
        latest = await task_states.get_latest_by_run(rid)
        assert latest is not None and latest["task_id"] == main.task_id           # 投影不被子行顶替
        await exec1("delete from sre_task_state where task_id in (%(m)s, %(c)s)",
                    {"m": main.task_id, "c": child.task_id})

    asyncio.run(scenario())


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


def _skill_run_ctx(client, *, crid: str, task_id: str, skill_key: str):
    """016-018 共用脚手架：建实例+run（run 创建边界已 ensure 容器）→ 直接构造 TaskState（014 模式）。"""
    from runtime.task_registry import TaskState

    instance = create_instance(client)
    run_row = unwrap(client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS,
                                 json={"client_request_id": crid, "agent_team_instance_id": instance["instance_id"]}))["run"]
    st = TaskState(task_id=task_id, run_id=str(run_row["agent_run_id"]), user_id="0026demo01",
                   instance_id=instance["instance_id"], input_text="x")
    st.available_skills = {skill_key: {"version_no": 1}}
    run = {"agent_run_id": run_row["agent_run_id"], "audit_trace_id": run_row["audit_trace_id"],
           "agent_team_instance_id": run_row["agent_team_instance_id"]}
    return st, run, run_row


def test_skill_016_manual_skill_stages_references_into_container(client, monkeypatch):
    """统一进容器（2026-07-15 拍板）：手册型 Skill 整包投递沙箱，references/ 经容器命令真读回；
    返回文本含 目录+文件清单+cat 指引+手册正文（修「agent 没按 skill 内容执行」：此前 references 全被丢弃）。"""
    import re

    from infra.external import skill_hub_client
    from runtime.sandbox_skill import run_bound_skill
    from sandbox.executor import executor as sandbox_executor
    from sandbox.executor import package_checksum

    ref_body = "细则：先查 A 再查 B，阈值 0.95。".encode()
    files = {"SKILL.md": "# alarm-query 手册\n先读 references/steps.md 再操作。".encode(),
             "references/steps.md": ref_body}

    async def _pkg(_key, _ver):
        return {"files": dict(files), "entrypoint": None, "checksum": package_checksum(files)}

    monkeypatch.setattr(skill_hub_client, "download_skill_package", _pkg)
    st, run, run_row = _skill_run_ctx(client, crid="sk_ref", task_id="tk-ref", skill_key="alarm-query")

    async def scenario():
        txt = await run_bound_skill(st, run, "alarm-query")
        assert "手册型技能" in txt and "references/steps.md" in txt and "run_container_command" in txt
        assert st.tool_blocked is False
        # 从返回文本取出容器目录，经容器命令真读回 references 内容（fake 后端 exec_shell 是真 subprocess）
        m = re.search(r"容器目录 (\S+)/：", txt)
        assert m, txt
        res = await sandbox_executor.run_command("0026demo01", f"cat {m.group(1)}/references/steps.md")
        assert res.exit_code == 0 and ref_body.decode() in res.stdout

    asyncio.run(scenario())
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run_row['agent_run_id']}", headers=USER_HEADERS))
    ok = [e for e in events if e["event_type"] == "openops.skill.call.succeeded"]
    assert ok and "含 1 个参考文件" in str(ok[0]["payload_redacted_json"].get("summary", ""))


def test_skill_017_manual_stage_failure_degrades_gracefully(client, monkeypatch):
    """投递优雅降级：stage 失败（容器抖动等）不让手册型调用整体失败——正文照常返回 + 文本说明降级。"""
    from infra.external import skill_hub_client
    from runtime.sandbox_skill import run_bound_skill
    from sandbox.executor import executor as sandbox_executor

    async def _pkg(_key, _ver):
        return {"files": {"SKILL.md": "# 手册\n降级演练正文。".encode()}, "entrypoint": None, "checksum": ""}

    async def _boom(*_a, **_k):
        raise RuntimeError("container down")

    monkeypatch.setattr(skill_hub_client, "download_skill_package", _pkg)
    monkeypatch.setattr(sandbox_executor, "stage_files", _boom)
    st, run, _ = _skill_run_ctx(client, crid="sk_deg", task_id="tk-deg", skill_key="alarm-query")

    async def scenario():
        txt = await run_bound_skill(st, run, "alarm-query")
        assert "手册型技能" in txt and "降级演练正文" in txt  # 手册主体仍达
        assert "投递失败" in txt  # 降级显式说明
        assert st.tool_blocked is False  # 不算拦截/失败

    asyncio.run(scenario())


def test_skill_018_script_skill_notes_staged_dir(client, monkeypatch):
    """脚本型统一口径：执行结果文本追加「已投递至容器目录」一行（同一指引，模型可查阅包内参考文件）。"""
    from infra.external import skill_hub_client
    from runtime.sandbox_skill import run_bound_skill
    from sandbox.executor import package_checksum

    files = {"SKILL.md": b"entrypoint: python3 run.py", "run.py": b"print('SCRIPT-OK')"}

    async def _pkg(_key, _ver):
        return {"files": dict(files), "entrypoint": "python3 run.py", "checksum": package_checksum(files)}

    monkeypatch.setattr(skill_hub_client, "download_skill_package", _pkg)
    st, run, _ = _skill_run_ctx(client, crid="sk_scr", task_id="tk-scr", skill_key="change-query")

    async def scenario():
        txt = await run_bound_skill(st, run, "change-query")
        assert "SCRIPT-OK" in txt  # 脚本真执行（fake 后端真 subprocess python3）
        assert "已投递至容器目录" in txt and "run_container_command" in txt

    asyncio.run(scenario())


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
    # capacity_full_policy：V1 仅接受 strict_ttl，其他值写侧拒 400（防"看似可配实则不生效"）
    r3 = client.put(base, headers=ADMIN_HEADERS,
                    json={"client_request_id": "s4", "updates": {"capacity_full_policy": "aggressive"}, "reason": "x"})
    assert r3.status_code == 400
    unwrap(client.put(base, headers=ADMIN_HEADERS,
                      json={"client_request_id": "s5", "updates": {"capacity_full_policy": "strict_ttl"}, "reason": "固定策略"}))


def test_run_004_per_user_running_task_limit_rejects(client):
    """RUN-004（设计 R5）：每用户 running task 达 per_user_running_task_limit → 新 task 拒 429。

    与开 run 的 SANDBOX_CAPACITY_FULL 是两道独立闸门：这道只管该用户并发任务数，不碰容器名额。
    """
    from infra.repositories import runtime_config
    from runtime import task_registry
    from runtime.task_registry import TaskState

    instance = create_instance(client)
    run1 = create_run(client, instance["instance_id"])
    run2 = create_run(client, instance["instance_id"])  # 同用户复用同一容器，不涉容量

    async def _limit_to_1():
        await runtime_config.upsert(runtime_config.DOMAIN_SANDBOX, "per_user_running_task_limit", 1, reason="t")

    asyncio.run(_limit_to_1())
    # run1 上挂一个 running task 占满限额（limit=1）
    task_registry.put(TaskState(task_id="tk_busy", run_id=str(run1["agent_run_id"]), user_id="0026demo01",
                                instance_id=instance["instance_id"], input_text="占位"))

    r = client.post(f"/api/openops/v1/agent-runs/{run2['agent_run_id']}/tasks", headers=USER_HEADERS,
                    json={"client_request_id": f"cc{time.time_ns()}", "input_text": "第二个任务"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "USER_TASK_CONCURRENCY_LIMIT"

    # 占位 task 结束 → 名额释放，新 task 放行（证明拒绝来自限额而非其他 fail-closed）
    task_registry.get_by_task("tk_busy").status = "completed"
    r2 = client.post(f"/api/openops/v1/agent-runs/{run2['agent_run_id']}/tasks", headers=USER_HEADERS,
                     json={"client_request_id": f"cc{time.time_ns()}", "input_text": "再来"})
    assert r2.status_code < 400, r2.text


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


# ─────── B8·补3：官方 Read/Grep（绑容器后端）+ list_container_files + 放宽输出上限（Skill 截断修复）───────

def _bash_ctx(client, *, crid: str, task_id: str):
    """B8·补3 共用脚手架：建实例+run（创建边界已 ensure 容器）→ TaskState（带 sandbox_cfg）+ run dict。"""
    from runtime.task_registry import TaskState

    instance = create_instance(client)
    run_row = unwrap(client.post("/api/openops/v1/agent-runs", headers=USER_HEADERS,
                                 json={"client_request_id": crid, "agent_team_instance_id": instance["instance_id"]}))["run"]
    st = TaskState(task_id=task_id, run_id=str(run_row["agent_run_id"]), user_id="0026demo01",
                   instance_id=instance["instance_id"], input_text="x")
    st.sandbox_cfg = {}  # 非 None → 工具不 fail-closed；容器缺失时经 run_id+cfg 自愈重建
    return st, _run_dict(run_row), run_row


def _tool_text(res) -> str:
    """从 agentscope ToolChunk/ToolResponse 取出文本（content 块可能是 TypedDict 或对象）。"""
    parts = []
    for b in getattr(res, "content", []) or []:
        t = b.get("text") if isinstance(b, dict) else getattr(b, "text", None)
        if t:
            parts.append(t)
    return "\n".join(parts)


def test_sandbox_tool_backend_reads_execs_and_writes(client):
    """B8·补3：容器后端适配器把 agentscope BackendBase 的 FS 调用路由进用户容器——
    exec_shell/read_file/write_file/file_exists 都在容器内生效（fake 后端真 subprocess 验证）。"""
    from runtime.sandbox_tool_backend import SandboxToolBackend

    st, _, _ = _bash_ctx(client, crid="stb", task_id="tk_stb")

    async def scenario():
        c = sandbox_executor.get("0026demo01")
        await c.backend.write_file("hello.txt", b"line1\nline2\n")
        be = SandboxToolBackend("0026demo01", st.run_id, {})
        p = f"{c.backend.workdir}/hello.txt"

        assert await be.file_exists(p) is True                                   # 派生自 exec_shell(test -e)
        assert await be.file_exists(f"{c.backend.workdir}/nope.txt") is False
        assert await be.read_file(p) == b"line1\nline2\n"                          # base64 读绝对路径
        r = await be.exec_shell(["echo", "hi"])
        assert r.exit_code == 0 and r.stdout == b"hi\n"                            # stdout 为 bytes（agentscope 约定）
        await be.write_file(f"{c.backend.workdir}/w.txt", b"xyz")                  # 写回容器
        assert await be.read_file(f"{c.backend.workdir}/w.txt") == b"xyz"

    asyncio.run(scenario())


def test_official_read_tool_reads_container_file(client):
    """B8·补3：官方 agentscope Read 绑容器后端 → 在**容器内**按行读文件（带行号），
    解决 Skill 手册/references 大文件被截断读不全。"""
    from agentscope.tool import Read

    from runtime.sandbox_tool_backend import SandboxToolBackend

    st, _, _ = _bash_ctx(client, crid="ord", task_id="tk_ord")

    async def scenario():
        c = sandbox_executor.get("0026demo01")
        body = ("\n".join(f"line-{i}" for i in range(1, 21)) + "\n").encode()
        await c.backend.write_file("doc.md", body)
        be = SandboxToolBackend("0026demo01", st.run_id, {})

        res = await Read(backend=be).call(file_path=f"{c.backend.workdir}/doc.md")
        text = _tool_text(res)
        assert "line-1" in text and "line-20" in text  # 全文读到（官方 Read 自带行号）
        # 绝对路径校验：相对路径被官方 Read 拒（证明 backend 已生效、走的是官方逻辑）
        rel = await Read(backend=be).call(file_path="doc.md")
        assert "absolute" in _tool_text(rel).lower()

    asyncio.run(scenario())


@pytest.mark.skipif(shutil.which("rg") is None,
                    reason="官方 Grep 依赖 ripgrep（沙箱镜像已加 ripgrep；本机跑此用例需装 rg）")
def test_official_grep_tool_searches_container(client):
    """B8·补3：官方 agentscope Grep 绑容器后端 → 在**容器内**用 ripgrep 搜内容（rg 在镜像里）。"""
    from agentscope.tool import Grep

    from runtime.sandbox_tool_backend import SandboxToolBackend

    st, _, _ = _bash_ctx(client, crid="org", task_id="tk_org")

    async def scenario():
        c = sandbox_executor.get("0026demo01")
        await c.backend.write_file("refs/g.md", "alpha\nNEEDLE_here 命中行\nbeta\n".encode())
        await c.backend.write_file("refs/other.md", b"no match here\n")
        be = SandboxToolBackend("0026demo01", st.run_id, {})

        res = await Grep(backend=be).call(pattern="NEEDLE_here",
                                          path=f"{c.backend.workdir}/refs", output_mode="content")
        text = _tool_text(res)
        assert "NEEDLE_here" in text  # ripgrep 在容器内命中

    asyncio.run(scenario())


def test_permission_context_allows_official_read_grep(client):
    """B8·补3：_permission_context 给官方 Read/Grep + list_container_files 授 tool 级 allow（防裁剪）；
    旧的自定义 read_container_file 已移除。"""
    from runtime.agentscope_runtime import _permission_context
    from runtime.task_registry import TaskState

    st = TaskState(task_id="tkp", run_id="r", user_id="0026demo01", instance_id="i", input_text="x")
    ctx = _permission_context(st)
    names = set(ctx.allow_rules)
    assert {"Read", "Grep", "list_container_files", "run_container_command"} <= names
    assert "read_container_file" not in names  # 已替换为官方 Read


def test_list_container_files_lists_and_filters(client):
    """B8·补3：list_container_files 列目录 + 可选通配过滤（find 可移植 flag，直接经 executor 只读执行）。"""
    from runtime.sandbox_bash import list_container_files

    st, run, run_row = _bash_ctx(client, crid="lcf", task_id="tk_lcf")

    async def scenario():
        c = sandbox_executor.get("0026demo01")
        await c.backend.write_file("refs/a.md", b"aaa")
        await c.backend.write_file("refs/b.txt", b"bbb")
        d = f"{c.backend.workdir}/refs"

        allf = await list_container_files(st, run, d)
        assert "a.md" in allf and "b.txt" in allf

        only_md = await list_container_files(st, run, d, pattern="*.md")
        assert "a.md" in only_md and "b.txt" not in only_md

    asyncio.run(scenario())
    events = unwrap(client.get(f"/api/openops/v1/audit/runs/{run_row['agent_run_id']}", headers=USER_HEADERS))
    assert any(e["event_type"] == "openops.sandbox.file.list" for e in events)


def test_run_container_command_output_cap_relaxed_with_notice(client):
    """B8·补3：run_container_command 回模型上限从硬 2000 放宽到 _BASH_OUTPUT_CHARS(16000)；超限时截断并
    追加可续读提示（指向官方 Read 工具）。用 cat 已暂存文件——只读自动放行，不触 HITL。"""
    from runtime.sandbox_bash import _BASH_OUTPUT_CHARS, run_container_command

    st, run, _ = _bash_ctx(client, crid="cap", task_id="tk_cap")

    async def scenario():
        c = sandbox_executor.get("0026demo01")
        # 3000 字符（> 旧 2000、< 16000）：现在完整返回，旧口径会截在中途
        await c.backend.write_file("mid.txt", b"B" * 3000)
        mid = await run_container_command(st, run, f"cat {c.backend.workdir}/mid.txt")
        assert ("B" * 3000) in mid and "已截断" not in mid

        # 20000 字符（> 16000）：截断 + 提示，长度回落到上限附近
        await c.backend.write_file("huge.txt", b"A" * 20000)
        huge = await run_container_command(st, run, f"cat {c.backend.workdir}/huge.txt")
        assert "已截断" in huge and "Read 工具" in huge
        assert len(huge) <= _BASH_OUTPUT_CHARS + 400  # 正文 + 提示语尾巴

    asyncio.run(scenario())


def test_manual_skill_body_truncation_marked_and_points_to_read_tool(client, monkeypatch):
    """B8·补3：手册型 Skill 正文超 cap 时不再静默丢尾——追加截断标记并指向容器内 SKILL.md 全文；
    staged_note 指向官方 Read 工具。"""
    monkeypatch.setenv("OPENOPS_SKILL_MANUAL_CAP", "60")  # 压低 cap 强制截断（无需超大正文）
    from infra.external import skill_hub_client
    from runtime.sandbox_skill import run_bound_skill
    from sandbox.executor import package_checksum

    long_md = ("# 排查手册\n" + "先查 A 再查 B，阈值 0.95。" * 20).encode()  # 远超 60 字符
    files = {"SKILL.md": long_md, "references/steps.md": b"detail"}

    async def _pkg(_key, _ver):
        return {"files": dict(files), "entrypoint": None, "checksum": package_checksum(files)}

    monkeypatch.setattr(skill_hub_client, "download_skill_package", _pkg)
    st, run, _ = _skill_run_ctx(client, crid="sk_trunc", task_id="tk-trunc", skill_key="alarm-query")

    async def scenario():
        txt = await run_bound_skill(st, run, "alarm-query")
        assert "Read 工具" in txt and "file_path=" in txt          # 提示语改指官方 Read
        assert "已截断" in txt and "SKILL.md" in txt and "读取全文" in txt  # 截断标记 + 全文入口
        assert st.tool_blocked is False

    asyncio.run(scenario())
