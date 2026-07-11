"""跨平台启动入口（显式掌控事件循环）。

Windows 默认 `ProactorEventLoop`，而 psycopg3 的**异步连接池只能跑在 `SelectorEventLoop`** 上，
否则报 `Psycopg cannot use the 'ProactorEventLoop' to run in async mode`。

只设全局 event loop policy 不够可靠：`uvicorn.run()` / `Server.run()` 内部会自己
`asyncio.run(...)` 新建 loop，某些 uvicorn/Python 组合下并未真正落到我们设的 policy 上
（实测 Windows 仍拿到 Proactor）。故这里**显式创建 `SelectorEventLoop` 并在其上 `await
server.serve()`**——绕过一切 policy 猜测，loop 类型 100% 可控。

副作用（可接受）：Windows 上 SelectorEventLoop 不支持子进程——但容器沙箱 run_skill/run_bash
在 Windows 本就不可用（无 sh）、且只在 agent 循环触发，mock 调试不碰，故此取舍正确。
Linux/macOS/WSL 默认就是 selector 兼容 loop，走 `asyncio.run` 即可。

起法：`python run.py`（端口默认 18082，可 `OPENOPS_PORT` 覆盖）。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 等价于 uvicorn 的 --app-dir src：把 src 加进 import 路径，使 main:app 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# TLS：装了 truststore 就用【操作系统证书库】验证 https（和 curl/浏览器一致）——内网自签/公司 CA
# 不在 Python certifi 里，默认会 CERTIFICATE_VERIFY_FAILED。装法：pip install truststore（3.10+）。
try:
    import truststore

    truststore.inject_into_ssl()
    print("[OpenOps] TLS: truststore 已注入（用系统证书库验证 https）", flush=True)
except ModuleNotFoundError:
    pass


async def _serve() -> None:
    import uvicorn

    loop = asyncio.get_running_loop()
    print(f"[OpenOps] event loop: {type(loop).__name__}")  # 启动即打印，一眼确认是 Selector

    config = uvicorn.Config(
        app="main:app",
        host="0.0.0.0",
        port=int(os.environ.get("OPENOPS_PORT", "18082")),
        reload=False,  # 开 reload/workers 会让 uvicorn 改回 ProactorEventLoop 去起子进程
        workers=1,
        log_level="info",
    )
    # 用 server.serve()（协程），不用 server.run()/uvicorn.run()——后两者会自建 loop、绕开本入口
    await uvicorn.Server(config).serve()


def main() -> None:
    if sys.platform == "win32":
        # 显式建 SelectorEventLoop 跑 serve()。手动建 loop（而非 asyncio.run 的 loop_factory=，
        # 那个需 py3.12+）以兼容 3.10 / 3.11。
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_serve())
        finally:
            asyncio.set_event_loop(None)
            loop.close()
    else:
        asyncio.run(_serve())


if __name__ == "__main__":
    main()
