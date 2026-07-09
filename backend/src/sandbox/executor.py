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
import time
from dataclasses import dataclass, field
from typing import Any

from domain.errors import ApiError, Err
from sandbox.backends import SandboxBackend, create_backend

_DEFAULT_IMAGE = "python:3.11-slim"


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
