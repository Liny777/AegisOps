"""OpenOps V1 Backend —— 只做装配（22 号：main 不写业务）。

启动：uvicorn main:app --app-dir src --reload --port 18081
依赖：docker compose up -d（PG 5432，首次启动自动建表）
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.responses import install_error_handlers
from api.routers import (
    admin,
    agent_teams,
    approvals,
    assets,
    audit,
    identity,
    runs,
    secrets,
    templates,
)
from infra import seed
from infra.db import close_pool, open_pool


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await open_pool()
    await seed.seed()
    # 后台资产对账（28.7）：OPENOPS_RECONCILE_INTERVAL_S > 0 时启用（默认关；登录/refresh 触发已覆盖 V1）
    import asyncio
    import os

    from app import asset_reconcile_service

    interval = float(os.environ.get("OPENOPS_RECONCILE_INTERVAL_S", "0"))
    reconciler = asyncio.create_task(asset_reconcile_service.background_loop(interval)) if interval > 0 else None
    yield
    if reconciler:
        reconciler.cancel()
    await close_pool()


app = FastAPI(title="OpenOps V1 Backend", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)
install_error_handlers(app)


@app.get("/health")
async def health():
    return {"status": "ok"}


for r in (identity, templates, agent_teams, assets, secrets, runs, approvals, audit, admin):
    app.include_router(r.router)
