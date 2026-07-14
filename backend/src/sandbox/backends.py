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
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger("openops.sandbox")

# 容器标签（对账 / 多环境隔离）：LABEL_MANAGED 标记平台托管；LABEL_USER 存原始 user_id（label 无字符集
# 限制，供审计/排障）；LABEL_SCOPE 隔离同宿主多环境（test/prod 各自只清自己的孤儿，互不误删）。
LABEL_MANAGED = "com.openops.sandbox"
LABEL_USER = "com.openops.sandbox.user"
LABEL_SCOPE = "com.openops.sandbox.scope"


def _label_scope() -> str:
    return os.getenv("OPENOPS_SANDBOX_LABEL_SCOPE", "default").strip() or "default"


def container_name(user_id: str) -> str:
    """确定性容器名（每用户恒一名，兼作宿主侧互斥）：消毒非法字符 + sha256 短哈希保唯一。

    docker 容器名字符集为 [a-zA-Z0-9][a-zA-Z0-9_.-]*——user_id 可能含中文/@//，消毒后可能同名碰撞，
    附 8 位哈希兜唯一；前缀 openops-sbx- 保证首字符合法且便于宿主侧按名过滤。
    """
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", user_id)[:24].lstrip("-_.") or "u"
    return f"openops-sbx-{safe}-{digest}"


def build_container_config(*, user_id: str, image: str, cpu: float, mem_mib: int,
                           network_mode: str, pids_limit: int) -> dict[str, Any]:
    """构造 aiodocker 容器 config（抽纯便于单测断言安全基线，不碰真 docker）。

    安全基线（09 号）：非 root(uid 1000) / 只读 rootfs / cap_drop=ALL / no-new-privileges /
    资源限额(cpu·mem·pids) / 仅 workspace·tmp 可写 tmpfs / Env 不注入平台上下文。
    """
    workdir = DockerContainerBackend.CONTAINER_WORKDIR
    return {
        "Image": image,
        "Cmd": ["sleep", "infinity"],
        "Tty": False,
        "WorkingDir": workdir,
        "User": "1000:1000",  # 非 root（09 号安全基线）
        "Env": [],  # 不注入 Cookie/Secret/X-OpenOps-*/effective_appids（09 号铁律）
        "Labels": {LABEL_MANAGED: "1", LABEL_USER: user_id, LABEL_SCOPE: _label_scope()},
        "HostConfig": {
            "AutoRemove": True,
            "ReadonlyRootfs": True,  # 只读 root，仅 workspace/tmp 可写
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges=true"],
            "NetworkMode": network_mode,
            "Memory": mem_mib * 1024 * 1024,
            "NanoCpus": int(cpu * 1e9),
            "PidsLimit": int(pids_limit),  # 防 fork 炸弹
            # mode=1777：非 root（uid 1000）可写 workspace/tmp（B8-SBX-001：默认 root:root 755 写不了）
            "Tmpfs": {workdir: "rw,size=256m,mode=1777", "/tmp": "rw,size=64m,mode=1777"},
        },
    }


def orphan_container_ids(rows: list[dict[str, Any]], scope: str) -> list[str]:
    """从 docker 容器清单里挑出本 scope 的平台托管容器 Id（纯决策，便于单测）。

    启动时进程内注册表必空 → 本 scope 下所有带管理 label 的容器都是上次进程遗留的孤儿。
    """
    out: list[str] = []
    for r in rows:
        labels = r.get("Labels") or {}
        if labels.get(LABEL_MANAGED) == "1" and labels.get(LABEL_SCOPE, "default") == scope:
            out.append(str(r.get("Id") or ""))
    return [i for i in out if i]


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
        # 线程池 + 同步 subprocess.run（全平台同一份实现，无 platform 分支）：asyncio 子进程 API 隐式依赖
        # 事件循环支持子进程传输——Windows SelectorEventLoop（psycopg3 所需，run.py 强制）不支持，
        # create_subprocess_exec 会抛裸 NotImplementedError（内网实测 skill 执行崩的根因）。
        # subprocess.run(timeout=) 超时自杀并回收子进程；生产 Linux 沙箱走 DockerContainerBackend 不经此路。
        def _run() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(  # noqa: S603 —— fake 后端本就真跑命令（隔离靠临时目录）
                command, cwd=self._root, capture_output=True, timeout=timeout,
                # fake 后端剥除敏感环境：不继承宿主 Cookie/Secret/X-OpenOps-*（真容器天然隔离）
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self._root},
            )

        try:
            cp = await asyncio.to_thread(_run)
        except FileNotFoundError as e:
            return ExecResult(127, "", f"command not found: {e}")
        except subprocess.TimeoutExpired:
            return ExecResult(-1, "", "execution timed out", timed_out=True)
        return ExecResult(cp.returncode or 0, _truncate(cp.stdout, max_output_bytes), _truncate(cp.stderr, max_output_bytes))

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

    def __init__(self, user_id: str, image: str, cpu: float, mem_mib: int, *,
                 network_mode: str = "bridge", pids_limit: int = 256) -> None:
        self.user_id = user_id
        self.image = image
        self._cpu = cpu
        self._mem_mib = mem_mib
        self._network_mode = network_mode
        self._pids_limit = pids_limit
        self.workdir = self.CONTAINER_WORKDIR
        self.container_name = container_name(user_id)
        self._docker = None
        self._container = None

    async def start(self) -> None:
        import aiodocker

        self._docker = aiodocker.Docker()
        cfg = build_container_config(
            user_id=self.user_id, image=self.image, cpu=self._cpu, mem_mib=self._mem_mib,
            network_mode=self._network_mode, pids_limit=self._pids_limit,
        )
        try:
            self._container = await self._docker.containers.create(config=cfg, name=self.container_name)
        except aiodocker.exceptions.DockerError as e:
            if e.status != 409:  # 409=同名冲突：崩溃残留/AutoRemove 未完成的同名尸体，强删后重试一次
                raise
            try:
                stale = await self._docker.containers.get(self.container_name)
                await stale.delete(force=True)
            except aiodocker.exceptions.DockerError:
                pass
            self._container = await self._docker.containers.create(config=cfg, name=self.container_name)
        await self._container.start()

    async def _exec(self, command: list[str], *, timeout: float) -> Any:
        from agentscope.workspace import DockerBackend

        be = DockerBackend(self._container, self.CONTAINER_WORKDIR)
        return await asyncio.wait_for(be.exec_shell(command, cwd=self.CONTAINER_WORKDIR), timeout=timeout)

    async def exec_shell(self, command: list[str], *, timeout: float, max_output_bytes: int) -> ExecResult:
        try:
            r = await self._exec(command, timeout=timeout)
        except asyncio.TimeoutError:
            return ExecResult(-1, "", "execution timed out", timed_out=True)
        out = r.stdout if isinstance(r.stdout, bytes) else str(r.stdout).encode()
        err = r.stderr if isinstance(r.stderr, bytes) else str(r.stderr).encode()
        return ExecResult(int(r.exit_code), _truncate(out, max_output_bytes), _truncate(err, max_output_bytes))

    async def write_file(self, rel_path: str, data: bytes) -> None:
        # B8-SBX-001：只读 rootfs 下 Docker 拒绝 put_archive/get_archive（archive API 整体被禁），
        # 改用 exec_shell 向可写 tmpfs 落盘：base64 内联 + mkdir -p 建嵌套父目录（put_archive 不可靠）。
        import base64

        path = f"{self.CONTAINER_WORKDIR}/{rel_path}"
        b64 = base64.b64encode(data).decode("ascii")
        parent = path.rsplit("/", 1)[0]
        r = await self._exec(["sh", "-lc", f"mkdir -p '{parent}' && printf %s '{b64}' | base64 -d > '{path}'"], timeout=30)
        if int(r.exit_code) != 0:
            err = r.stderr if isinstance(r.stderr, bytes) else str(r.stderr).encode()
            raise RuntimeError(f"write_file 失败：{err.decode('utf-8', 'replace')[:200]}")

    async def read_file(self, rel_path: str) -> bytes:
        import base64

        path = f"{self.CONTAINER_WORKDIR}/{rel_path}"
        r = await self._exec(["sh", "-lc", f"base64 '{path}'"], timeout=30)
        out = r.stdout if isinstance(r.stdout, bytes) else str(r.stdout).encode()
        return base64.b64decode(out)

    async def close(self) -> None:
        try:
            if self._container is not None:
                await self._container.kill()
        except Exception:  # noqa: BLE001 — 尽力回收，AutoRemove 兜底
            pass
        finally:
            if self._docker is not None:
                await self._docker.close()


async def create_backend(user_id: str, *, image: str, cpu: float, mem_mib: int,
                         network_mode: str = "bridge", pids_limit: int = 256) -> SandboxBackend:
    """按 OPENOPS_SANDBOX 选后端；docker 创建失败上抛（executor 转 SANDBOX_CONTAINER_FAILED）。"""
    if os.getenv("OPENOPS_SANDBOX", "fake").lower() == "docker":
        be = DockerContainerBackend(user_id, image, cpu, mem_mib,
                                    network_mode=network_mode, pids_limit=pids_limit)
        await be.start()
        return be
    return FakeBackend(user_id)


async def purge_orphan_containers() -> int:
    """启动时清理上次进程遗留的平台托管容器（仅 docker 档由 main 调用；进程内注册表此刻必空）。

    按 LABEL_MANAGED 过滤 + 本 scope 归属，逐个强删。守护进程不可用等异常由调用方兜住不阻断启动。
    """
    import aiodocker

    docker = aiodocker.Docker()
    try:
        rows = await docker.containers.list(all=True, filters=json.dumps({"label": [f"{LABEL_MANAGED}=1"]}))
        ids = orphan_container_ids([c._container for c in rows], _label_scope())
        for cid in ids:
            try:
                await (await docker.containers.get(cid)).delete(force=True)
            except Exception:  # noqa: BLE001 — 单个清理失败不影响其余
                log.warning("[sandbox] 孤儿容器清理失败 %s", cid[:12])
        return len(ids)
    finally:
        await docker.close()
