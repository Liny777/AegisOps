"""Sandbox Executor（B8，09/28.10 号）：per-user 容器会话期常驻编排。

生命周期（会话期常驻，2026-07-09 拍板）：
- `ensure_user_container`：run 开启（会话）边界调用，容量准入 + 确保容器（缺失则新建）；
  同一用户复用一个容器，active_run_count 记活跃 run 数。
- `release_user_container`：末个活跃 run 关闭后 active_run_count 归零 → 置 idle（记 idle_since），
  由 `sweep_idle` 按 TTL 回收（不立即删——留复用窗口，TTL 同时兜底 run 泄漏）。
- 容量准入：容器总数（active + idle 未回收）< max_user_containers_per_host；满则先回收已到 TTL
  的 idle 腾位（strict_ttl），仍满抛 `SANDBOX_CAPACITY_FULL`。

容器运行态以进程内注册表 + Docker 为真相源（V1 单机进程，部署要求会话粘滞），不落 PG 核心表。
真容器执行原语在 `backends.py`（fake / docker 双后端）。Skill 执行见 executor.run_skill（B8-2），
容器内受控 Bash 见 runtime 装配（B8-3）。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from domain.errors import ApiError, Err
from domain.skill_package import package_checksum
from sandbox.backends import SandboxBackend, create_backend

_DEFAULT_IMAGE = "python:3.11-slim"
# 单次 Skill/命令执行限额（09/28.3 号；env 可调，同其他 B 块的 SRE_* 旋钮风格）
_SKILL_TIMEOUT_S = float(os.getenv("OPENOPS_SKILL_TIMEOUT_S", "600"))
_OUTPUT_MAX_BYTES = int(os.getenv("OPENOPS_SKILL_OUTPUT_MAX_BYTES", str(2 * 1024 * 1024)))


@dataclass
class SkillResult:
    status: str  # success / failed / timeout
    exit_code: int
    stdout: str
    stderr: str
    result_json: dict[str, Any] | None = None  # 解析 output.json（若 Skill 写了）


# 重导出（tests / 调用方用 executor.package_checksum；真定义在 domain.skill_package）
__all__ = ["SkillResult", "SandboxExecutor", "executor", "package_checksum"]


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
        }


class SandboxExecutor:
    def __init__(self) -> None:
        self._by_user: dict[str, Container] = {}
        self._lock = asyncio.Lock()

    def _cfg(self, cfg: dict[str, Any]) -> tuple[int, int, float, int]:
        return (
            int(cfg.get("max_user_containers_per_host", 26)),
            int(cfg.get("user_container_idle_ttl_minutes", 15)),
            float(cfg.get("container_cpu_limit", 0.5)),
            int(cfg.get("container_memory_limit_mib", 2048)),
        )

    def _reap_expired_idle(self, ttl_minutes: int) -> list[Container]:
        """回收已到 TTL 的 idle 容器（返回待关闭句柄，锁内摘除、锁外 close）。"""
        deadline = time.monotonic() - ttl_minutes * 60
        expired = [c for c in self._by_user.values()
                   if c.status == "idle" and c.idle_since is not None and c.idle_since <= deadline]
        for c in expired:
            self._by_user.pop(c.user_id, None)
        return expired

    async def ensure_user_container(self, user_id: str, run_id: str, cfg: dict[str, Any]) -> Container:
        """run 开启边界：容量准入 + 确保该用户容器存在，登记活跃 run。"""
        max_containers, ttl, cpu, mem = self._cfg(cfg)
        to_close: list[Container] = []
        async with self._lock:
            c = self._by_user.get(user_id)
            if c is None:
                # 容量准入：先回收已到 TTL 的 idle 腾位（strict_ttl），再判总数
                to_close = self._reap_expired_idle(ttl)
                if len(self._by_user) >= max_containers:
                    for dead in to_close:  # 锁内先 close 掉腾位容器再判，避免误拒
                        await dead.backend.close()
                    to_close = []
                    if len(self._by_user) >= max_containers:
                        raise ApiError(
                            Err.SANDBOX_CAPACITY_FULL,
                            f"当前沙箱资源已满（{len(self._by_user)}/{max_containers} 个用户容器占用中），请稍后或低峰期再开启会话",
                            retryable=True,
                        )
                try:
                    backend = await create_backend(user_id, image=_DEFAULT_IMAGE, cpu=cpu, mem_mib=mem)
                except Exception as e:  # noqa: BLE001 — 守护进程/镜像不可用等
                    raise ApiError(Err.SANDBOX_CONTAINER_FAILED, f"用户容器创建失败：{type(e).__name__}") from e
                c = Container(user_id=user_id, backend=backend, image=_DEFAULT_IMAGE)
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
        """后台/边界回收：删除已到 idle TTL 的容器。返回回收数。"""
        _, ttl, _, _ = self._cfg(cfg)
        async with self._lock:
            expired = self._reap_expired_idle(ttl)
        for c in expired:
            await c.backend.close()
        return len(expired)

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

    async def run_skill(
        self, user_id: str, *, task_id: str, tool_call_id: str, entrypoint: str,
        files: dict[str, bytes], expected_checksum: str | None = None, timeout: float | None = None,
        run_id: str | None = None, cfg: dict[str, Any] | None = None,
    ) -> SkillResult:
        """在该用户容器内执行一个脚本型 Skill（28.3）：checksum 校验 → 装包 → 执行 entrypoint。

        - `files`：Skill 包内文件（相对 skill 目录），执行前按 `expected_checksum` 校验整包一致性。
        - `entrypoint`：容器内一条 shell 命令（`python run.py` / `bash run.sh`），经 sh 通道执行。
        - 每次调用独立 workdir（`skills/{tool_call_id}/`），不与其他调用/task 共享。
        - 传 `run_id`+`cfg` 时容器缺失自动重建（_get_or_revive）；否则缺失即 SANDBOX_CONTAINER_FAILED。
        """
        c = await self._get_or_revive(user_id, run_id, cfg)
        if expected_checksum and package_checksum(files) != expected_checksum:
            raise ApiError(Err.SKILL_CHECKSUM_MISMATCH, "Skill 包 checksum 不匹配，拒绝执行")
        rel_dir = f"skills/{task_id}/{tool_call_id}"
        for name, data in files.items():
            await c.backend.write_file(f"{rel_dir}/{name}", data)
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
