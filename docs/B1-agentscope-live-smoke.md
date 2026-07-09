# B1 · 真 AgentScope 2.0.3 Runtime 全栈冒烟运行手册

目标：把 `OPENOPS_RUNTIME=agentscope` 的**真 AgentScope 2.0.3** runtime 跑进真实后端（FastAPI + PG + SSE），
用 API 走完一次 `准入 → 建实例 → 建 Run → 发 Task → stub RCA 推进 → ASK 审批 → completed → 审计链`，
并与默认 `mock` 后端对比，确认两条后端产出的 `openops.*` 事件序列与审计一致。

> 代码位置：分支 **`feat/workbench-frontend`**，commit `249081f` 起。B1 只用 `agentscope.agent.Agent` +
> stub model + `PermissionContext`，**不**引入 `agentscope.app`（不需要 apscheduler / redis）。
> 本轮模型是 **stub**（脚本化 RCA），真 GLM 在 B2 接入 —— 冒烟只验证 runtime 接缝，不需要任何 LLM API Key。

---

## 0. 前置

- Docker（起 PostgreSQL 16）
- Python 3.11
- `git`、`curl`、`jq`
- 拉到 B1 代码：
  ```bash
  git fetch origin
  git checkout feat/workbench-frontend
  git log --oneline -1   # 应看到 249081f 或更新
  ```

## 1. 起 PostgreSQL

```bash
cd openOps-Dev-New
docker compose up -d
docker compose ps          # postgres healthy；映射 localhost:5432（openops/openops/openops）
```
首次启动自动执行 `backend/sql/openops_v1_core.sql` 建表。若之前跑过想重来：`docker compose down -v && docker compose up -d`。

## 2. 装后端依赖（含 agentscope）

**推荐用独立 venv，避免污染全局环境**：
```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[test,agentscope]"    # 关键：agentscope extra = agentscope==2.0.3
python -c "import agentscope; print('agentscope', agentscope.__version__)"   # 期望 2.0.3
```
> 备选：直接装进现有环境 `pip install -e ".[test,agentscope]"`（会往该环境加 agentscope 及其依赖）。

## 3. 启动后端（选 agentscope 后端）

```bash
# 仍在 backend/，venv 已激活
export OPENOPS_DATABASE_URL="postgresql://openops:openops@localhost:5432/openops"
export OPENOPS_RUNTIME=agentscope        # ★ 选真 AgentScope 后端（默认是 mock）
export OPENOPS_ORCH_DELAY_MS=200         # 可选：加快演示（mock 用；agentscope 步进由 stub 控制）
uvicorn main:app --app-dir src --port 18081
```
启动日志应无异常；后端会幂等 seed（demo 用户 `0026demo01`、白名单、模板、Skill/MCP、标注、模型等）。
另开一个终端做下面的 API 流程。

## 4. 走一遍闭环（curl）

```bash
BASE=http://localhost:18081/api/openops/v1
U=(-H "X-OpenOps-Mock-User: 0026demo01" -H "X-OpenOps-Mock-Name: LinYi" -H "Content-Type: application/json")
crid() { echo "crid_$(date +%s%N)"; }

# 4.1 准入
curl -s "${U[@]}" $BASE/me | jq .data          # whitelisted=true, role=user

# 4.2 取模板
TVID=$(curl -s "${U[@]}" $BASE/templates/available | jq -r '.data[0].template_version_id')
echo "template_version_id=$TVID"

# 4.3 建实例（workspace ws_pay_abc 由 oModel mock 提供，已 ready）
IID=$(curl -s "${U[@]}" -d "{\"client_request_id\":\"$(crid)\",\"template_version_id\":\"$TVID\",\"name\":\"冒烟 AgentTeam\",\"workspace_id\":\"ws_pay_abc\"}" \
  $BASE/agent-teams | jq -r '.data.instance.agent_team_instance_id')
echo "instance=$IID"

# 4.4 建 Run
RID=$(curl -s "${U[@]}" -d "{\"client_request_id\":\"$(crid)\",\"agent_team_instance_id\":\"$IID\"}" \
  $BASE/agent-runs | jq -r '.data.run.agent_run_id')
echo "run=$RID"

# 4.5 发 Task（触发真 AgentScope：巡检→定界→ASK）
curl -s "${U[@]}" -d "{\"client_request_id\":\"$(crid)\",\"input_text\":\"支付延迟突增，帮我定位\"}" \
  $BASE/agent-runs/$RID/tasks | jq .data

# 4.6 轮询 state 直到出现 pending 审批
for i in $(seq 1 30); do
  sleep 0.5
  AID=$(curl -s "${U[@]}" $BASE/agent-runs/$RID/approvals | jq -r '.data[0].approval_request_id // empty')
  [ -n "$AID" ] && break
done
echo "approval=$AID"

# 4.7 批准恢复动作
curl -s "${U[@]}" -d "{\"client_request_id\":\"$(crid)\",\"decision\":\"approved\"}" \
  $BASE/approvals/$AID:decide | jq .data

# 4.8 轮询 state 直到 task completed，打印 RCA 修订号与事件链
for i in $(seq 1 30); do
  sleep 0.5
  ST=$(curl -s "${U[@]}" $BASE/agent-runs/$RID/state)
  [ "$(echo "$ST" | jq -r '.data.active_task.status')" = "completed" ] && break
done
echo "$ST" | jq '{task: .data.active_task.status, rca_revision: .data.rca.revision,
  events: [.data.recent_events[].event_type]}'
```

## 5. 期望结果（agentscope 后端）

- `4.1` `whitelisted=true`。
- `4.5` 之后异步推进；`4.6` 拿到 `approval_request_id`。
- `4.8` `task=completed`、`rca_revision=3`，`events` 事件链（顺序）大致为：
  ```
  agent_run.created → task.started → scope.resolved →
  openops.tool.call.started → openops.tool.call.succeeded → openops.rca.updated →   # 巡检
  openops.tool.call.started → openops.tool.call.succeeded → openops.rca.updated →   # 定界
  openops.approval.required → openops.approval.approved →
  openops.tool.call.succeeded → openops.rca.updated →                               # 恢复执行
  openops.task.completed
  ```
- 拒绝路径（`decision":"rejected"`）：`approval.rejected → rca.updated(未执行) → task.completed`，且**不出现**恢复的 `tool.call.succeeded`。

（可选）SSE 实时看事件：`curl -N "${U[@]}" $BASE/agent-runs/$RID/events/stream`。

## 6. 与 mock 后端对比（parity）

```bash
# Ctrl-C 停后端，改回默认 mock 再起
export OPENOPS_RUNTIME=mock
uvicorn main:app --app-dir src --port 18081
```
重跑第 4 节。**期望**：`events` 事件类型序列与 agentscope 后端**同形**（消息文案可略有差异，事件类型/顺序/审计链一致）。这验证「换 runtime 后端、plumbing 不变」。

## 7. 排障

| 现象 | 排查 |
|---|---|
| 启动即报 `OPENOPS_RUNTIME=agentscope 需要安装 agentscope==2.0.3` | 第 2 步没装 agentscope extra，或没在装了 agentscope 的环境启动 |
| `ModuleNotFoundError: apscheduler` | 说明误用了 `agentscope.app`（app-server 层）。B1 不该走到那里；确认用的是 `feat/workbench-frontend` 的 `runtime/agentscope_runtime.py` |
| `/me` 返回 UNAUTHORIZED | 少了 `X-OpenOps-Mock-User` 头 |
| 建实例报 workspace 未就绪 | `workspace_id` 用 `ws_pay_abc`（oModel mock 里 ready 的那个） |
| 一直等不到 approval | 看后端日志有无异常；确认 `OPENOPS_RUNTIME=agentscope` 已 export 到启动进程；stub 到第 3 步才发 ASK |
| task 卡在 running 不 completed | 审批后 `st.approval_ev` 未触发？确认 `:decide` 返回 200 且 `decision=approved` |

## 8. 反馈内容

跑完请回报：①第 5 节 `events` 实际输出；②`task` 状态与 `rca_revision`；③agentscope vs mock 的事件序列是否同形；④任何异常堆栈。

---

## 9. B2 变体：接真实 GLM（可选，需 API Key）

B2 已把 stub model 换成「有 Key 用真 GLM、无 Key 回退 stub」。要压真实 `glm-5.1`：

```bash
# 在第 3 步启动后端前，额外 export GLM 的 API Key（仅环境变量，绝不落库/日志）
export OPENOPS_PLATFORM_GLM_API_KEY="<你的智谱 GLM API Key>"
export OPENOPS_RUNTIME=agentscope
uvicorn main:app --app-dir src --port 18081
```
然后照第 4 节走一遍。**与 stub 的差别**：
- `events` 里出现 `openops.model.call.started`/`succeeded`，且 `model.call.started` 的 payload `model` 为 **`glm-5.1`**（stub 时是 `stub-rca`）—— 用这个判断真模型是否在驱动。
- `task.completed` / 末条 `rca.updated` 的 `conclusion` 来自 **GLM 真实输出**（stub 时是固定脚本文案）。
- `model.call.succeeded` 带 `input_tokens`/`output_tokens`。
- Key 缺失或错误：应回退 stub（缺失）或 `openops.model.call.failed` + `task.failed`（错误，错误信息已脱敏）。

> ⚠ GLM 是否稳定按预期调用 `query_resource`/`recover_execute` 取决于其 tool-calling 表现；若 GLM 不调工具或流程异常，回报实际 `events` 与结论文本即可，据此调 system prompt / 工具描述。真实 Key 不要贴进任何工单/日志。

请回报 stub 版与（如有 Key）GLM 版的 `events` 与 `conclusion`，以及是否同形。
