# 2026-07-12 全天 23 提交 · 全栈回归与探针测试报告

- **日期**：2026-07-13
- **被测对象**：`Liny777/AegisOps` 2026-07-12 当天全部 23 个提交（`b0af478` 10:45 → `b168a4b` 23:12），合计 65 文件 +6869/−636
- **测试基线**：快照 `b168a4b`（当天最后一笔），独立 git worktree 上执行，不含 07-13 的 S1/B9/S3 后续提交
- **环境**：macOS / Python 3.11.7（backend/.venv）/ pytest 9.1.1 / agentscope 2.0.3 / PG 16（docker `openops-v1-pg`）/ 沙箱 FakeBackend / omodel·IAM·SkillHub 走 mock（内网真环境项如实标注「环境受限未测」）
- **方法**：Obsidian OpenOps 全部设计文档（00–37/97–99）提炼验收基线 → 23 提交分 6 块逐 diff 分析 → 快照全量 pytest + 前端 tsc/vite 构建 + SQL 迁移一致性静态校验 → 18 个定向探针补盲区 → 每个疑似缺陷由独立「反驳者」代理做对抗核实（源码级因果链验证，宁可漏报不误报）

---

## 一、结果总览

| 项目 | 结果 | 说明 |
|---|---|---|
| 后端全量回归（169 例） | ✅ **161 passed / 0 failed / 8 skipped** | 185.7s；skip 均为环境门控（PG schema 专项、external real 变体等） |
| 前端 TypeScript 编译 | ✅ 通过 | `tsc -b` 仅覆盖 src/；sidecar `server/copilot-runtime.ts`、`server/identity.ts` 单独 `tsc --noEmit --strict` 亦零错误 |
| 前端生产构建 | ✅ 通过 | `vite build` 约 10s，仅 chunk>500kB 提示性警告 |
| SQL 迁移 ↔ core.sql 一致性 | ✅ 一致 | persistence/delegation 两迁移（已收编删除）从 git 历史取回逐行比对 = 完全一致；repository SQL 列名/类型全部对上；conftest TRUNCATE 面 26 表无遗漏 |
| 定向探针（18 项） | ⚠️ 8 pass / 10 fail / 2 skip | 10 个 fail 经对抗核实：**8 项确认为产品缺陷，2 项被推翻** |
| Obsidian 19 号文档 | ⚠️ 4 处漂移 | 见 §五 文档回写项 |

**一句话结论**：07-12 的 P/D/E 三块与 UI/会话/杂项修复在**正向主链路上全部达标**（当天自带的回归用例全绿、DDL 收编无失真、前端可编译可构建），但探针在盲区挖出 **2 个高危 + 5 个中危 + 1 个低危**已确认缺陷，其中 E1 审批桥残留路径（审批门旁路）和 D 块子 task_id 跨批碰撞两条直接威胁本次提交自己声称建立的安全边界，建议立案修复并补回归。

---

## 二、被测提交分块

| 块 | 提交 | 内容 |
|---|---|---|
| **P 持久化三件套** | 9a7a68f, d75e37f | 幂等键/任务影子快照/AgentState 会话状态全落 PG，启动孤儿收敛，migrate 收编 core.sql |
| **D sub_agents 编排** | dd66d59, e11aa5b, b168a4b | gather 派发+delegation 账本+两层预算+per-agent 工具隔离；动态 MCP 按画像白名单裁剪；37 号迁移手册 |
| **E 审批桥+治理** | 0488ce8, 54fbb65, 67714e7 | 子 Agent 审批桥（decide 按子 task_id 路由、审批等待不吃超时预算）、max_iters/tool_result_limit 治理、活动栏 agent_key 全事件分组+状态徽标、编辑器治理旋钮 |
| **B CopilotChat 接管** | 004544e, e8bb040, fc6add3, 760fc71, 159225e | 官方 UI + Node sidecar + 断流取消桥；工具卡 args/result 修复；SCOPE_RESOLVE_FAILED 原因必达 |
| **A 会话与导航** | 3f2b81d, 70e2320, 0f8378d, d4999a2, 6495960, 5123d40 | 会话软删/自动起名/重命名、按 Agent 过滤、模型快速失败、导航与设置 IA 重排 |
| **杂项修复** | 6147fad, 73ff064, 288752f, b0af478 | 沙箱执行边界自愈重建、fake 沙箱线程池化、reconcile 补 MCP 入库+skill 失败三通道、Skill 链路真化（skill_key 统一） |

---

## 三、分块测试结论

### P 块 —— 运行态持久化三件套 ✅（附 2 个中危缺陷）
当天自带回归（`test_run_task.py` 幂等重放跨重启、孤儿收敛、会话续忆三组 + `test_ddl.py` 表数守卫 + conftest TRUNCATE 面扩充）全绿。探针另确认：purge_expired 启动清理 ✅、孤儿 cancel 越权 403 ✅、幽灵任务占并发名额且 converge 后释放 ✅、emit 快照 approval_id 刷新 ✅、P3 坏 state 容错回退 ✅。
**缺陷**：DEF-3（并发幂等窗口重复建 run）、DEF-4（终态快照 cancel 语义谎报+无审计），见 §四。

### D 块 —— sub_agents 多 Agent 编排 ✅（附 1 个高危 + 1 个低危缺陷）
当天回归（预算拒发三型、agentscope 端到端派发+账本 completed+审计、per-agent 白名单裁剪）全绿。探针确认预算-账本联动、未知角色拒发均按设计。
**缺陷**：DEF-2（跨批同角色子 task_id 碰撞破坏审批路由）、DEF-8（>5 任务静默截断）。「崩溃遗留 delegation 行永久占预算」判定被**推翻**（预算按 leader_task_id 隔离，崩溃的 leader 不会再派发，遗留行只是数据卫生问题）。

### E 块 —— 审批桥 + 治理 + 活动栏分组 ✅（附 1 个高危 + 1 个中危缺陷）
当天回归（审批桥 approve/reject 双路径、审批行 task_id 带子后缀、子审批不误置主 st、治理参数真透传、审批等待不计预算）全绿。探针确认模板 tool_result_limit 校验边界（1000..200000）正确拒收。
**缺陷**：DEF-1（迟到 decide 经 get_by_run fallback 污染主任务握手——本次提交声称修掉 bug 的残留路径）、DEF-6（approval.approved/rejected 事件缺 agent_key，子审批决策在活动栏归错组）。

### B 块 —— CopilotChat UI 接管 ✅ 编译级（运行时面留有盲区）
tsc/vite/sidecar 编译全通过；后端 `test_agui.py` 锁住了 TOOL_CALL_ARGS 事件配对与 TOOL_CALL_RESULT 取 result_summary 两个当天修复。**断流取消桥（GeneratorExit→fire-and-forget cancel）零自动化测试**，且 fire-and-forget task 无强引用可能被 GC、非事件循环上下文 close() 会抛 RuntimeError、断流感知最长滞后 15s 三个风险点均无用例——这是本机可测而未测的最大盲区（见 §六）。

### A 块 —— 会话删除/命名/导航 ✅（附 1 个中危缺陷）
当天回归（自动起名前 30 字、rename trim+审计、他人 403、软删+列表排除+state 404）全绿。「rename schema 120/service 截 60」判定被**推翻**（两处注释证明是有意归一化契约，响应/SSE/审计均带最终权威值）。
**缺陷**：DEF-5（软删后 run.deleted 审计对 owner 和 admin 的 /audit/runs 入口永久 404）。

### 杂项 —— 沙箱自愈/线程池/reconcile/Skill 真化 ✅（附 1 个中危缺陷）
当天回归（close_all 模拟重启后自愈+归属登记、subprocess.run 超时旗标、动态工具不被未标注 catalog 行拦截）全绿。探针确认 FakeBackend 退出码/截断/敏感 env 剥除、自愈容量满 fail-closed、并发自愈幂等均按设计。
**缺陷**：DEF-7（bash 工具路径自愈实为死代码——6147fad 的回归测试直调 executor 绕过了工具入口守卫，守卫属漏删）。

---

## 四、已确认缺陷清单（对抗核实后 8 项）

行号基于快照 `b168a4b`。每项均经独立反驳者代理逐环验证因果链后 CONFIRMED。

| # | 严重度 | 位置 | 缺陷 | 修复方向 |
|---|---|---|---|---|
| DEF-1 | **高** | `backend/src/app/run_state_service.py:340` | **迟到 decide 污染主任务握手（审批门旁路）**：`get_by_task(task_id) or get_by_run(run_id)` fallback——子任务取消/终态后其 pending 审批卡片仍在列表中，此时 decide 会命中主 st 并置 approval_result+approval_ev；主任务若恰有并发 ASK 在等，写/恢复类工具可在用户未批准该操作的情况下被放行。正是 0488ce8 声称修掉的「子审批错置主任务」的残留路径 | 删除 get_by_run fallback（或校验 `st.task_id == appr["task_id"]` 才置握手信号）；取消级联时把子任务 pending 审批一并收口 cancelled |
| DEF-2 | **高** | `backend/src/runtime/subagent_dispatch.py:42` | **跨批同角色子 task_id 必碰撞**：task_id=`{leader}.{agent_key}{seq}`，seq 是批内 enumerate 下标。register 覆盖写+finally 无条件 unregister→第一批收尾把第二批幸存子摘掉，其审批 decide 走 DEF-1 的 fallback 错路由。dispatch_subagents 未设 `is_concurrency_safe=False`，agentscope 默认并发执行工具，模型一轮双派发即触发；纯顺序场景下第一批遗留 pending 审批行同样碰撞 | task_id 改全局唯一后缀（delegation_id 短哈希或 leader 累计计数）；子 AgentState session_id 同步加后缀 |
| DEF-3 | 中 | `backend/src/infra/idempotency.py:50` + `run_state_service.py:43-69` | **并发幂等窗口重复建 run**：先执行业务再 INSERT 幂等键，gather 并发同 client_request_id 建出 2 个 run、各自返回不同 run_id，输家 run 成孤儿行（幂等键行因唯一索引只有 1 条）。串行重放正常 | 先 INSERT 占位抢锁（ON CONFLICT DO NOTHING，冲突读回赢家）再执行业务，成功后 UPDATE result_json |
| DEF-4 | 中 | `backend/src/app/run_state_service.py:226-237` | **孤儿 cancel 打在终态快照上语义谎报**：快照 completed+内存 miss 时 :cancel 返回 `{status:"cancelled"}` 200 但 DB 仍 completed；且整个快照回退分支（含真正改状态的 mark_status）不写任何审计事件，违背「审计为事实源」单点约束 | 终态行返回真实 task_status+already_terminal 标记；改状态分支补 task.cancelled 审计 |
| DEF-5 | 中 | `backend/src/app/audit_trace_service.py:12-14` | **软删会话审计不可见**：run_events 先经 `runs.get_run`（过滤 deleted_at）→软删后 owner 与 platform_admin 查 `/audit/runs/{rid}` 均 404，`run.deleted` 事件写了但常规入口永久不可见（仅 /audit/traces/{trace_id} 后门可见，而 trace_id 删除后无处可取）——删除动作实际无痕 | run_events 改用不过滤 deleted_at 的存在性+归属校验（get_run 加 include_deleted 参数），至少对 admin 放行 |
| DEF-6 | 中 | `backend/src/app/run_state_service.py:326-338` | **审批决策事件缺 agent_key**：decide_approval 直发 audit.insert_event/events.publish 不走 emit() 单点，approval.approved/rejected payload 无 agent_key；前端 groupNodes 对 !agentKey 节点归主「时间线」组——子 Agent 的审批决策必然归错组，破坏 E3「全事件注入」不变式 | 按 appr["task_id"] 解析归属 TaskState 后改走 emit()，或最低限度补 payload.agent_key |
| DEF-7 | 中 | `backend/src/runtime/sandbox_bash.py:88-89` | **bash 工具沙箱自愈是死代码**：`if sandbox_executor.get(user) is None: return "容器不可用…"` 前置守卫（1caff4c 时代遗留）恰好拦掉了唯一需要自愈的场景，run_id+cfg 永远到不了 _get_or_revive；6147fad 的回归测试直调 executor 层绕过守卫故未发现。重启/idle 回收后 bash 工具复现「用户容器不存在」原 bug，与 skill 路径（自愈可达）不对称 | 删掉/放宽 :88 守卫（仅 run_id/sandbox_cfg 缺失时 fail-closed），回归测试改走 run_container_command 工具入口 |
| DEF-8 | 低 | `backend/src/runtime/subagent_dispatch.py:178` | **dispatch >5 任务静默截断**：`tasks[:_MAX_BATCH]` 后无告知，预算拒绝文案报截断后数量（发 6 个报「本批 5」），额度足够时第 6 个任务无声丢弃。缓解：工具 schema 已声明「一次最多 5 个」 | 超限直接拒绝（报真实提交数），或截断后在返回文本首行明示 |

### 被推翻的 2 项判定（核实避免了误报）

| 原判定 | 推翻理由 |
|---|---|
| D 块崩溃遗留 delegation running 行永久占派发预算（原判高危） | 预算按 leader_task_id 隔离；产生遗留行的崩溃与该 leader 存活互斥（重启收敛只标 interrupted 不恢复协程，新任务 task_id 全新），遗留行占不到任何存活派发的名额。探针把遗留行手工插到存活 leader 名下，构造了现实中不可能的状态。降级为数据卫生问题：converge 顺带收敛 delegation 行 + deadline 列无消费方可删 |
| A 块 rename schema 120 / service 截 60 契约缝隙 | schema 行内注释与 service docstring 两处独立证明「传输层宽松校验 + service 归一化」是有意契约；响应/SSE/审计均返回归一化后的权威值，空白折叠本身也会使响应≠请求。至多是代码整洁偏好 |

---

## 五、文档回写项（Obsidian 19 号，本次只读未改）

1. 缺 `sre_agent_delegation`（D 块派发账本表）整表 DDL——core.sql L1065-1081 已有；
2. `sre_agent_run` 定义缺 `run_title` 列（run-title 迁移已收编 core.sql L331）；
3. 表数写「22→25」，实际 core.sql 为 **26** 张；
4. 「增量迁移 = migrate-2026-07-12-persistence.sql」引用已失效（该文件与 delegation 迁移已在 0488ce8/d75e37f 收编后删除，指引应改为「旧库重跑 openops_v1_core.sql」）。

---

## 六、覆盖缺口与建议（按优先级）

**① 修复 8 项确认缺陷并补回归**（DEF-1/2 优先——二者叠加构成审批门旁路的现实攻击面）。

**② 三大负向路径零覆盖**（21/28/31 号文档反复点名的验收断言）：
- `IDEMPOTENCY_KEY_CONFLICT`：同 key 不同请求体应 409——grep 全库无测试，且 idempotency.py 无请求体 hash 比对，疑似**连实现都缺**（21 号、31 号 INIT-006）；
- ASK 5 分钟超时终态全链路（decision=timeout→approval.timeout 审计→终态后 decide 返回 APPROVAL_TIMEOUT）：expire_stale_approvals 已接线 5 处但 test_ask 只测 approved/rejected/cancelled（31 号 ASK-004）；「ASK 超时即拒绝」(27 号) 与「审批等待不吃预算」(E 块) 的口径差异也未被任何用例裁决；
- `CONTEXT_LIMIT_EXCEEDED` 压缩兜底（31 号 MODEL-004）：E4 只验证了 tool_result_limit 数值透传；`OPENOPS_MAIN_TOOL_RESULT_LIMIT` env 路径缺 1000..200000 范围校验（模板路径有），D7 的 160000>窗口事故可经 env 复现。

**③ 断流取消桥补自动化**（本机可测而未测）：fire-and-forget 无强引用、GeneratorExit 兜底路径 get_running_loop、15s 感知滞后三点锁用例，防「停止按钮」无声回归。

**④ 随 07-13 Playwright 基建补前端交互用例**：E3 活动栏分组/徽标（叠加 DEF-6 实际渲染正确性成疑）、工具卡 args/result 展示、「/」技能菜单、导航四条路径、sidecar threadId 重写与身份头透传（当前 sidecar 零测试）。

**⑤ 事件脱敏**：760fc71 让工具 arguments 进持久化与前端后，payload_redacted_json 无实际脱敏实现；用户对话中提供的密码/token 会作为工具入参原样进审计表与工具卡（16/30.4 号明令禁止路径），test_security 未覆盖。

**⑥ 环境受限未测（如实标注，列入内网验收清单，非本次遗漏）**：真 Docker 安全基线（非 root/只读 rootfs/cap_drop/seccomp/idle TTL/容量 26 准入/自愈真实行为）；真 GLM 派发出工具卡+活动栏子组、真恢复审批 .recover 任务号；IAM 双步鉴权/Cookie 透传；真 96 注册表 reconcile（display_name 去重、改名悬挂行）与 SkillHub 真 ZIP（skill_key 键碰撞）。

---

## 七、测试资产

- 探针测试 3 个文件 20 用例（`test_probe_0712_p.py` / `test_probe_0712_de.py` / `test_probe_0712_a_misc.py`），已随报告附上——DEF-1..8 修复后可直接改造为回归用例入库；
- 快照 worktree 用毕已清理；主工作区、数据库 schema 均未做持久修改（测试库按 conftest 惯例被 TRUNCATE 重置）。
