# OpenOps V1 · Windows 内网本地开发上手 & 外部依赖 mock/real 切换

> 适用工程：`openOps-Dev-New`（分支 `feat/workbench-frontend`）
> 面向场景：把项目挪到内网 Windows 本地开发，外部依赖（IAM / oModel / Skill Hub / MCP Registry）尚未就绪。
> **核心结论**：本地开发**不需要任何真实外部依赖、也不需要 Redis**。整套代码「先 mock/adapter 契约、后换真实接口」（见 `学习/OpenOps/33-OpenOps V1后续开发计划`）。装 PostgreSQL + Python 即可跑通全栈闭环。

## 0. 前置要求

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | 3.11+ | 后端 |
| Node.js | 18+ | 前端（Vite） |
| PostgreSQL | 16 | 二选一：Docker Desktop 起 或 原生安装 |
| Docker Desktop | 可选 | 起 PG 最省事；无 Docker 用原生 PG |
| Redis | ❌ 不需要 | 见 §6；`.env` 里的 Redis 是遗留占位 |

内网注意：若用 Docker，需能拉到 `postgres:16-alpine`（或提前离线导入镜像）。

## 1. 快速开始（三步，PowerShell，工程根 = `openOps-Dev-New`）

### 1.1 起 PostgreSQL
方案 A（Docker Desktop，自动建表）：
```powershell
docker compose up -d      # 唯一服务 postgres:16-alpine；首启自动执行 DDL 建表
```
方案 B（原生 PG，无 Docker）：见 §2.2 手动建表。

### 1.2 起后端
```powershell
cd backend
python -m venv .venv                 # 不要拷 mac 的 .venv，必须在 Windows 重建
.venv\Scripts\Activate.ps1
pip install -e ".[test]"             # 默认 mock 运行时，不装 agentscope / redis
uvicorn main:app --app-dir src --reload --host 0.0.0.0 --port 18082
```
启动时会连 PG 并**幂等灌种子**（demo 用户 / 白名单 / 模板 / 审批好的工具 / 模型 / 沙箱配置）。
验证：浏览器打开 `http://localhost:18082/health` → `{"status":"ok"}`。

### 1.3 起前端
```powershell
cd frontend
npm install
npm run dev                          # http://127.0.0.1:5175 ，/api 代理到 18082
```
侧栏可在 `0026demo01`(普通用户) / `admin`(管理员) 间切角色。

## 2. 建表

### 2.1 DDL 文件
`backend/sql/openops_v1_core.sql` —— **22 张表**（B7 增 `model_asset`+`model_access_grant`），由 Obsidian 19 号建表语句生成。`CREATE TABLE IF NOT EXISTS` 幂等、**无外键、无触发器、无需任何 PG 扩展**（UUID 应用层生成），任意原生 PostgreSQL 直接建。⚠远程/共享库：DDL **不带 schema 前缀、无 search_path 处理**，表落连接默认 schema——共享 PG 给本项目**独立 database 或 schema**；库里若有旧版表，`IF NOT EXISTS` 只补缺表**不改旧表列**，列有变更须清库重建（无迁移脚本）。

### 2.2 建表怎么执行（重要）
应用启动**不会自动建表**。建表只在两处发生：
- **Docker**：`docker-compose.yml` 把 DDL 挂进 `docker-entrypoint-initdb.d`，**PG 数据卷为空的首启**自动执行。
- **pytest**：`tests/conftest.py` 的 `reset_database()` 每次重建。

⚠️ **用原生/远程 PG（不走 Docker）时必须先手动跑一次 DDL**，否则启动时 `seed()` 查不存在的表、后端起不来。用 `psql` 或任意 SQL 客户端（DBeaver/pgAdmin）对目标库执行一次：
```powershell
# 本地库
psql "postgresql://openops:openops@localhost:5432/openops" -f backend\sql\openops_v1_core.sql
# 远程/共享库（内网部署场景）——把 URL 换成远程库，账号需有 CREATE 权限
psql "postgresql://<用户>:<密码>@<远程主机>:5432/<库>" -f backend\sql\openops_v1_core.sql
```
⚠️ 远程**共享** PG（本项目走独立 schema，**表名带 `sre_` 前缀**避免撞名）：建表时要把 **search_path 设到你的 schema**，表才落对地方——GUI 工作台里选中该 schema 再执行 DDL 全文，或 psql 用带 options 的连接串：
```powershell
psql "host=<主机> port=5432 dbname=<库> user=<用户> password=<密码> options=-csearch_path=<schema>" -f backend\sql\openops_v1_core.sql
```
这个 `<schema>` **必须和后端 `OPENOPS_PG_SCHEMA` 一致**。`IF NOT EXISTS` 只补缺表**不改旧表列**（列变更须清库重建）。**`pytest` 会 `TRUNCATE` 全表——测试库务必另开，别指向部署/共享库。**

### 2.3 连接配置（离散 `OPENOPS_PG_*` 首选）
后端连库两种写法：**设了 `OPENOPS_PG_HOST` 就走离散变量**（keyword 格式，密码/特殊字符**免 URL 转义**）；否则回退整串 `OPENOPS_DATABASE_URL`。

| 变量 | 说明 |
|---|---|
| `OPENOPS_PG_HOST` / `PORT`(默认 5432) / `DB` / `USER` / `PASSWORD` | 连接四要素 + 密码（特殊字符无需转义） |
| `OPENOPS_PG_SCHEMA` | 表落此 schema（经 `search_path`）；留空=账号默认。**须与建表时的 schema 一致**；表名带 `sre_` 前缀 |
| `OPENOPS_PG_SSLMODE` | 公司库要 SSL 时设 `require` |
| `OPENOPS_DATABASE_URL` | 或整串回退（未设 `OPENOPS_PG_HOST` 时才用） |

⚠️ 后端**不加载 .env**、`infra/db.py` 在 **import 时**即读——先设、再起 uvicorn（推荐用启动脚本，见 §2.5，已带 `OPENOPS_PG_*` 模板）。

### 2.4 重置数据库（改了 seed / 想重来）
```powershell
docker compose down -v      # 删数据卷
docker compose up -d        # 重新首启 → 重建表 + 重新 seed
```
（远程库无 Docker 卷可删：改用 `DROP SCHEMA ... CASCADE` 或删库重建后再跑 §2.2 的 DDL。）

### 2.5 配置方式：后端不读 .env，用启动脚本（重要）
后端**没有 dotenv/pydantic-settings，不加载任何 `.env` 文件**——把 `.env.example` 改名成 `.env` 放哪都**不会生效**（`.env.example` 仅是变量名参考清单）。配置 = 把变量设成**真进程环境变量**，两种方式：

- **临时**：起后端前 `$env:OPENOPS_PG_HOST="..."`（及 `PG_PORT/PG_DB/PG_USER/PG_PASSWORD/PG_SCHEMA`；或整串 `$env:OPENOPS_DATABASE_URL="..."`）。`infra/db.py` 在 import 时即读，须先设再起 uvicorn。
- **推荐（免每次手敲）**：用启动脚本。仓库已带模板：
  - 后端 `backend/run-backend.ps1.example` → **复制为 `backend/run-backend.ps1`，填入真连接串**，然后 `cd backend; .\run-backend.ps1`（设 env + 起 uvicorn 18082）。⚠含明文密码，**已在 `.gitignore`，勿提交**。
  - 前端 `frontend/run-frontend.ps1`（无密钥、可直接跑）→ `cd frontend; .\run-frontend.ps1`（起 vite dev 5175，代理 `/api`→18082）。
  - 若 PowerShell 拦截脚本执行：本会话放行 `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`。

## 3. 本地鉴权（无真 IAM）

当前无真实 IAM，用请求头模拟登录态；角色 / 白名单事实以 PG 为准。

| 头 | 必填 | 说明 |
|---|---|---|
| `X-OpenOps-Mock-User` | 是 | 用户 ID；缺失返回 401 |
| `X-OpenOps-Mock-Name` | 否 | 显示名，中文需 URI 编码 |

Seed 预置且已白名单的账号：`0026demo01`（林一，普通 user）、`admin`（李四，platform_admin）。

直接调 API 示例：
```powershell
curl -H "X-OpenOps-Mock-User: admin" http://localhost:18082/api/openops/v1/me
```
前端自动注入这两个头（`frontend/src/lib/api/client.ts`），侧栏切角色（`appState.tsx` 的 `switchRole`）。真实 W3/IAM introspect 替换属 **B9**。

## 4. 环境变量清单

| 变量 | 默认 | 何时需要 | 说明 |
|---|---|---|---|
| `OPENOPS_DATABASE_URL` | `postgresql://openops:openops@localhost:5432/openops` | 非默认连接 | 单一 URL |
| `OPENOPS_RUNTIME` | `mock` | 接真 agentscope | `mock` \| `agentscope` |
| `OPENOPS_OMODEL` | `mock` | 联调 oModel | `mock` \| `real` |
| `OPENOPS_OMODEL_BASE_URL` | 空 | `OPENOPS_OMODEL=real` | oModel 测试环境地址；空则 fail-closed |
| `OPENOPS_OMODEL_TIMEOUT_S` | `8` | 可选 | oModel 超时秒数 |
| `OPENOPS_PLATFORM_GLM_API_KEY` | 空 | 接真 GLM 模型（B2） | 只从环境读，禁入 PG/日志 |
| `OPENOPS_LLM_PROBE` | `mock` | 联调用户自定义 LLM | `mock`（启发式，不打网）\| `real`（建 llm-config 时发真 `chat/completions` 验 tool-calling） |
| `OPENOPS_SANDBOX` | `fake` | 接真 Docker 沙箱（B8） | `fake`（tempdir+subprocess，默认）\| `docker`（需 `.[sandbox]`+Docker Desktop）；本地调试 fake 够用 |
| `OPENOPS_MCP` / `OPENOPS_MCPREGISTRY` / `OPENOPS_SKILLHUB` | `mock` | 联调对应外部服务（C3） | `=real` 时须配同名 `*_BASE_URL`，否则 fail-loud（不静默降级） |
| `OPENOPS_ENCRYPTION_KEY` | 空→dev 派生 | 用户 Secret 加密（C2，`infra/crypto.py`） | **生产 Secret 主 key**（Fernet，`Fernet.generate_key()` 生成）；`_OLD` 逗号分隔旧 key 供轮换；**丢了密文解不开** |
| `OPENOPS_SECRET_KEY` | `change-me` | 未配 `OPENOPS_ENCRYPTION_KEY` 时的 dev 回退源 | 本地默认可用（从它派生确定性 Fernet key，warn 提示）；**生产必配 `OPENOPS_ENCRYPTION_KEY`** |
| `VITE_OPENOPS_API_MODE`（前端） | `real` | 纯 UI 演示时 | `real`=真打后端；`mock`=用 `mockData` 纯前端跑，**不需要后端** |
| `VITE_OPENOPS_API_BASE`（前端） | `/api`（走 vite 代理） | 前端指向非默认后端 | 后端 base 地址 |
| ~~`OPENOPS_REDIS_URL`~~ | — | ❌ 从不读取 | `.env.example` 遗留占位，无视 |
| ~~`OPENOPS_ENV` / `OPENOPS_MOCK_EXTERNALS`~~ | — | ❌ 从不读取 | 遗留占位 |

> `.env.example` 在**仓库根**（非 `backend/`）。后端不自动加载它，仅作变量清单参考。

## 5. 外部依赖 mock/real 切换矩阵

**总原则**：每个外部依赖都在 adapter 后面，默认 mock，联调时按需 opt-in。你按 mock 契约把自己这侧做完、测完，等真接口来只是**换 env + 对齐 HTTP 形状**，callers 签名不变。

| 依赖 | 当前状态 | 切 real 怎么做 | 属哪块 |
|---|---|---|---|
| **oModel** | `omodel_client.py` 可切换；`omodel_mock.py` 默认；`omodel_real.py` **已按 29.5 对齐**（resolve 用端点4「列出工作空间关联项目」；⚠workspace 级发现集非 per-user 授权，见 `backend/docs/EXTERNAL-INTEGRATION.md` 安全口径） | 配 `OPENOPS_OMODEL=real` + `OPENOPS_OMODEL_BASE_URL=<host root>` | B3 |
| **Skill Hub** | mock 默认；**已备 real 变体**（`OPENOPS_SKILLHUB=real`，C3） | 配 `OPENOPS_SKILLHUB_BASE_URL`（未配 fail-loud）；真下载按 **ZIP 原始字节** sha256 校验 `X-Checksum-SHA256`（29.3 §2.5，C1-CHK-001 已对齐） | B6/C1 |
| **MCP Registry** | mock 默认；**已备 real 变体**（`OPENOPS_MCPREGISTRY=real`，C3） | 配 `OPENOPS_MCPREGISTRY_BASE_URL`（`POST /mcps/proxy`，未配 fail-loud） | B6/C3 |
| **平台 HTTP MCP** | mock 默认；**已备 real 变体**（`OPENOPS_MCP=real`，C3） | 配 `OPENOPS_MCP_BASE_URL`（Tool Gateway header 透传，28.2） | B4/C3 |
| **用户自定义 LLM 探测** | mock 启发式；**已备 real**（`OPENOPS_LLM_PROBE=real`，C2/C3） | real 建 llm-config 时发真 `chat/completions` 验 tool-calling | — |
| **IAM** | `X-OpenOps-Mock-User` 头 | 真 W3/IAM introspect | B9 |
| **Runtime** | `OPENOPS_RUNTIME=mock`（脚本化编排 `orchestrator.py`） | `=agentscope`（真 Agent，须 `pip install -e ".[test,agentscope]"`；仍**不需 Redis**） | B1 |
| **平台模型** | mock，不调真 LLM | 配 `OPENOPS_PLATFORM_GLM_API_KEY` + Model Gateway | B2 |
| **沙箱** | `OPENOPS_SANDBOX=fake`（tempdir+subprocess） | `=docker`（须 `.[sandbox]`+Docker Desktop；⚠Windows 走命名管道，见 §9.7） | B8 |

### 5.1 oModel（real 已对齐 29.5）
`omodel_client.py` 是分发器（`_impl()` 模式，其他依赖同款）。mock 验证全链路：初始化建 workspace、Scope resolve、空范围 fail-closed、平台 MCP 按 `effective_appids` 过滤（`31` EXT-001/002、SCOPE-*）。**real 已按 29.5 对齐**（`omodel_real`：resolve=端点4、get/list/create=WorkspaceMetadata 映射），切 `OPENOPS_OMODEL=real`+`OPENOPS_OMODEL_BASE_URL` 即联真；**剩 per-user 过滤 + Cookie 鉴权待 umodel P0**（29.6，见 EXTERNAL-INTEGRATION.md 安全口径）。

## 6. 为什么没有 Redis

- 主依赖（`pyproject.toml`）无 redis 无 agentscope；`docker-compose.yml` 只有 postgres。
- 会话 / 运行态：持久事实存 PG（`agent_run` / `approval_request` / `audit_event` / `scope_snapshot`）+ 进程内存（`task_registry` dict、`events` `deque(maxlen=500)`），**完全不经 Redis**（重启丢进程态，靠 `GET /agent-runs/{run_id}/state` + audit_event 重放恢复）。
- `.env.example` 的 `OPENOPS_REDIS_URL` 全库零引用，纯遗留占位——这就是「看到 redis」的来源。
- agentscope 2.0.3：redis 是 optional extra（`[storage]`），核心 `agentscope.agent.Agent` 层不牵；**实际 B1 runtime 用 Agent 直连、不走 `create_app(storage=RedisStorage)`，不需要 Redis**（见 `docs/B1-agentscope-live-smoke.md`；注意：这与 33 号计划里"B1 用 RedisStorage"的措辞不一致，以实现为准）。
- 结论：**本地不装 Redis**。只有将来接 agentscope app-server 层（`create_app` / 内置 AG-UI）才需要，那时 Windows 上用 Docker `redis:7-alpine` / Memurai / WSL2。

## 7. 联调 Skill Hub / MCP Registry（real 已按 29.3 对齐）

两者的 real 变体**已内联在 `skill_hub_client.py` / `mcp_registry_client.py`**（C3 加开关、本轮按 29.3 对齐 URL/信封/字段），本地开发默认 mock、**不需要 cookie**。联真：

1. 切 `OPENOPS_SKILLHUB=real` + `OPENOPS_SKILLHUB_BASE_URL=<host root>`；`OPENOPS_MCPREGISTRY=real` + `OPENOPS_MCPREGISTRY_BASE_URL=<host root>`（未配 fail-loud）。client 自动拼 `/obsv/agent/management/...` 前缀。
2. 29.3 list/download/proxy 读端点**无需鉴权**（29.4 §三.4）；未来写端点（upload 等）才需透传用户 IAM Cookie。
3. ⚠️ Cookie/凭据是敏感项：**只放环境变量，禁入代码 / 日志 / PG / 前端**（每块敏感信息搜索）。契约细节 + 联调待办见 `backend/docs/EXTERNAL-INTEGRATION.md`。

## 8. 种子数据 & 模板 / 工具审批

`seed.py` 启动时幂等灌入（以模板 key `sensai_fast_recovery` 存在为跳过标志）：

| 内容 | 明细 |
|---|---|
| Demo 用户 + 白名单 | `0026demo01`（user）、`admin`（platform_admin） |
| 平台模板 | `sensai_fast_recovery`（感知快恢 Agent：main + inspect/diagnose/recover 三子 agent） |
| 平台 Skill | `巡检 inspection`（落库，运行时暂未接） |
| 平台 MCP + 工具目录 | `query_resource`、`recover_execute` |
| **工具标注（已审批）** | `status='allowed'`；`recover_execute` 需 ASK、其余免审批；均 `scope_mode=required`、`appid_arg_path=$.appid` |
| 沙箱容量 | 26 容器 / 每用户 2 running task / idle 15min / CPU 0.5 / 内存 2048MiB |
| 平台模型 | Qwen3.5 / GLM-5.1 / GPT-4.1 / DeepSeek-V3（active）、Claude 3.5（disabled） |

> ⚠️ **关键**：运行时工具可用性由 `mcp_tool_annotation`（`status='allowed'`）+ `agentscope_runtime._build_toolkit` 里的硬编码函数决定，**不是模板 content_json**（模板 role→skill→mcp 映射运行时暂未消费，属 B4/B7）。所以"提前在数据库审批好工具" = 那几条 annotation，seed 已做。

### 8.1 加一个新平台工具并让它真被调用（三处齐改）
1. `infra/external/mcp_registry_client.py` 的 `_TOOLS` 加该 tool（真实环境接 Registry `tools/list`）。
2. 写一条 `mcp_tool_annotation` 且 `status='allowed'`（seed 里，或走写接口 `PUT /api/openops/v1/admin/mcp-tools/{tool_catalog_id}/annotation`）。
3. `runtime/agentscope_runtime.py` 的 `_build_toolkit` 加对应 async Python 函数实现。

### 8.2 换一版模板 / 工具集
改 `seed.py` 的 `TEMPLATE_CONTENT` 与标注播种。因幂等以 `template_key` 跳过，改后需**清库重播**（§2.4）或**换新 `template_key`**。

## 9. Windows 常见坑

1. **别拷 mac 的 `backend/.venv` 和 `frontend/node_modules`**（都含平台原生二进制：venv 是 mac 解释器、node_modules 有 esbuild/rollup 的 darwin 产物）。Windows 必须各自 `python -m venv .venv` 与 `npm install` 重建（内网无外网则走内部 PyPI/npm 镜像或预下载 wheel/tarball）。
2. 激活命令：`.venv\Scripts\Activate.ps1`（PowerShell），不是 `source .venv/bin/activate`。
3. **应用不自动建表**：原生 PG 要先手动跑 DDL（§2.2）。
4. **后端不读 .env**：env 要手动 `$env:` 注入，否则全走默认（默认够本地用）。
5. `.env.example`（在**仓库根**）里 `OPENOPS_REDIS_URL` / `OPENOPS_ENV` / `OPENOPS_MOCK_EXTERNALS` 当前无代码读取、可无视；但同文件的 `OPENOPS_SECRET_KEY` 与 `OPENOPS_DATABASE_URL` 是**真被读取**的。
6. 端口：后端 18082、前端 5175、PG 5432；前端 `vite.config.ts` 把 `/api` 代理到 18082。
7. **沙箱 fake 后端在 Windows 上不可用**：`executor.py` 用 `sh -lc "… python3 run.py"`（`:163/:185`），Windows 无 `sh`→**优雅返回 exit 127（不 crash 服务）**，且 mock 包入口是 `python3`。核心 RCA 演示流**不碰沙箱**，故不影响初期调试；只有 agent 真调 `run_skill`/`run_bash`（需 `OPENOPS_RUNTIME=agentscope`+模型 或 `OPENOPS_DEMO_SANDBOX_STEP=1`）才触发。要真跑沙箱：用 WSL2/Linux，或 `OPENOPS_SANDBOX=docker`（Windows Docker Desktop 走命名管道 `npipe:////./pipe/docker_engine`，本代码默认连 unix socket、未处理 `DOCKER_HOST`，需自行适配）。
8. **别强制 `uvicorn --loop selector`**：默认 `ProactorEventLoop` 支持子进程；换 selector loop 会让 `create_subprocess_exec` 抛 `NotImplementedError`。`uvicorn[standard]` 的 uvloop 是 Unix-only，Windows 自动回退 Proactor，无需干预。
9. **远程/共享 PG**：给本项目独立 database 或 schema（DDL 无 schema 前缀、无 search_path 处理）；`pytest` 会反复 `TRUNCATE` 全表，**测试库别指向部署/共享库**。

## 10. 验证闭环 & 自检

本地最小闭环（全 mock）：**白名单准入 → 初始化 Agent（mock workspace）→ 工作台发任务 → mock 编排推进 RCA/ASK → 批准 → task completed → 审计回放**。

每次改动固定自检：
```powershell
cd backend ; pytest -q                 # 会连同一个 PG 反复重建/清表，别指向生产库
cd ..\frontend ; npm run build ; npx tsc -b
```
发布 / 提交前做敏感信息搜索，确认 API Key / Cookie / Authorization / Bearer / Secret 不进前端、SSE、审计、日志、测试快照。

## 11. 关键文件速查

- 入口 / 依赖：`backend/src/main.py`、`backend/pyproject.toml`
- DB 连接：`backend/src/infra/db.py`（`OPENOPS_DATABASE_URL`）
- 建表 DDL：`backend/sql/openops_v1_core.sql`
- 种子：`backend/src/infra/seed.py`
- 鉴权：`backend/src/api/deps.py`
- PG 编排：`docker-compose.yml`
- oModel 分发 / mock / real：`backend/src/infra/external/omodel_client.py`、`omodel_mock.py`、`omodel_real.py`
- Skill Hub / MCP Registry mock：`backend/src/infra/external/skill_hub_client.py`、`mcp_registry_client.py`
- 运行时选择 / 编排：`backend/src/app/runtime_adapter.py`、`backend/src/runtime/orchestrator.py`、`backend/src/runtime/agentscope_runtime.py`
- 工具标注写接口：`backend/src/api/routers/admin.py`
- 前端代理 / 头注入 / 切角色：`frontend/vite.config.ts`、`frontend/src/lib/api/client.ts`、`frontend/src/lib/appState.tsx`
- 相关文档：`docs/ROADMAP.md`、`docs/B1-agentscope-live-smoke.md`、`docs/API.md`；计划 `学习/OpenOps/33-OpenOps V1后续开发计划`
