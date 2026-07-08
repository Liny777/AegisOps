# OpenOps V1 Backend

FastAPI 后端骨架，先提供 mock runtime 和 mock 外部依赖，用于前端联调与架构验证。

```bash
pip install -e ".[test]"
uvicorn openops_backend_new.main:app --reload --port 18081
pytest -q
```

## 分层约定

- `api`：后续放 FastAPI router。
- `app`：后续放业务服务、事务编排、幂等与归属校验。
- `domain`：后续放 DTO、错误码、枚举。
- `infra`：后续放 PG repository、外部 client、加密。
- `runtime`：后续放 AgentScope Runtime Adapter、Tool Gateway 和事件投影。
- `sandbox`：后续放用户 Skill 沙箱执行。

当前 MVP 为了快速联调，将 mock store 和 endpoint 收敛在少量文件内，接口路径和 DTO 先稳定。
