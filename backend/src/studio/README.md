# Agent Studio 切片（垂直切片 · 独立维护）

管理员回溯复盘（`/admin/studio/*`）+ 用户自查回放（`/agent-runs/{id}/replay`）。
把 AgentScope 的 OTel span 按用户/run 归属后落 `sre_agent_studio_span`，再按
**用户 → run → agent** 重新组织给管理员看；用户只能看自己的、且看不到 LLM 输入。

运行原理详见 Obsidian《28.12 运行主链路-AgentScope Agent引擎集成》。

## 切片包含什么（你拥有这些目录）

| 路径 | 内容 |
|---|---|
| `backend/src/studio/` | facade + context / tracing / middleware / repository / service / router |
| `backend/tests/studio/` | 切片全部测试（含 3 条护栏，见下） |
| `backend/sql/slices/studio_span.sql` | 切片自有 DDL（**不在** core.sql 里） |
| `frontend/src/studio/` | entry / api / types / mockData + 5 个组件 |

## 两条铁律

**① core → studio 只准 `import studio`（后端）/ `import from "studio/entry"`（前端）。**
core 不认识切片内部结构；文件怎么拆、叫什么名字由你决定。
后端由 `tests/studio/test_facade_isolation.py::test_core_imports_studio_only_through_facade` 守。

**② facade（`src/studio/__init__.py`）顶层只 import `.context`（纯 stdlib），其余一律函数内惰性 import。**
理由是硬的：`middleware` 顶层 import agentscope、`tracing` 顶层 import opentelemetry.sdk
（随 `[agentscope]` 可选组装），而 `runtime.agentscope_runtime` 顶层 `import studio`——
facade 一变重，**mock 档（pytest 默认、未装 agentscope）全测试红**，且报错指向 runtime 而非 studio。
另外 `service → app.agui_service → … → runtime.agentscope_runtime` 只差一步成环。
由 `test_facade_isolation.py::test_facade_import_stays_dependency_free`（子进程断言）守。

前端同理：`src/studio/entry.tsx` **只准 `lazy()` + 常量**，顶层 import 任何组件都会把切片
打进主 chunk，毁掉路由级 code splitting。验证：`npm run build` 后 dist/assets 里
`ReplayPage-*.js` / `AgentStudioPage-*.js` 必须仍是独立 chunk。

## core 对切片的全部接缝（就这些，改动请知会）

后端 3 个文件：
- `src/main.py`：`import studio` + `await studio.start(_rt)` + `studio.status_label()` + `await studio.stop()` + `studio.routers()`
- `src/runtime/agentscope_runtime.py`：`studio.set_task_context(...)` / `studio.agent_middlewares()` / `studio.reset_task_context(...)`
- `src/runtime/subagent_dispatch.py`：同上三处（子 Agent）

前端 3 个文件：`App.tsx`（`{studioRoutes}`）、`layout/Sidebar.tsx`（`STUDIO_*_NAV` + `studioActiveKey`）、
`admin/AdminConsole.tsx`（`STUDIO_ADMIN_PAGE`）。

facade 签名（后端）：
```python
studio.enabled() -> bool
studio.set_task_context(user_id, run_id, task_id, role) -> token   # 四参恒为 str，不传 TaskState（防环）
studio.reset_task_context(token) -> None
studio.agent_middlewares() -> list                                  # 每次返回新实例，不做单例
await studio.start(runtime_backend) -> handle | None                # 永不抛
await studio.stop(handle) -> None                                   # 必须在 close_pool() 之前
studio.status_label(handle) -> "on" | "off"
studio.routers() -> list[APIRouter]
```

## 拆不掉的耦合（改这些要两边一起动）

1. **子 Agent session_id 字符串契约**（最危险）——生产方 `runtime.subagent_dispatch.sub_session_id`（core），
   消费方 `studio/service.py::_match_handover`。断了是**静默失效**（交接内容空着、其余全对、无日志）。
   已用 `sub_session_id` / `parse_sub_session_id` 配对函数 + `tests/studio/test_session_id_contract.py`
   把它变成响亮失败——**看到这条测试红，说明 core 改了格式，你要同步改**。
2. **core 表只读**：`sre_agent_run`（8 列）、`sre_agent_delegation`、`sre_agent_session_state`。
   core 改这些列名/语义，切片必红。切片**绝不写 core 表**。
3. **`app.agui_service.project_transcript`**：保证「管理员看到的 = 用户看到的」。
   已有测试断言 `own == got`，这条有网。
4. **前端 `api.getAdminTable("users")`**：`AgentStudioPage` 的用户选择表复用 core 的通用表格接口。
5. **core 的 `/agent-runs/{id}/messages`**：`studioApi.replayRunMessages` 直接打它（后端 service 已共用
   `project_transcript`，再造一个 studio 端点是重复实现）。
6. `api.deps.{User,Admin}`、`api.responses.ok`、`lib/api/client` —— **这些是正确的共享**，不是债。
7. `sre_agent_studio_span` 的备份/容量/保留期归平台运维（组织问题，非代码问题）。

## 本地开发

```bash
# 后端：切片测试（单独跑，验 pythonpath 与包解析）
cd backend && OPENOPS_DATABASE_URL=<独立测试库> python3 -m pytest tests/studio -q
# 全量回归（每步都要跑；装了 agentscope 的解释器也跑一次，middleware/tracing 才真执行）
cd backend && python3 -m pytest tests/ -q

# 打包完整性（唯一能逮到 pyproject include 漏项的手段——其余门禁全逮不到）
cd backend && python3 -m pip wheel --no-deps -w /tmp/w . && unzip -l /tmp/w/*.whl | grep studio/

# 前端
cd frontend && npx tsc --noEmit && npm run build
VITE_OPENOPS_API_MODE=mock npx vite --port 5181 --strictPort   # ?as=admin / ?as=user 两档都点
```

## 新增/修改时的检查清单

- 加端点 → 只改 `studio/router.py`（core 零改动）；路径变了记得同步 `frontend/src/studio/api.ts` 与测试。
- 加 span 字段 → `tracing._span_to_row` → `repository.insert_span` → `sql/slices/studio_span.sql`
  （**加列不加表**，表数基线 27 不变）→ 需要的话再动 `stats_by_run_ids` / 前端 `types.ts`。
- **加表** → 表数基线要改 3 处：`tests/test_ddl.py` 的 27、`scripts/release_check.sh` 的 27，
  并确认 `docker-compose.yml` 与 `docs/deploy-intranet.md` 的 apply 链覆盖新文件。
  （`tests/conftest.py` 的表清单是自动发现的，无需改。）
- 改 env 默认值 → `config/openops.*.env.example` 三份 + 本 README。
