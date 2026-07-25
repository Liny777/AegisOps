"""Agent Studio 切片自有路由（原寄生在 api/routers/admin.py 与 api/routers/runs.py）。

**路径逐字不变**（前端 5 个 api 方法与切片测试都按字面路径断言）：
    GET /api/openops/v1/admin/studio/runs?user_id=&page=&page_size=
    GET /api/openops/v1/admin/studio/runs/{run_id}
    GET /api/openops/v1/admin/studio/runs/{run_id}/messages
    GET /api/openops/v1/agent-runs/{run_id}/replay
注意 admin_router 的 prefix 已含 `/studio`，故装饰器路径**不再**带 `/studio`——
两处都改才拼得对，只改一处会变成 `/admin/studio/studio/runs`（症状是 404，容易误判成鉴权问题）。

匹配顺序：`/agent-runs/{run_id}/replay` 与 core runs.router 的 14 条路由零重叠
（state/messages/events/tasks 都是字面后缀，`{run_id}:delete` 的路径转换器不跨 `/`），
故在 main 里 include 的先后无所谓。**但新增带通配后缀的路由前，务必重跑路由清单 diff。**

鉴权用 core 的公开依赖：`Admin`(platform_admin) / `User`(白名单本人)——这是正确的共享，
studio 不自建鉴权。
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from api.deps import Admin, User
from api.responses import ok
from studio import service

admin_router = APIRouter(prefix="/api/openops/v1/admin/studio", tags=["studio"])
user_router = APIRouter(prefix="/api/openops/v1", tags=["studio"])


# ---- 管理员回溯复盘：span 原文按 用户→run→agent 钻取 ----
@admin_router.get("/runs")
async def studio_runs(
    _admin: Admin,
    user_id: str = Query(min_length=1, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """某用户全部 run（**含软删**，管理员复盘不受用户删会话影响）+ span 聚合指标。"""
    return ok(await service.list_user_runs(user_id, page, page_size))


@admin_router.get("/runs/{run_id}")
async def studio_run_detail(run_id: str, _admin: Admin):
    """run 详情：span 按 agent 实例分组（main 卡最前）+ 交接账本 + rollup。"""
    return ok(await service.run_detail(run_id))


@admin_router.get("/runs/{run_id}/messages")
async def studio_run_messages(run_id: str, _admin: Admin):
    """run 对话记录（用户↔助手全文，与用户端 /messages 同投影；含软删 run，无脱敏）。"""
    return ok(await service.run_messages(run_id))


# ---- 用户自查回放（对话界面侧栏「回放」） ----
@user_router.get("/agent-runs/{run_id}/replay")
async def run_replay(run_id: str, user: User):
    """回放（owner-only）：span 按 agent 实例分组 + 主↔子交接 + rollup；LLM 输入不下发。"""
    return ok(await service.run_replay(user, run_id))
