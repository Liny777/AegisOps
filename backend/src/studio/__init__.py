"""Agent Studio 垂直切片 —— core 唯一允许 import 的入口（facade）。

Studio = 管理员回溯复盘（/admin/studio/*）+ 用户自查回放（/agent-runs/{id}/replay）。
它把 AgentScope 的 OTel span 按用户/run 归属后落 `sre_agent_studio_span`，再按
用户→run→agent 重新组织给管理员看。运行原理见 Obsidian《28.12 AgentScope Agent引擎集成》§8。

两条铁律（改动本文件前必读；由 tests/studio/test_facade_isolation.py 守）
=========================================================================
① **core → studio 只准 `import studio` + 调本文件导出的名字。**
   core 任何模块不得出现 `from studio.xxx import ...`——切片内部的文件名与结构由
   studio owner 随时改，只有本文件的签名是对 core 的承诺。

② **本文件顶层只 import `studio.context`（纯 stdlib：os + contextvars），其余子模块
   一律函数内惰性 import。** 理由是硬的，不是洁癖：
   - `studio.middleware` 顶层 import agentscope、`studio.tracing` 顶层 import
     opentelemetry.sdk（二者随 `[agentscope]` 可选组安装）。而 `runtime.agentscope_runtime`
     顶层 `import studio` —— facade 一变重，**mock 档（pytest 默认、未装 agentscope）
     `import runtime.agentscope_runtime` 直接 ModuleNotFoundError，全测试红**。
   - `studio.service` → `app.agui_service` → `app.run_state_service` → `app.runtime_adapter`
     →（当前是函数内惰性）`runtime.agentscope_runtime`。facade 顶层拉 `.service`
     就把这条链焊成真环。

studio → core 的方向是健康的，只用公开 API：`infra.db.{q_all,q_one,exec1,row_json}`、
`infra.repositories.{runs,delegations,agent_session_states}`、`app.agui_service.project_transcript`、
`domain.errors`、`api.deps.{User,Admin}`、`api.responses.ok`。不碰任何下划线私有名。
"""
from __future__ import annotations

from typing import Any

# 唯一允许的顶层 import：context 只依赖 os + contextvars（见铁律②）
from studio.context import (
    reset_studio_task_context as reset_task_context,
    set_studio_task_context as set_task_context,
    studio_enabled as enabled,
)

__all__ = [
    "enabled",
    "set_task_context",
    "reset_task_context",
    "agent_middlewares",
    "start",
    "stop",
    "status_label",
    "routers",
]

# ── runtime 接缝 ──────────────────────────────────────────────────────────────
# set_task_context(user_id: str, run_id: str, task_id: str, role: str) -> token
#     把本 task 身份写进 contextvar，供 tracing.OpenOpsSpanProcessor.on_start 归属 span。
#     返回**不透明 token**，core 只负责原样传回 reset_task_context，不得解构。
#     ⚠ 四个参数恒为 str：facade **绝不** import runtime.task_registry.TaskState——
#       那就是 core → studio → core 的环。role = "main" 或子 Agent 的 agent_key。
# reset_task_context(token) -> None   在 finally 里回退（与 set 对称）。


def agent_middlewares() -> list[Any]:
    """`Agent(middlewares=...)` 用的捕获中间件；每次返回**新实例**。

    取代原 `runtime.agentscope_runtime._studio_middlewares()`（subagent_dispatch 曾跨模块
    调这个私有名）。不做单例——同一 middleware 对象被多个 Agent 共享会破坏 agentscope 的
    per-agent 行为（实测破坏 AgentState 会话连续性，test_p3）。

    开关关 / agentscope 未装 / 版本漂移 → 返回 []，绝不阻断 run。
    """
    try:
        from studio.middleware import studio_middlewares

        return studio_middlewares()
    except Exception:  # noqa: BLE001 —— 依赖缺失/版本漂移：降级为不捕获
        return []


# ── lifespan 接缝 ─────────────────────────────────────────────────────────────
async def start(runtime_backend: str) -> Any | None:
    """startup：清过期 span → 装 OTel SpanProcessor → 起 drain task。

    返回**不透明句柄**（None = 未启用或降级），core 只负责传回 stop()/status_label()。
    仅 `runtime_backend == "agentscope"` 且开关开时真正启用——其余档位不产生 span，
    装了也是空转（见 tracing.install_and_start 的 provider 语义）。

    **永不抛**：可观测性组件缺失不得拖垮服务启动。
    """
    import logging

    if runtime_backend != "agentscope" or not enabled():
        return None
    try:
        from studio import repository, tracing

        await repository.purge_expired()  # 启动即清一次过期 span
        return tracing.install_and_start()
    except Exception as e:  # noqa: BLE001
        logging.getLogger("openops.startup").warning("[startup] agent studio 捕获未启用：%s", e)
        return None


async def stop(handle: Any | None) -> None:
    """shutdown 收口。

    ⚠ 调用点必须在 `infra.db.close_pool()` **之前** —— drain 落库走同一个连接池。
    """
    if handle is not None:
        handle.cancel()


def status_label(handle: Any | None) -> str:
    """启动横幅片段（`studio=on/off`）；措辞归 studio，core 只负责拼进 banner。"""
    return "on" if handle is not None else "off"


# ── API 接缝 ──────────────────────────────────────────────────────────────────
def routers() -> list[Any]:
    """切片自有的 FastAPI 路由，main 逐个 `app.include_router(r)`。

    两个 prefix：`/api/openops/v1/admin/studio`（Admin 依赖）与 `/api/openops/v1`
    （User 依赖的 `/agent-runs/{run_id}/replay`）。惰性 import——facade 不背 fastapi
    （见铁律②）。新增端点只改 studio/router.py，core 一行不动。
    """
    from studio.router import admin_router, user_router

    return [admin_router, user_router]
