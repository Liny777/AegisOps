"""Sandbox Executor（B8，09/28.10 号）：per-user 容器会话期常驻编排。

生命周期（会话期常驻，2026-07-09 拍板）：
- `ensure_user_container`：run 开启（会话）边界调用，容量准入 + 确保容器（缺失则新建）；
  同一用户复用一个容器，active_run_count 记活跃 run 数。
- `release_user_container`：末个活跃 run 关闭后 active_run_count 归零 → 置 idle（记 idle_since），
  由 `sweep_idle` 按 TTL 回收（不立即删——留复用窗口，TTL 同时兜底 run 泄漏）。
- 容量准入：容器总数（active + idle 未回收）< max_user_containers_per_host；满则先回收已到 TTL
  的 idle 腾位（strict_ttl），再与 Docker 探活对账回收带外死亡的 failed 残留（`_reap_dead`），
  仍满才抛 `SANDBOX_CAPACITY_FULL`。

容器运行态以进程内注册表 + Docker 为真相源（V1 单机进程，部署要求会话粘滞），不落 PG 核心表。
真容器执行原语在 `backends.py`（fake / docker 双后端）。Skill 执行见 executor.run_skill（B8-2），
容器内受控 Bash 见 runtime 装配（B8-3）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from domain.errors import ApiError, Err
from domain.skill_package import package_checksum
from sandbox.backends import SandboxBackend, create_backend

log = logging.getLogger("openops.sandbox")

_DEFAULT_IMAGE = "python:3.11-slim"
_NETWORK_MODES = {"bridge", "none"}
# 单次 Skill/命令执行限额（09/28.3 号；env 可调，同其他 B 块的 SRE_* 旋钮风格）
_SKILL_TIMEOUT_S = float(os.getenv("OPENOPS_SKILL_TIMEOUT_S", "600"))
_OUTPUT_MAX_BYTES = int(os.getenv("OPENOPS_SKILL_OUTPUT_MAX_BYTES", str(2 * 1024 * 1024)))
# 容量对账单容器探活超时：探活只为腾位，宁可超时放过（视为存活）也不拖住准入
_ALIVE_PROBE_TIMEOUT_S = float(os.getenv("OPENOPS_SANDBOX_ALIVE_PROBE_TIMEOUT_S", "3"))


def _as_int(v: Any, default: int) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _as_float(v: Any, default: float) -> float:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ResolvedCfg:
    """从 runtime_config sandbox 域解析出的强类型运行配置（全部带兜底，容忍字符串/脏值）。"""
    max_containers: int
    ttl_minutes: int
    cpu: float
    mem_mib: int
    image: str
    network_mode: str
    pids_limit: int
    capacity_full_policy: str  # 容量满策略：V1 仅 strict_ttl（先回收已到 TTL 的 idle 再判，不提前抢占）


@dataclass
class SkillResult:
    status: str  # success / failed / timeout
    exit_code: int
    stdout: str
    stderr: str
    result_json: dict[str, Any] | None = None  # 解析 output.json（若 Skill 写了）


# 重导出（tests / 调用方用 executor.package_checksum；真定义在 domain.skill_package）
__all__ = ["SkillResult", "SandboxExecutor", "executor", "package_checksum", "ALERT_SANDBOX_UID"]

# 7x24 告警接管的平台共享沙箱伪用户键（设计 v2.1 决策⑥）：全员告警诊断共用这一只容器，
# 计入主机名额恒 1 个；追问任务切回用户自己容器（决策⑦）。前缀 sys- 保证不与真实工号冲撞。
# 容器寻址一律走 TaskState.sandbox_uid 单一事实源，本常量是它在 alert-origin 下的取值。
ALERT_SANDBOX_UID = "sys-alert-sandbox"


@dataclass
class Container:
    user_id: str
    backend: SandboxBackend
    image: str
    status: str = "active"  # active（有活跃 run）/ idle（无活跃 run，待 TTL 回收）/ failed
    active_run_count: int = 0
    active_run_ids: set[str] = field(default_factory=set)
    created_at: float = field(default_factory=time.monotonic)
    idle_since: float | None = None

    def public(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "runtime_status": self.status,
            "image_version": self.image,
            "active_run_count": self.active_run_count,
            "idle_seconds": None if self.idle_since is None else round(time.monotonic() - self.idle_since, 1),
            "container_name": getattr(self.backend, "container_name", None),  # fake 后端无此属性→None
        }


def _reject_unsafe_names(files: dict[str, bytes]) -> None:
    """拒绝会写出 per-tool_call 目录之外的 ZIP 条目名（`../`、绝对路径、NUL）。

    `skill_hub_client._unzip` 对 SkillHub 下发的条目名零校验，而投递路径是 `{rel_dir}/{name}`
    直拼——`../../x` 可跨 task 污染同一用户容器。落盘前一次性拒掉整包。
    """
    for name in files:
        if (name.startswith(("/", "\\")) or "\x00" in name
                or ".." in name.replace("\\", "/").split("/")):
            raise ApiError(Err.SKILL_PACKAGE_INVALID, f"Skill 包含非法文件路径，拒绝投递：{name[:80]}")


class SandboxExecutor:
    def __init__(self) -> None:
        self._by_user: dict[str, Container] = {}
        self._lock = asyncio.Lock()

    def _cfg(self, cfg: dict[str, Any]) -> ResolvedCfg:
        image = str(cfg.get("container_image") or "").strip() or _DEFAULT_IMAGE
        net = str(cfg.get("container_network_mode") or "bridge").strip().lower() or "bridge"
        if net not in _NETWORK_MODES:  # 历史脏值/直改 DB：落回 bridge（网络是 skill 外呼的功能依赖，收紧应显式）
            log.warning("[sandbox] 非法 container_network_mode=%r，回退 bridge", net)
            net = "bridge"
        pids = _as_int(cfg.get("container_pids_limit"), 256)
        # 容量满策略：V1 仅实现 strict_ttl；写侧校验（runtime_config_service）已拦非法值，此处对脏值/历史值兜底回退
        policy = str(cfg.get("capacity_full_policy") or "strict_ttl").strip().lower() or "strict_ttl"
        if policy != "strict_ttl":
            log.warning("[sandbox] 不支持的 capacity_full_policy=%r，回退 strict_ttl", policy)
            policy = "strict_ttl"
        return ResolvedCfg(
            max_containers=_as_int(cfg.get("max_user_containers_per_host"), 26),
            ttl_minutes=_as_int(cfg.get("user_container_idle_ttl_minutes"), 15),
            cpu=_as_float(cfg.get("container_cpu_limit"), 0.5),
            mem_mib=_as_int(cfg.get("container_memory_limit_mib"), 2048),
            image=image,
            network_mode=net,
            pids_limit=pids if pids > 0 else 256,
            capacity_full_policy=policy,
        )

    def _reap_expired_idle(self, ttl_minutes: int) -> list[Container]:
        """回收已到 TTL 的 idle 容器（返回待关闭句柄，锁内摘除、锁外 close）。"""
        deadline = time.monotonic() - ttl_minutes * 60
        expired = [c for c in self._by_user.values()
                   if c.status == "idle" and c.idle_since is not None and c.idle_since <= deadline]
        for c in expired:
            self._by_user.pop(c.user_id, None)
        return expired

    async def _reap_dead(self) -> list[Container]:
        """探活对账（Docker 为真相源）：带外死亡的容器标 failed 并摘出注册表，返回待关闭句柄。

        09/11 号设计「回收 idle 和 **failed 残留**」+「重新统计 Docker/Runtime 中的活跃容器」：
        容器若在会话中途带外死亡（docker kill / OOM / 守护进程重启），注册表仍记着它 → 一直占着
        名额直到其 run 关闭且 idle TTL 到期。本对账把注册表拉回 Docker 真相，把死容器腾出来。

        保守语义：探活超时/异常=**不确定** → 一律视为存活跳过，绝不因 daemon 抖动误杀他人名额。
        """
        containers = list(self._by_user.values())
        # 并发探活：本方法在准入锁内跑，串行最坏 N×timeout（26 个容器 × 守护进程挂死）会长时间
        # 堵死所有人的开会话；并发后锁占用被压到约 1×timeout，与容器数无关。
        oks = await asyncio.gather(*(self._probe_alive(c) for c in containers))
        dead: list[Container] = []
        for c, ok in zip(containers, oks):
            if not ok:
                c.status = "failed"
                self._by_user.pop(c.user_id, None)
                dead.append(c)
                log.warning("[sandbox] 容器带外死亡，回收 failed 残留腾位 user=%s", c.user_id)
        return dead

    async def _probe_alive(self, c: Container) -> bool:
        """单容器探活；不确定（无探活能力/超时/异常）一律回 True（保守，绝不误杀）。"""
        probe = getattr(c.backend, "alive", None)
        if probe is None:  # 后端未实现探活（测试替身）→ 视为存活
            return True
        try:
            return bool(await asyncio.wait_for(probe(), timeout=_ALIVE_PROBE_TIMEOUT_S))
        except Exception:  # noqa: BLE001 — 超时/异常=不确定 → 保守视为存活
            return True

    async def ensure_user_container(self, user_id: str, run_id: str, cfg: dict[str, Any]) -> Container:
        """run 开启边界：容量准入 + 确保该用户容器存在，登记活跃 run。"""
        rc = self._cfg(cfg)
        to_close: list[Container] = []
        async with self._lock:
            c = self._by_user.get(user_id)
            if c is None:
                # 容量准入：strict_ttl 策略先回收已到 TTL 的 idle 腾位（不提前抢占未到 TTL 的 idle），再判总数
                if rc.capacity_full_policy == "strict_ttl":
                    to_close = self._reap_expired_idle(rc.ttl_minutes)
                if len(self._by_user) >= rc.max_containers:
                    for dead in to_close:  # 锁内先 close 掉腾位容器再判，避免误拒
                        await dead.backend.close()
                    to_close = []
                    # 仍满 → 与 Docker 对账一次再判（设计：回收 failed 残留 + 按真实存活重新统计）。
                    # 只在拒绝前做：探活有成本，而这正是计数准确性唯一要紧的时刻。
                    for gone in await self._reap_dead():
                        await gone.backend.close()
                    if len(self._by_user) >= rc.max_containers:
                        raise ApiError(
                            Err.SANDBOX_CAPACITY_FULL,
                            f"当前沙箱资源已满，已有 {len(self._by_user)}/{rc.max_containers} 个用户容器占用中。请稍后或低峰期再试。",
                            retryable=True,
                        )
                try:
                    backend = await create_backend(user_id, image=rc.image, cpu=rc.cpu, mem_mib=rc.mem_mib,
                                                   network_mode=rc.network_mode, pids_limit=rc.pids_limit)
                except Exception as e:  # noqa: BLE001 — 守护进程/镜像不可用等
                    raise ApiError(Err.SANDBOX_CONTAINER_FAILED, f"用户容器创建失败：{type(e).__name__}") from e
                c = Container(user_id=user_id, backend=backend, image=rc.image)
                self._by_user[user_id] = c
            c.active_run_ids.add(run_id)
            c.active_run_count = len(c.active_run_ids)
            c.status = "active"
            c.idle_since = None
        for dead in to_close:
            await dead.backend.close()
        return c

    async def release_user_container(self, user_id: str, run_id: str) -> None:
        """末个活跃 run 关闭边界：摘除该 run，归零则置 idle 交 TTL 回收。"""
        async with self._lock:
            c = self._by_user.get(user_id)
            if c is None:
                return
            c.active_run_ids.discard(run_id)
            c.active_run_count = len(c.active_run_ids)
            if c.active_run_count == 0:
                c.status = "idle"
                c.idle_since = time.monotonic()

    async def sweep_idle(self, cfg: dict[str, Any]) -> int:
        """后台/边界回收：删除已到 idle TTL 的容器 + 带外死亡的 failed 残留。返回回收数。

        failed 残留在此主动回收（不必等到有人撞容量满），顺带让管理台容器列表不显示已死容器。
        """
        rc = self._cfg(cfg)
        async with self._lock:
            expired = self._reap_expired_idle(rc.ttl_minutes)
            dead = await self._reap_dead()
        for c in expired + dead:
            await c.backend.close()
        return len(expired) + len(dead)

    async def _get_or_revive(self, user_id: str, run_id: str | None, cfg: dict[str, Any] | None) -> Container:
        """执行边界取容器；缺失且带 run 上下文则按容量准入现场重建（自愈）。

        会话期常驻（create_run 边界 ensure）是**优化不是正确性前提**：进程重启丢内存注册表 /
        idle TTL 回收后，DB 里 run 仍 active，复用旧 run 的任务在此自愈重建（内网实测：
        后端重启后 /alarm-query 报「用户容器不存在」）。容量满仍 SANDBOX_CAPACITY_FULL fail-closed；
        无 run/cfg 上下文的调用维持原 fail-closed 语义。
        """
        c = self._by_user.get(user_id)
        if c is None and run_id and cfg is not None:
            c = await self.ensure_user_container(user_id, run_id, cfg)
        if c is None:
            raise ApiError(Err.SANDBOX_CONTAINER_FAILED, "用户容器不存在（run 未开启或已回收）")
        return c

    async def _stage(self, c: Container, rel_dir: str, files: dict[str, bytes],
                     expected_checksum: str | None) -> None:
        """投递整包进容器（只写不执行）：路径校验 → checksum 校验 → 逐文件写入（两后端均自动建父目录）。

        全有全无：任一校验不过则一个字节都不落盘（checksum 保的是整包一致性，半包目录会让 agent
        面对「看起来完整实则缺文件」的现场，比直接降级更难排查）。
        """
        _reject_unsafe_names(files)
        if expected_checksum and package_checksum(files) != expected_checksum:
            raise ApiError(Err.SKILL_CHECKSUM_MISMATCH, "Skill 包 checksum 不匹配，拒绝投递")
        for name, data in files.items():
            await c.backend.write_file(f"{rel_dir}/{name}", data)

    async def stage_files(
        self, user_id: str, *, task_id: str, tool_call_id: str, files: dict[str, bytes],
        expected_checksum: str | None = None,
        run_id: str | None = None, cfg: dict[str, Any] | None = None,
    ) -> tuple[str, list[str]]:
        """统一投递原语（skill 不分脚本型/手册型都进容器）：返回 (容器内绝对目录, 排序文件名)。

        手册型 Skill 由 run_bound_skill 显式调用（references/ 供 agent 经 run_container_command 按需读取）；
        脚本型经 run_skill 内部同一 `_stage` 路径。传 `run_id`+`cfg` 时容器缺失自动重建（_get_or_revive）。"""
        c = await self._get_or_revive(user_id, run_id, cfg)
        rel_dir = f"skills/{task_id}/{tool_call_id}"
        await self._stage(c, rel_dir, files, expected_checksum)
        return f"{c.backend.workdir}/{rel_dir}", sorted(files)

    async def run_skill(
        self, user_id: str, *, task_id: str, tool_call_id: str, entrypoint: str,
        files: dict[str, bytes], expected_checksum: str | None = None, timeout: float | None = None,
        run_id: str | None = None, cfg: dict[str, Any] | None = None,
    ) -> SkillResult:
        """在该用户容器内执行一个脚本型 Skill（28.3）：checksum 校验 → 装包（_stage 同投递原语）→ 执行 entrypoint。

        - `files`：Skill 包内文件（相对 skill 目录），执行前按 `expected_checksum` 校验整包一致性。
        - `entrypoint`：容器内一条 shell 命令（`python run.py` / `bash run.sh`），经 sh 通道执行。
        - 每次调用独立 workdir（`skills/{tool_call_id}/`），不与其他调用/task 共享。
        - 传 `run_id`+`cfg` 时容器缺失自动重建（_get_or_revive）；否则缺失即 SANDBOX_CONTAINER_FAILED。
        """
        c = await self._get_or_revive(user_id, run_id, cfg)
        rel_dir = f"skills/{task_id}/{tool_call_id}"
        await self._stage(c, rel_dir, files, expected_checksum)
        cmd = ["sh", "-lc", f"cd {c.backend.workdir}/{rel_dir} && {entrypoint}"]
        r = await c.backend.exec_shell(cmd, timeout=timeout or _SKILL_TIMEOUT_S, max_output_bytes=_OUTPUT_MAX_BYTES)
        if r.timed_out:
            raise ApiError(Err.SKILL_TIMEOUT, f"Skill 执行超时（>{int(timeout or _SKILL_TIMEOUT_S)}s），已终止")
        result_json = None
        try:  # 约定 Skill 把结构化结果写 output.json（28.3）
            raw = await c.backend.read_file(f"{rel_dir}/output.json")
            result_json = json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001 — 无 output.json 时仅回 stdout 摘要，不报错
            pass
        return SkillResult(
            status="success" if r.exit_code == 0 else "failed",
            exit_code=r.exit_code, stdout=r.stdout, stderr=r.stderr, result_json=result_json,
        )

    async def run_command(
        self, user_id: str, command: str, *, timeout: float | None = None,
        run_id: str | None = None, cfg: dict[str, Any] | None = None,
    ) -> SkillResult:
        """容器内执行一条 Bash 命令（B8-3 受控 Bash 的执行原语；管控/审计在装配层）。"""
        c = await self._get_or_revive(user_id, run_id, cfg)
        r = await c.backend.exec_shell(["sh", "-lc", command], timeout=timeout or _SKILL_TIMEOUT_S,
                                       max_output_bytes=_OUTPUT_MAX_BYTES)
        status = "timeout" if r.timed_out else ("success" if r.exit_code == 0 else "failed")
        return SkillResult(status=status, exit_code=r.exit_code, stdout=r.stdout, stderr=r.stderr)

    def get(self, user_id: str) -> Container | None:
        return self._by_user.get(user_id)

    def list_containers(self) -> list[dict[str, Any]]:
        return [c.public() for c in self._by_user.values()]

    async def destroy(self, user_id: str) -> bool:
        """管理员强制销毁（B8-4）：中断该用户容器（含运行中 run）。"""
        async with self._lock:
            c = self._by_user.pop(user_id, None)
        if c is None:
            return False
        await c.backend.close()
        return True

    async def close_all(self) -> None:
        """lifespan 收口：回收全部容器。"""
        async with self._lock:
            containers = list(self._by_user.values())
            self._by_user.clear()
        for c in containers:
            await c.backend.close()


executor = SandboxExecutor()
