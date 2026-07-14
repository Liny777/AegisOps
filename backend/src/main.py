"""OpenOps V1 Backend —— 只做装配（22 号：main 不写业务）。

启动：uvicorn main:app --app-dir src --reload --port 18082
依赖：docker compose up -d（PG 5432，首次启动自动建表）
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

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


def _build_id(info_file: Path | None = None) -> str:
    """发布包 BUILD_INFO 的稳定标识；本地源码运行显示 dev。"""
    path = info_file or (Path(__file__).resolve().parents[2] / "BUILD_INFO")
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("BUILD_ID="):
                return line.split("=", 1)[1].strip()[:96] or "unknown"
    except OSError:
        pass
    return "dev"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await open_pool()
    await seed.seed()
    # P 块启动例程：过期幂等键清理 + 重启孤儿任务收敛（running 快照 → interrupted）
    from app import run_state_service as _rss
    from infra import idempotency as _idem

    await _idem.purge_expired()
    await _rss.converge_orphan_tasks()
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
        f"build={_build_id()}  runtime={_rt}{_agentscope}  "
        f"model={os.environ.get('OPENOPS_RUNTIME_MODEL', 'glm-5.1')}  "
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
        f"sandbox={os.environ.get('OPENOPS_SANDBOX', 'fake')}  "
        f"sandbox_sweep={os.environ.get('OPENOPS_SANDBOX_SWEEP_INTERVAL_S', '60')}s"
    )
    logging.getLogger("openops.startup").warning("[startup] %s", _banner)
    print(f"[OpenOps][startup] {_banner}", flush=True)

    # docker 档启动清理孤儿容器（上次进程遗留：注册表此刻必空，本 scope 带管理 label 的全是孤儿）
    if os.environ.get("OPENOPS_SANDBOX", "fake").lower() == "docker":
        try:
            from sandbox.backends import purge_orphan_containers
            _n = await purge_orphan_containers()
            if _n:
                logging.getLogger("openops.startup").warning("[startup] 清理孤儿沙箱容器 %d 个", _n)
        except Exception as e:  # noqa: BLE001 — 守护进程未起等：不阻断启动，后续建容器自会报 SANDBOX_CONTAINER_FAILED
            logging.getLogger("openops.startup").warning("[startup] 孤儿容器清理跳过：%s", e)

    from app import asset_reconcile_service, sandbox_admin_service

    interval = float(os.environ.get("OPENOPS_RECONCILE_INTERVAL_S", "0"))
    reconciler = asyncio.create_task(asset_reconcile_service.background_loop(interval)) if interval > 0 else None
    sweep_s = float(os.environ.get("OPENOPS_SANDBOX_SWEEP_INTERVAL_S", "60"))
    sweeper = asyncio.create_task(sandbox_admin_service.background_sweep_loop(sweep_s)) if sweep_s > 0 else None
    yield
    if reconciler:
        reconciler.cancel()
    if sweeper:
        sweeper.cancel()
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


class RootPathShim:
    """文根自剥（OPENOPS_ROOT_PATH，如 /openback）：请求路径带前缀就剥掉再路由，
    并把 root_path 写回 scope（307 重定向 / docs URL 生成仍带前缀）。

    这样公司网关**剥不剥前缀都能工作**——域名系统只填 ip+端口（不剥）可直指后端；
    sidecar/内网直连的裸路径不受影响（无前缀分支原样放行）。
    不用 uvicorn --root-path：新版 uvicorn 会把 root_path 拼回请求路径，遇不剥前缀的
    网关变双前缀 404（内网实测）。
    """

    def __init__(self, app, prefix: str):
        self.app = app
        self.prefix = "/" + prefix.strip("/")

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == self.prefix or path.startswith(self.prefix + "/"):
                scope = dict(scope)
                scope["path"] = path[len(self.prefix):] or "/"
                scope["root_path"] = self.prefix
        await self.app(scope, receive, send)


_root_path = os.environ.get("OPENOPS_ROOT_PATH", "").strip().rstrip("/")
if _root_path:
    app.add_middleware(RootPathShim, prefix=_root_path)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)
install_error_handlers(app)


@app.get("/health")
async def health():
    return {"status": "ok"}


for r in (identity, templates, agent_teams, assets, secrets, runs, approvals, audit, admin):
    app.include_router(r.router)
