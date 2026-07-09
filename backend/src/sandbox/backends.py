"""沙箱执行后端（B8）：容器内执行原语的双实现。

- `fake`（默认）：宿主机临时目录 + subprocess，进程内模拟。够验生命周期 / checksum /
  超时 / 审计 / 命令裁决逻辑，**不提供真 OS 隔离**（CI / 无 Docker 环境用）。
- `docker`：真 aiodocker 容器 + agentscope `DockerBackend.exec_shell`（真 OS 隔离），
  由 `OPENOPS_SANDBOX=docker` 启用；镜像/守护进程不可用时创建即失败。

两后端同一接口 `SandboxBackend`：exec_shell / write_file / read_file / close。
`exec_shell` 统一返回 `ExecResult(exit_code, stdout: str, stderr: str, timed_out)`，
stdout/stderr 已按 `max_output_bytes` 脱敏截断（命令行与产物不含平台注入项，由上层保证）。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Protocol


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _truncate(data: bytes, limit: int) -> str:
    text = data.decode("utf-8", errors="replace")
    if len(text) > limit:
        return text[:limit] + f"\n…（输出超 {limit} 字符已截断）"
    return text


class SandboxBackend(Protocol):
    workdir: str

    async def exec_shell(self, command: list[str], *, timeout: float, max_output_bytes: int) -> ExecResult: ...
    async def write_file(self, rel_path: str, data: bytes) -> None: ...
    async def read_file(self, rel_path: str) -> bytes: ...
    async def close(self) -> None: ...


class FakeBackend:
    """进程内后端：真跑 subprocess，隔离靠临时目录（非 OS 级）。"""

    def __init__(self, user_id: str) -> None:
        self._root = tempfile.mkdtemp(prefix=f"openops-sbx-{user_id}-")
        self.workdir = self._root

    async def exec_shell(self, command: list[str], *, timeout: float, max_output_bytes: int) -> ExecResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                *command, cwd=self._root, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                # fake 后端剥除敏感环境：不继承宿主 Cookie/Secret/X-OpenOps-*（真容器天然隔离）
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self._root},
            )
        except FileNotFoundError as e:
            return ExecResult(127, "", f"command not found: {e}")
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecResult(-1, "", "execution timed out", timed_out=True)
        return ExecResult(proc.returncode or 0, _truncate(out, max_output_bytes), _truncate(err, max_output_bytes))

    async def write_file(self, rel_path: str, data: bytes) -> None:
        path = os.path.join(self._root, rel_path)
        os.makedirs(os.path.dirname(path) or self._root, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    async def read_file(self, rel_path: str) -> bytes:
        with open(os.path.join(self._root, rel_path), "rb") as f:
            return f.read()

    async def close(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)


class DockerContainerBackend:
    """真 Docker 后端：aiodocker 容器 + 安全基线（非 root / 只读 rootfs / cap_drop=ALL）。"""

    CONTAINER_WORKDIR = "/openops/workspace"

    def __init__(self, user_id: str, image: str, cpu: float, mem_mib: int) -> None:
        self.user_id = user_id
        self.image = image
        self._cpu = cpu
        self._mem_mib = mem_mib
        self.workdir = self.CONTAINER_WORKDIR
        self._docker = None
        self._container = None

    async def start(self) -> None:
        import aiodocker

        self._docker = aiodocker.Docker()
        cfg = {
            "Image": self.image,
            "Cmd": ["sleep", "infinity"],
            "Tty": False,
            "WorkingDir": self.CONTAINER_WORKDIR,
            "User": "1000:1000",  # 非 root（09 号安全基线）
            "Env": [],  # 不注入 Cookie/Secret/X-OpenOps-*/effective_appids（09 号铁律）
            "HostConfig": {
                "AutoRemove": True,
                "ReadonlyRootfs": True,  # 只读 root，仅 workspace/tmp 可写
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges=true"],
                "NetworkMode": "bridge",
                "Memory": self._mem_mib * 1024 * 1024,
                "NanoCpus": int(self._cpu * 1e9),
                "Tmpfs": {self.CONTAINER_WORKDIR: "rw,size=256m", "/tmp": "rw,size=64m"},
            },
        }
        self._container = await self._docker.containers.create(config=cfg)
        await self._container.start()

    async def exec_shell(self, command: list[str], *, timeout: float, max_output_bytes: int) -> ExecResult:
        from agentscope.workspace import DockerBackend

        be = DockerBackend(self._container, self.CONTAINER_WORKDIR)
        try:
            r = await asyncio.wait_for(be.exec_shell(command, cwd=self.CONTAINER_WORKDIR), timeout=timeout)
        except asyncio.TimeoutError:
            return ExecResult(-1, "", "execution timed out", timed_out=True)
        out = r.stdout if isinstance(r.stdout, bytes) else str(r.stdout).encode()
        err = r.stderr if isinstance(r.stderr, bytes) else str(r.stderr).encode()
        return ExecResult(int(r.exit_code), _truncate(out, max_output_bytes), _truncate(err, max_output_bytes))

    async def write_file(self, rel_path: str, data: bytes) -> None:
        from agentscope.workspace import DockerBackend

        be = DockerBackend(self._container, self.CONTAINER_WORKDIR)
        await be.write_file(f"{self.CONTAINER_WORKDIR}/{rel_path}", data)

    async def read_file(self, rel_path: str) -> bytes:
        from agentscope.workspace import DockerBackend

        be = DockerBackend(self._container, self.CONTAINER_WORKDIR)
        return await be.read_file(f"{self.CONTAINER_WORKDIR}/{rel_path}")

    async def close(self) -> None:
        try:
            if self._container is not None:
                await self._container.kill()
        except Exception:  # noqa: BLE001 — 尽力回收，AutoRemove 兜底
            pass
        finally:
            if self._docker is not None:
                await self._docker.close()


async def create_backend(user_id: str, *, image: str, cpu: float, mem_mib: int) -> SandboxBackend:
    """按 OPENOPS_SANDBOX 选后端；docker 创建失败上抛（executor 转 SANDBOX_CONTAINER_FAILED）。"""
    if os.getenv("OPENOPS_SANDBOX", "fake").lower() == "docker":
        be = DockerContainerBackend(user_id, image, cpu, mem_mib)
        await be.start()
        return be
    return FakeBackend(user_id)
