"""OpenOps V1 Backend —— 只做装配（22 号：main 不写业务）。

启动：uvicorn main:app --app-dir src --reload --port 18082
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
    import logging
    import os

    # 启动横幅（诊断）：一眼确认当前 runtime 后端 + 关键 mock/real 开关，杜绝“以为切了 real、其实还是 mock”
    _rt = os.environ.get("OPENOPS_RUNTIME", "mock").strip().lower()
    _agentscope = ""
    if _rt == "agentscope":
        try:
            import agentscope  # noqa: F401
            _agentscope = f" (agentscope {getattr(agentscope, '__version__', '?')} 已装)"
        except ModuleNotFoundError:
            _agentscope = " (⚠ agentscope 未安装：提交任务会报错)"
    def _cookie_disp(specific: str) -> str:
        """cookie 显示：专属 SET(len) > 共享 shared(len) > unset。含 `;` 引号不当会截断——长度识破。"""
        v = os.environ.get(specific, "")
        if v:
            return f"SET(len={len(v)})"
        shared = os.environ.get("OPENOPS_CONSOLE_COOKIE", "")
        return f"shared(len={len(shared)})" if shared else "unset"
    _tls = ("ca-file" if os.environ.get("OPENOPS_TLS_CA_FILE") else
            "INSECURE" if os.environ.get("OPENOPS_TLS_INSECURE") == "1" else
            "truststore" if "truststore" in __import__("sys").modules else "certifi")
    _banner = (
        f"runtime={_rt}{_agentscope}  model={os.environ.get('OPENOPS_RUNTIME_MODEL', 'glm-5.1')}  "
        f"glm_key={'SET' if os.environ.get('OPENOPS_PLATFORM_GLM_API_KEY') else 'unset'}  "
        f"omodel={os.environ.get('OPENOPS_OMODEL', 'mock')}  "
        f"omodel_cookie={_cookie_disp('OPENOPS_OMODEL_COOKIE')}  "
        f"scope_override={os.environ.get('OPENOPS_SCOPE_OVERRIDE_APPIDS') or 'off'}  "
        f"mcp={os.environ.get('OPENOPS_MCP', 'mock')}  "
        f"mcp_route={os.environ.get('OPENOPS_MCP_ROUTE', 'direct')}  "
        f"mcpregistry={os.environ.get('OPENOPS_MCPREGISTRY', 'mock')}  "
        f"mcp_cookie={_cookie_disp('OPENOPS_MCPREGISTRY_COOKIE')}  "
        f"apptree={os.environ.get('OPENOPS_APPTREE', 'mock')}  "
        f"apptree_cookie={_cookie_disp('OPENOPS_APPTREE_COOKIE')}  "
        f"apptree_user={os.environ.get('OPENOPS_APPTREE_USER_ID') or '(登录态 user_id)'}  tls={_tls}  "
        f"trust_env={'on' if os.environ.get('OPENOPS_HTTP_TRUST_ENV') == '1' else 'off'}  "
        f"skillhub={os.environ.get('OPENOPS_SKILLHUB', 'mock')}  "
        f"skillhub_cookie={_cookie_disp('OPENOPS_SKILLHUB_COOKIE')}  "
        f"sandbox={os.environ.get('OPENOPS_SANDBOX', 'fake')}"
    )
    logging.getLogger("openops.startup").warning("[startup] %s", _banner)
    print(f"[OpenOps][startup] {_banner}", flush=True)

    from app import asset_reconcile_service

    interval = float(os.environ.get("OPENOPS_RECONCILE_INTERVAL_S", "0"))
    reconciler = asyncio.create_task(asset_reconcile_service.background_loop(interval)) if interval > 0 else None
    yield
    if reconciler:
        reconciler.cancel()
    # 先收口 runtime 任务（取消 + 短等审计写完），再关池——避免关闭期 PoolClosed 噪声（B5-BE-001）
    from runtime import task_registry

    pending = [st.orchestrator for st in task_registry._by_run.values()
               if st.orchestrator and not st.orchestrator.done()]
    for t in pending:
        t.cancel()
    if pending:
        try:
            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=3)
        except asyncio.TimeoutError:  # pragma: no cover - 收口超时直接关池
            pass
    # 沙箱容器收口（B8）：回收全部用户容器（fake 删临时目录 / docker kill）
    from sandbox.executor import executor as sandbox_executor

    await sandbox_executor.close_all()
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
