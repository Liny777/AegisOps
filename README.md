# OpenOps V1 新工程

这是 OpenOps V1 的全新前后端工程骨架，用于承载 SRE Agent 平台的 V1 原型与后续联调。

## 目录

- `frontend`：Vite + React + TypeScript 前端原型，覆盖初始化、对话工作台、实例设置和管理台。
- `backend`：FastAPI 后端骨架，提供 OpenOps V1 REST API、mock runtime、mock 外部依赖和 DDL。
- `docs`：接口、联调和环境说明。

## 快速启动

后端：

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
uvicorn openops_backend_new.main:app --reload --host 0.0.0.0 --port 18081
```

前端：

```bash
cd frontend
npm install
npm run dev
```

默认前端使用 mock facade，可不依赖后端直接演示。切到真实后端时设置：

```bash
VITE_OPENOPS_API_MODE=real
VITE_OPENOPS_API_BASE=http://localhost:18081
```

## 设计边界

- 新工程不复制旧 `openOps-Dev` 的复杂 patch 体系。
- V1 第一阶段使用 mock runtime，真实 AgentScope 2.0.3 运行时通过 `runtime` 适配层替换。
- PostgreSQL 是平台配置与企业审计事实源。
- AgentScope RedisStorage 只在真实 runtime 接入阶段使用，不修改 AgentScope 框架与 Redis StorageBase。
- Secret 明文不得进入 prompt、日志、审计、事件流或沙箱默认环境。
