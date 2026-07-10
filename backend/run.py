"""跨平台启动入口。

Windows 默认 `ProactorEventLoop`，而 psycopg3 的**异步连接池只能用 `SelectorEventLoop`**，
否则报 `Psycopg cannot use the 'ProactorEventLoop' to run in async mode`。事件循环策略必须在
uvicorn 创建 loop **之前**设好——`uvicorn main:app` 会在自己的 asyncio.run 内导入 app（太晚），
所以用本入口而非直接跑 uvicorn CLI。Linux/macOS/WSL 默认就是 selector 兼容 loop，本入口无副作用。

副作用（可接受）：Windows 上 SelectorEventLoop 不支持子进程——但容器沙箱 run_skill/run_bash
在 Windows 本就不可用（无 sh），且只在 agent 循环触发，mock 调试不碰，故此取舍正确。

起法：`python run.py`（端口默认 18082，可 `OPENOPS_PORT` 覆盖）。
"""
from __future__ import annotations

import asyncio
import os
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 等价于 uvicorn 的 --app-dir src：把 src 加进 import 路径，使 main:app 可导入
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))


if __name__ == "__main__":
    import uvicorn

    # 不用 --reload/--workers（否则 uvicorn 会改回 ProactorEventLoopPolicy 做子进程）
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("OPENOPS_PORT", "18082")))
