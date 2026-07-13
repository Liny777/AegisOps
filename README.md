# OpenOps V1 新工程

OpenOps V1 的前后端工程，承载 SRE Agent 平台的 V1 原型与联调。已交付 **B0–B8 + C1–C3**：真 AgentScope runtime、Model Gateway/GLM、Scope Service、Tool Gateway、AG-UI 工作台、资产对账与配置热更新、管理台（模型 ACL + 模板版本写闭环）、Docker 沙箱执行面、Secret 加密与 LLM egress 防护、外部依赖接真开关。默认全部走 **mock/fake**（pytest 与 demo 不依赖真环境），按 env 开关逐项切真。

## 目录

- `frontend`：Vite + React + TypeScript 前端，覆盖初始化向导、AG-UI 对话工作台、实例设置和管理台。
- `backend`：FastAPI 后端，提供 OpenOps V1 REST API、AG-UI/SSE 事件流、runtime（mock / 真 AgentScope）、Tool/Model Gateway、Docker 沙箱执行面、mock 外部依赖与 DDL。
- `docs`：接口/联调/环境说明；各块冒烟报告在 `docs/test/`。
- `backend/docs`：外部依赖接真开关清单（`EXTERNAL-INTEGRATION.md`）。

## 已交付能力

- **真 AgentScope 2.0.3 runtime**（`OPENOPS_RUNTIME=agentscope`；mock 为默认 test-double）。
- **Model Gateway + GLM**：平台模型元数据 + env Key 构建 `OpenAIChatModel`，无 Key 回退 stub；`model.call.*` 事件。
- **Scope Service + oModel adapter**：`effective_appids` 解析、TTL、fail-closed。
- **Tool Gateway**：平台 HTTP MCP 受控调用（标注 / Scope / Secret / `X-OpenOps-*` header / 审计），用户 MCP 隔离。
- **AG-UI 工作台**（headless）：`POST /agent-runs/{id}/agui` 标准事件流 + `@ag-ui/client` HttpAgent，`VITE_OPENOPS_TRANSPORT` 可回退 SSE。
- **资产对账 + 配置热更新 + 设置页写闭环**：不可变配置版本链、`source=openops` 对账、运行时标注热更新。
- **管理台**：模型资产白名单（按人授权，三处 fail-closed）+ 模板版本写闭环（草稿/发布不可变/禁用）+ 模板工具集 enforcement + 沙箱容器页。
- **Docker 沙箱执行面**：per-user 容器会话期常驻 + 容量准入；`run_skill`（checksum + entrypoint + 超时/截断 + output.json）；容器内受控 Bash 四层裁决 + 审计；`OPENOPS_SANDBOX=fake(默认)|docker`。
- **安全**：Secret **Fernet 真加密**落库（仅调用边界瞬时解密）+ 用户 LLM **egress SSRF** 防护 + 用户 LLM 无静默回退。
- **外部依赖接真开关**：平台 MCP / MCP Registry / Skill Hub / 平台 GLM / oModel 均有 real 变体 + env 开关（默认 mock）。

进度详见 [docs/ROADMAP.md](docs/ROADMAP.md)（Obsidian《33 后续开发计划》/《34 差距盘点》为权威计划与差距盘点）；各块冒烟报告见 `docs/test/`。

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
pip install -e ".[test]"                 # 默认：mock runtime + fake 沙箱，pytest 不依赖真环境
# 可选：真 AgentScope runtime → pip install -e ".[test,agentscope]"
# 可选：真 Docker 沙箱后端    → pip install -e ".[test,sandbox]"
uvicorn main:app --app-dir src --reload --host 0.0.0.0 --port 18082
```

首次启动时 PostgreSQL 会通过 `backend/sql/openops_v1_core.sql` 建表；后端 lifespan 会幂等写入 demo 用户、白名单、感知快恢模板、平台 Skill/MCP Tool 标注、沙箱容量配置和平台模型资产。

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

## 运行开关

默认全部 mock/fake，pytest 与 demo 不依赖真环境；按需切真：

| 开关 | 取值（默认在前） | 作用 |
|---|---|---|
| `OPENOPS_RUNTIME` | `mock` \| `agentscope` | mock 编排器 / 真 AgentScope runtime（需 `.[agentscope]`） |
| `OPENOPS_OMODEL` | `mock` \| `real` | Scope resolve / workspace CRUD 接 oModel（BASE_URL 必须固定域名，禁止 `{host}`；部署显式配当前企业 tenant） |
| `OPENOPS_TRUSTED_PROXY_CIDRS` | CIDR 列表 | IAM/oModel 会话绑 IP 的可信反代网段；拒绝过宽网段，直连请求忽略 XFF |
| `OPENOPS_SANDBOX` | `fake` \| `docker` | 进程内 tempdir / 真 Docker 容器（需 `.[sandbox]` + 本机 Docker） |
| `OPENOPS_LLM_PROBE` | `mock` \| `real` | 用户自定义 LLM 能力探测（real 发真 `chat/completions`） |
| `OPENOPS_PLATFORM_GLM_API_KEY` | 无 → stub | 配置后真 GLM 驱动 RCA（需 `OPENOPS_RUNTIME=agentscope`） |
| `OPENOPS_MCP` / `OPENOPS_MCPREGISTRY` / `OPENOPS_SKILLHUB` | `mock` \| `real` | 外部依赖接真 + 对应 `*_BASE_URL` |
| `OPENOPS_ENCRYPTION_KEY`（+`_OLD`） | 缺省派生 dev key | Secret Fernet 加密 key（生产必配；`_OLD` 逗号分隔用于轮换） |
| `OPENOPS_LLM_EGRESS_BLOCK_PRIVATE` / `OPENOPS_LLM_EGRESS_DENY` | 关 / 空 | 收紧用户 LLM egress（默认放行 RFC1918 内网网关） |
| `OPENOPS_SKILL_TIMEOUT_S` / `OPENOPS_SKILL_OUTPUT_MAX_BYTES` | `600` / `2MiB` | Skill/命令执行限额 |

接真开关总表、逐项 HTTP 契约与联调顺序见 [backend/docs/EXTERNAL-INTEGRATION.md](backend/docs/EXTERNAL-INTEGRATION.md)。

## 验证

```bash
cd backend
pytest -q                                          # 默认 mock/fake，全绿

cd ../frontend
npm run build
```

Docker 沙箱实机回归护栏（需 `.[sandbox]` + 本机 Docker）：

```bash
cd backend
OPENOPS_SANDBOX_DOCKER_TEST=1 pytest -k docker_real
```

各块冒烟报告在 `docs/test/`（`B1..B8-*`、`C1-C3-*`）。

最小闭环：白名单用户进入初始化向导，创建 AgentTeam 后进入工作台，发送任务后经 AG-UI/SSE 看到 `scope.resolved`、巡检/定界、RCA 更新和 ASK 卡，批准后任务完成；审计可在管理台或 run 审计页回放。

## Demo 用户

- 普通用户：`0026demo01`
- 平台管理员：`admin`

前端侧栏角色切换会修改 `X-OpenOps-Mock-User` 请求头；角色与白名单事实仍以 PG seed 数据为准。真 W3/IAM Cookie introspect 属 B9，尚未替换该 mock 头。

## 设计边界

- 新工程不复制旧 `openOps-Dev` 的复杂 patch 体系。
- 真 AgentScope 2.0.3 runtime 经 `runtime` 适配层接入，`OPENOPS_RUNTIME` 切换；mock 为默认 test-double（pytest/demo 不依赖真模型与真网）。
- PostgreSQL 是平台配置与企业审计事实源；DDL 无 FK/触发器/函数，seed 幂等。
- AgentScope RedisStorage / DockerBackend 只在真实 runtime / 沙箱接入阶段使用，不修改 AgentScope 框架。
- Secret 以 **Fernet 加密落库**、仅在 Model/Tool Gateway 调用边界瞬时解密；明文不得进入 prompt、日志、审计、事件流或沙箱默认环境（SEC-001）。用户自定义 LLM 的 `base_url` 过 egress SSRF 校验。
