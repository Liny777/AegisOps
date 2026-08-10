"""7x24 告警接管垂直切片 —— core 唯一允许 import 的入口（facade）。

告警接管 = 轮询拉取告警平台增量 → 规则匹配 → 附着式聚合成 incident（DB 即队列）
→ dispatcher 并发闸消费 → 以实例 owner 身份自动建诊断 run（entry_source='alert'）。
设计定稿见 docs/plans/alert-takeover-7x24.md 与 Obsidian 39 号设计文档。

铁律与 studio 切片一致（tests/studio/test_facade_isolation.py 的同款约束）：
① core → alerts 只准 `import alerts` + 调本文件导出的名字；切片内部结构随时可改。
② 本文件顶层零切片子模块 import——router/service/dispatcher 全部函数内惰性 import，
   facade 不背 fastapi/httpx。

alerts → core 方向只用公开 API：`infra.db.{q_all,q_one,exec1,row_json,jsonb}`、
`infra.idempotency`、`infra.external.alert_platform_client`、
`infra.external.alert_inet_contract`（内网 29.11/29.10 契约映射，kafka_source 消费侧用）、
`infra.repositories.runs` 的公开函数、`app.run_state_service.{create_run,start_task}`、
`domain.errors`、`api.deps.{User,Admin}`、`api.responses.ok`。不碰任何下划线私有名。
"""
from __future__ import annotations

from typing import Any

__all__ = ["routers", "start", "stop", "status_label", "ensure_defaults", "granted_user_ids"]


def routers() -> list[Any]:
    """切片自有 FastAPI 路由，main 逐个 `app.include_router(r)`。

    两个 prefix：`/api/openops/v1/alerts`（User）与 `/api/openops/v1/admin/alerts`（Admin）。
    新增端点只改 alerts/router.py，core 一行不动。
    """
    from alerts.router import admin_router, user_router

    return [user_router, admin_router]


async def ensure_defaults() -> None:
    """补种 runtime_config `alert` 域缺失键（只补缺失，保护管理员改过的值）。

    dev 档顺带给演示用户开通告警接管白名单（2026-08-09 算力保护 fail-closed：
    名单空=全关；生产不种，管理员在管理台按需开通）。
    """
    import os

    from alerts import service

    await service.ensure_config_defaults()
    if os.environ.get("OPENOPS_BUILD", "dev").strip().lower() == "dev":
        from alerts import repository as repo

        await repo.set_user_grant("0026demo01", True, "dev-seed")


async def granted_user_ids() -> set[str]:
    """告警接管白名单用户集（管理台用户表「告警接管」列用；core 只准经本 facade 取）。"""
    from alerts import repository as repo

    return {str(r["user_id"]) for r in await repo.list_user_grants()}


async def start() -> Any | None:
    """startup：收敛重启孤儿 incident + 起 poller/dispatcher 两个后台循环。

    返回不透明句柄（None = 未启用），core 只负责传回 stop()/status_label()。
    `OPENOPS_ALERT_PULL_INTERVAL_S=0`（默认）时仅收敛不起循环——是否启用属部署形态。
    **永不抛**：告警子系统缺配不得拖垮服务启动。
    """
    import logging

    try:
        await ensure_defaults()
        from alerts import dispatcher

        return await dispatcher.start_background()
    except Exception as e:  # noqa: BLE001 —— 启动降级：留日志不阻断
        logging.getLogger("openops.startup").warning("[startup] 告警接管子系统未启用：%s", e)
        return None


async def stop(handle: Any | None) -> None:
    """shutdown 收口；须在 `infra.db.close_pool()` 之前调用（循环落库走同一连接池）。

    handle=None（循环未启用）也要走：admin :dispatch 手动派发的 worker 同样需要收口，
    否则关池后残余 worker 报 PoolClosed 噪声。
    """
    from alerts import dispatcher

    await dispatcher.stop_background(handle)


def status_label(handle: Any | None) -> str:
    """启动横幅片段（`alert_loops=on/off`）。"""
    return "on" if handle is not None else "off"
