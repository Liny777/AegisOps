# OpenOps V1 Backend

FastAPI 后端骨架，提供 PG 持久化、OpenOps V1 REST API、SSE 事件流、mock runtime 和 mock 外部依赖，用于最小闭环联调。

## 启动

```bash
cd ..
docker compose up -d

cd backend
pip install -e ".[test]"
uvicorn main:app --app-dir src --reload --port 18081
```

后端启动时会连接 `OPENOPS_DATABASE_URL`，执行幂等 seed：demo 用户、白名单、感知快恢模板、平台 Skill/MCP、MCP Tool 标注、沙箱容量配置、平台模型。

## 测试

```bash
pytest -q
```

测试会连接本地 compose PostgreSQL，加载 `sql/openops_v1_core.sql`，每个用例清库后重播 seed。

## 分层约定

- `api`：FastAPI router、鉴权依赖、统一响应与错误。
- `app`：业务服务、事务编排、幂等与归属校验。
- `domain`：DTO、错误码、枚举。
- `infra`：PG repository、外部 mock client、加密。
- `runtime`：mock Runtime Adapter、任务注册表、SSE 事件缓冲。
- `sandbox`：用户 Skill 沙箱执行占位，后续块实现。

当前 MVP 不接真实 AgentScope、真实 oModel、真实 LLM、Docker 沙箱；这些能力通过后续块替换 mock client/runtime。
