# OpenOps V1 新工程

这是 OpenOps V1 的全新前后端工程骨架，用于承载 SRE Agent 平台的 V1 原型与后续联调。

## 目录

- `frontend`：Vite + React + TypeScript 前端原型，覆盖初始化、对话工作台、实例设置和管理台。
- `backend`：FastAPI 后端骨架，提供 OpenOps V1 REST API、mock runtime、mock 外部依赖和 DDL。
- `docs`：接口、联调和环境说明。

## 快速启动

先启动 PostgreSQL：

```bash
docker compose up -d
```

后端：

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
uvicorn main:app --app-dir src --reload --host 0.0.0.0 --port 18082
```

首次启动时 PostgreSQL 会通过 `backend/sql/openops_v1_core.sql` 建表；后端 lifespan 会幂等写入 demo 用户、白名单、感知快恢模板、平台 MCP 标注、沙箱容量配置和平台模型。

前端：

```bash
cd frontend
npm install
npm run dev
```

默认前端使用 real facade，经 Vite 代理访问后端 `/api/openops/v1/...`。需要纯 UI 演示时可临时切到 mock：

```bash
VITE_OPENOPS_API_MODE=mock
VITE_OPENOPS_API_BASE=http://localhost:18082
```

## 验证

```bash
cd backend
pytest -q

cd ../frontend
npm run build
```

最小闭环：白名单用户进入初始化向导，创建 AgentTeam 后进入工作台，发送任务后通过 SSE 看到 `scope.resolved`、巡检/定界、RCA 更新和 ASK 卡，批准后任务完成；审计可在管理台或 run 审计页回放。

## Demo 用户

- 普通用户：`0026demo01`
- 平台管理员：`admin`

前端侧栏角色切换会修改 `X-OpenOps-Mock-User` 请求头；角色与白名单事实仍以 PG seed 数据为准。

## 设计边界

- 新工程不复制旧 `openOps-Dev` 的复杂 patch 体系。
- V1 第一阶段使用 mock runtime，真实 AgentScope 2.0.3 运行时通过 `runtime` 适配层替换。
- PostgreSQL 是平台配置与企业审计事实源。
- AgentScope RedisStorage 只在真实 runtime 接入阶段使用，不修改 AgentScope 框架与 Redis StorageBase。
- Secret 明文不得进入 prompt、日志、审计、事件流或沙箱默认环境。
