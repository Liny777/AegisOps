"""agentscope 官方文件工具（Read/Grep/…）的容器后端适配器（B8·补3+）。

把 agentscope `tool.BackendBase` 抽象接到本项目的沙箱执行器上——官方 Read/Grep 因此在**用户
容器内**读文件/搜内容（而非默认 `LocalBackend` 的宿主机，那会越过容器隔离读服务器文件）。经本
项目的 `SandboxBackend`（FakeBackend 真 subprocess / DockerContainerBackend 真容器）执行，
fake 与 docker 两种后端都通、可测。

只实现 3 个抽象方法（exec_shell/read_file/write_file）；file_exists/is_dir/list_dir/stat_mtime
由 BackendBase 基于 exec_shell 默认派生（test -e / find / stat）。命令由官方工具机器构造、Read/Grep
只读，直经容器执行器（与 run_skill 的 entrypoint 同理不过 command_guard）；容器缺失经 run_id+cfg
自愈重建（_get_or_revive）。
"""
from __future__ import annotations

import base64
import shlex
from typing import Any, Sequence

from agentscope.tool import BackendBase, ExecResult

from sandbox.backends import b64_chunks
from sandbox.executor import _OUTPUT_MAX_BYTES
from sandbox.executor import executor as sandbox_executor

_DEFAULT_TIMEOUT_S = 30.0


class SandboxToolBackend(BackendBase):
    """把 agentscope 文件工具的 FS 调用路由进指定用户的沙箱容器。

    绑定 (user_id, run_id, cfg)：每次调用现取容器后端（缺失自愈重建），故一个实例可跨工具复用，
    也不持有可能过期的容器引用。
    """

    def __init__(self, user_id: str, run_id: str | None, cfg: dict[str, Any] | None) -> None:
        self._user_id = user_id
        self._run_id = run_id
        self._cfg = cfg

    async def _backend(self) -> Any:
        c = await sandbox_executor._get_or_revive(self._user_id, self._run_id, self._cfg)
        return c.backend

    async def exec_shell(self, command: Sequence[str], *, cwd: str | None = None,
                         timeout: float | None = None) -> ExecResult:
        pb = await self._backend()
        argv = list(command)
        if cwd:  # 项目后端 exec_shell 无 cwd 概念：包一层 cd 再 exec 原 argv（$0 占位）
            argv = ["sh", "-lc", f'cd {shlex.quote(cwd)} && exec "$@"', "sh", *argv]
        r = await pb.exec_shell(argv, timeout=timeout or _DEFAULT_TIMEOUT_S,
                                max_output_bytes=_OUTPUT_MAX_BYTES)
        out = r.stdout if isinstance(r.stdout, bytes) else str(r.stdout).encode("utf-8")
        err = r.stderr if isinstance(r.stderr, bytes) else str(r.stderr).encode("utf-8")
        return ExecResult(exit_code=int(r.exit_code), stdout=out, stderr=err)

    # read/write 经 python3 做 base64 中转（官方工具传的是容器内绝对路径；项目后端原生 read_file 走相对
    # 路径不适配）。用 python3 而非 base64 CLI：沙箱本就是 python 镜像，且规避 BSD/GNU base64 的差异
    # （BSD 不吃位置文件名、解码是 -D 不是 -d，fake 模式在 macOS 上会挂）。path/data 走 argv，免引号地雷。
    _READ_PY = "import base64,sys;sys.stdout.write(base64.b64encode(open(sys.argv[1],'rb').read()).decode())"
    # 写模式由 argv[3] 传入（首片 wb 覆盖 / 后续 ab 追加）：整包 b64 塞单个 argv 会撞 execve 的
    # MAX_ARG_STRLEN（128KiB），>96KB 的文件必炸 E2BIG，故与容器后端同样分片（见 b64_chunks）。
    _WRITE_PY = ("import base64,os,sys;p=sys.argv[1];d=os.path.dirname(p);"
                 "os.makedirs(d,exist_ok=True) if d else None;"
                 "open(p,sys.argv[3]).write(base64.b64decode(sys.argv[2]))")

    async def read_file(self, path: str) -> bytes:
        r = await self.exec_shell(["python3", "-c", self._READ_PY, path])
        if r.exit_code != 0:
            raise FileNotFoundError(path)
        return base64.b64decode(r.stdout)

    async def write_file(self, path: str, data: bytes) -> None:
        chunks = b64_chunks(data)
        for i, chunk in enumerate(chunks):
            r = await self.exec_shell(["python3", "-c", self._WRITE_PY, path, chunk, "wb" if i == 0 else "ab"])
            if r.exit_code != 0:
                raise OSError(f"write_file 失败（{path}，{len(data)}B，第 {i + 1}/{len(chunks)} 片，"
                              f"exit={r.exit_code}）：{r.stderr.decode('utf-8', 'replace')[:200]}")
